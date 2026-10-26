"""
Products API (FastAPI)
======================
A thin HTTP layer over the Superbalist catalogue. It exposes products to the
frontend as JSON. It stores nothing itself: every request reads Postgres (via
catalogue_db.py) and, for an opened product, Superbalist live (via
superbalist_product.py).

Runs next to the try-on backend:
    gemini-ai-try-on (main.py) ->  http://localhost:8000
    this products API          ->  http://localhost:8001

SETUP
    pip install fastapi "uvicorn[standard]" psycopg2-binary requests python-dotenv
    Put DATABASE_URL in the project's .env (one folder up); catalogue_db.py loads it.
    For Cloud SQL, run the Cloud SQL Auth Proxy so the database is on localhost:5432.

RUN (from inside the superbalist/ folder)
    python -m uvicorn products_api:app --reload --port 8001
    # http://localhost:8001/docs  -> interactive API browser

ENDPOINTS
    GET /products                 grid: filters + search + sort + pagination
    GET /products/{id}            one product + on-demand detail
    GET /products/{id}/sizes      live sizes/stock only
    GET /products/{id}/image/{n}  the product's n-th photo (0 = first), served from here
    GET /brands                   brand names (list of strings)
    GET /brands/cards             brand name + product count + cover image
    GET /categories               categories, optionally scoped to gender/brand
    GET /health                   DB check
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

import catalogue_db as db
from superbalist_product import get_product_details, fetch_sizes

app = FastAPI(title="Superbalist Products API", version="0.2.0")

_ALLOWED = [
    o.strip()
    for o in os.environ.get(
        "FRONTEND_ORIGINS",
        "http://localhost:8080,http://localhost:5173,http://127.0.0.1:8080",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Row -> frontend shape
# ---------------------------------------------------------------------------
def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def _first_image(images):
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            return first.get("full") or first.get("thumb")
    return None


def _to_card(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "name": row.get("name"),
        "brand": row.get("brand"),
        "price": _num(row.get("price")),
        "wasPrice": _num(row.get("was_price")),
        "onSale": bool(row.get("on_sale")),
        "currency": row.get("currency") or "ZAR",
        "img": _first_image(row.get("images")),
        "retailer": "Superbalist",
        "category": row.get("category"),
        "subcategory": row.get("subcategory"),
        "gender": row.get("gender"),
        "colour": row.get("colour"),
        "url": row.get("url"),
        "fit": None,  # filled in later by the try-on backend
    }


# ---------------------------------------------------------------------------
# Grid / browse
# ---------------------------------------------------------------------------
@app.get("/products")
def list_products(
    gender: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    brand: Optional[str] = None,
    colour: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    q: Optional[str] = Query(None, description="search name or brand"),
    sort: str = "newest",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
):
    """The main grid. Every filter is optional and they all combine —
    including the search box."""
    filters = dict(
        gender=gender, category=category, subcategory=subcategory,
        brand=brand, colour=colour, min_price=min_price, max_price=max_price,
        q=(q or "").strip() or None,
    )
    rows = db.browse(**filters, sort=sort, page=page, page_size=page_size)
    total = db.count(**filters)
    return {
        "items": [_to_card(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }


# ---------------------------------------------------------------------------
# Single product (grid row + on-demand detail)
# ---------------------------------------------------------------------------
@app.get("/products/{product_id}")
def product_detail(
    product_id: int,
    detail: bool = Query(True, description="fetch live description/material/sizes/colours"),
):
    row = db.get_product(product_id)
    if not row:
        raise HTTPException(status_code=404, detail="product not found")

    out = _to_card(row)
    imgs = row.get("images")
    out["images"] = [i.get("full") for i in imgs if isinstance(i, dict) and i.get("full")] if isinstance(imgs, list) else []
    out.update({
        "description": None,
        "material": None,
        "sizes": [],
        "sizesDetail": [],
        "inStockSizes": [],
        "colourOptions": [],
        "modelFit": None,
        "tailorTips": [],
    })

    if detail and row.get("url"):
        d = get_product_details(product_id, product_url=row["url"], name=row.get("name"))
        out["description"] = d.get("description")
        out["material"] = d.get("material")
        out["modelFit"] = d.get("model_fit")
        out["colourOptions"] = d.get("colour_options") or []
        out["sizes"] = [s.get("size") for s in (d.get("sizes") or []) if s.get("size")]
        out["sizesDetail"] = d.get("sizes") or []
        out["inStockSizes"] = d.get("in_stock_sizes") or []
        if d.get("colour") and not out.get("colour"):
            out["colour"] = d["colour"]

    return out


# The try-on page needs a product photo as a file to upload. Superbalist's image server doesn't allow the
# browser to read its photos from another site, so they come through here. The address is looked up from
# the product's own row, never taken from the request, so this can't be pointed at any other site.
_IMAGE_MAX_BYTES = 7 * 1024 * 1024   # the try-on backend's limit per image


@app.get("/products/{product_id}/image/{n}")
def product_image(product_id: int, n: int):
    row = db.get_product(product_id)
    if not row:
        raise HTTPException(status_code=404, detail="product not found")
    imgs = row.get("images")
    urls = [i.get("full") for i in imgs if isinstance(i, dict) and i.get("full")] if isinstance(imgs, list) else []
    if not 0 <= n < len(urls):
        raise HTTPException(status_code=404, detail="image not found")
    try:
        r = requests.get(urls[n], timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=502, detail="Couldn't download the product photo.")
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if r.status_code != 200 or ctype not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=502, detail="Couldn't download the product photo.")
    if len(r.content) > _IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="The product photo is over 7 MB.")
    return Response(content=r.content, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/products/{product_id}/sizes")
def product_sizes(product_id: int):
    row = db.get_product(product_id)
    if not row:
        raise HTTPException(status_code=404, detail="product not found")
    sizes, status = fetch_sizes(product_id, product_url=row.get("url"))
    return {"id": str(product_id), "status": status, "sizes": sizes or []}


# ---------------------------------------------------------------------------
# Brands / categories
# ---------------------------------------------------------------------------
@app.get("/brands")
def brands(gender: Optional[str] = None):
    """Brand names only (used to resolve /stores/<slug> back to a brand)."""
    return {"brands": db.distinct_brands(gender)}


@app.get("/brands/cards")
def brand_cards(gender: Optional[str] = None):
    """For the Brands page: each brand with its product count and a cover
    image taken from one of its own products."""
    return {"brands": [
        {"name": r["name"], "count": r["count"], "img": r["img"]}
        for r in db.brand_cards(gender)
    ]}


@app.get("/categories")
def categories(gender: Optional[str] = None, brand: Optional[str] = None):
    return {"categories": db.distinct_categories(gender, brand)}


@app.get("/health")
def health():
    try:
        return {"ok": True, "products": db.count()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

"""
Phase 2 — Temporary products API (FastAPI)
==========================================
A thin HTTP layer over your existing Phase-1 code. It exposes the Superbalist
catalogue to the Lovable frontend as JSON, shaped to the frontend's needs.

It does NOT touch your colleague's try-on backend. Run them side by side:
    colleague's gemini-ai-try-on  ->  http://localhost:8000
    this products API             ->  http://localhost:8001

WHAT IT WRAPS (nothing is rewritten — these stay in Python, as planned):
    catalogue_db.py        -> grid / browse / filter / search / single row
    superbalist_product.py -> on-demand description, material, sizes, colours

SETUP
    pip install fastapi "uvicorn[standard]" psycopg2-binary requests
    # PowerShell:
    $env:DATABASE_URL = "postgresql://postgres:tryondev2026@localhost:5432/tryon"

RUN
    python -m uvicorn products_api:app --reload --port 8001
    # open http://localhost:8001/docs  for the interactive API browser

ENDPOINTS
    GET /products                 grid, with filters + pagination
    GET /products/{id}            one product, incl. on-demand detail
    GET /products/{id}/sizes      live sizes/stock only (fast)
    GET /brands                   distinct brands (the "stores" page)
    GET /categories               distinct categories (filter menu)
    GET /health                   quick DB check
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import catalogue_db as db
from superbalist_product import get_product_details, fetch_sizes

app = FastAPI(title="Superbalist Products API", version="0.1.0")

# The Vite dev server (localhost:8080) and Lovable preview call this directly.
# In production you'd narrow this; for local dev we allow the frontend origins.
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
# Row -> frontend Product shape
# ---------------------------------------------------------------------------
# Frontend `Product` type (src/lib/shop-store.ts):
#   { id, name, brand, price, img, retailer, category,
#     fit, description, sizes, materials, care, tailorTips }
# We fill what the catalogue knows; fit/tailorTips are try-on features that
# don't exist yet, so they're returned null/empty and the UI hides them.
def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def _first_image(images):
    """images is JSONB: a list of {full, thumb}. Return the best single URL."""
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            return first.get("full") or first.get("thumb")
    return None


def _to_card(row: dict) -> dict:
    """The lightweight shape used in grids / store pages / marketplace."""
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
        "gender": row.get("gender"),
        "colour": row.get("colour"),
        "url": row.get("url"),
        # try-on feature — not available until the AI backend scores a fit
        "fit": None,
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
    q: Optional[str] = Query(None, description="free-text name search"),
    sort: str = "newest",
    page: int = 1,
    page_size: int = Query(24, ge=1, le=100),
):
    """The main grid. Any filter left out is ignored.
    If `q` is given it runs a name search (other filters except gender ignored)."""
    if q:
        rows = db.search(q, gender=gender, page=page, page_size=page_size)
        items = [_to_card(r) for r in rows]
        # search() doesn't count; report page length so the UI can page by feel
        return {"items": items, "page": page, "page_size": page_size,
                "total": None, "query": q}

    rows = db.browse(
        gender=gender, category=category, subcategory=subcategory,
        brand=brand, colour=colour, min_price=min_price, max_price=max_price,
        sort=sort, page=page, page_size=page_size,
    )
    total = db.count(
        gender=gender, category=category, subcategory=subcategory,
        brand=brand, colour=colour, min_price=min_price, max_price=max_price,
    )
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
    """Product page payload. The base row is instant (from the DB); the
    description, material, colour options and live sizes are fetched on demand
    from Superbalist the moment the product is opened."""
    row = db.get_product(product_id)
    if not row:
        raise HTTPException(status_code=404, detail="product not found")

    out = _to_card(row)
    # carry the full image list for the product gallery
    imgs = row.get("images")
    out["images"] = [i.get("full") for i in imgs if isinstance(i, dict)] if isinstance(imgs, list) else []

    # defaults for the detail fields so the UI always has the keys
    out.update({
        "description": None,
        "material": None,
        "sizes": [],
        "inStockSizes": [],
        "colourOptions": [],
        "modelFit": None,
        # invented tailoring feature in the mock — no real data source yet
        "tailorTips": [],
    })

    if detail and row.get("url"):
        d = get_product_details(
            product_id, product_url=row["url"], name=row.get("name"),
        )
        out["description"] = d.get("description")
        out["material"] = d.get("material")
        out["modelFit"] = d.get("model_fit")
        out["colourOptions"] = d.get("colour_options") or []
        out["sizes"] = [s.get("size") for s in (d.get("sizes") or []) if s.get("size")]
        out["sizesDetail"] = d.get("sizes") or []        # size + live stock/price
        out["inStockSizes"] = d.get("in_stock_sizes") or []
        if d.get("colour") and not out.get("colour"):
            out["colour"] = d["colour"]

    return out


@app.get("/products/{product_id}/sizes")
def product_sizes(product_id: int):
    """Live sizes/stock only — cheap, for a size picker that refreshes."""
    row = db.get_product(product_id)
    if not row:
        raise HTTPException(status_code=404, detail="product not found")
    sizes, status = fetch_sizes(product_id, product_url=row.get("url"))
    return {"id": str(product_id), "status": status, "sizes": sizes or []}


# ---------------------------------------------------------------------------
# Filter menus / "stores" (brands)
# ---------------------------------------------------------------------------
@app.get("/brands")
def brands(gender: Optional[str] = None):
    """Distinct brands — this powers the Stores page (brands ARE the stores
    on Superbalist, since there's only one retailer)."""
    return {"brands": db.distinct_brands(gender)}


@app.get("/categories")
def categories(gender: Optional[str] = None):
    return {"categories": db.distinct_categories(gender)}


@app.get("/health")
def health():
    try:
        n = db.count()
        return {"ok": True, "products": n}
    except Exception as e:  # noqa: BLE001  — surface the DB error plainly in dev
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

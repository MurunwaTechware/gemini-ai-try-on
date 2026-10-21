"""
Superbalist — on-demand product detail fetcher
===============================================
Call this when a user OPENS a specific garment (the product / try-on view),
NOT in bulk. One product open = at most two lightweight requests, so you never
hit the rate limit that batch-fetching triggers.

Returns, for one product:
  - description (plain + HTML)
  - colour, material, model-fit info   (from the product page's `data` block)
  - colour_options  (the sibling colour products: id, colour, url, swatch image)
  - sizes + per-size live stock/price   (from the public SKU endpoint)

USAGE (from your app backend / API layer):

    from superbalist_product import get_product_details

    # product_url comes from the catalogue entry's "url" field
    details = get_product_details(
        1764584,
        product_url="https://superbalist.com/men/tops/t-shirts-vests/regular-fit-polo-shirt-light-blue-1330413005/1764584",
    )

Pair this with superbalist_catalogue.py: the catalogue builds the grid
(name, brand, price, images, colour); this fills in everything else the
moment a user actually looks at one item.
"""

import re
import json
import time
import requests

CDN = "https://assets.superbalistcdn.co.za"
BASE_SITE = "https://superbalist.com"
SKU_URL = "https://superbalist.com/api/public/products/{pid}/skus"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Reuse the connection across calls (faster as a user browses).
_session = requests.Session()


# ---------------------------------------------------------------------------
# Nuxt payload helpers
# ---------------------------------------------------------------------------
def _hydrate_factory(P):
    cache = {}

    def hydrate(index):
        if isinstance(index, int) and index < 0:
            return None
        if index in cache:
            return cache[index]
        value = P[index]
        if value is None or not isinstance(value, (list, dict)):
            cache[index] = value
            return value
        if isinstance(value, list):
            if len(value) > 0 and isinstance(value[0], str):
                out = [value[0]] + [hydrate(x) for x in value[1:]]
                cache[index] = out
                return out
            out = []
            cache[index] = out
            for n in value:
                out.append(hydrate(n))
            return out
        out = {}
        cache[index] = out
        for k, v in value.items():
            out[k] = hydrate(v)
        return out

    return hydrate


def _clean_cousin_colour(name):
    """'Regular fit polo shirt - navy blue - 1330413004' -> 'navy blue'."""
    if not name:
        return None
    name = re.sub(r'\s*-\s*\d+\s*$', '', name)   # drop trailing supplier code
    return name.rsplit(" - ", 1)[1].strip() if " - " in name else None


def _img(img_path, hires=False):
    if not img_path:
        return None
    if hires:
        return f"{CDN}{img_path}"
    return f"{CDN}/fit-in/500x720/filters:quality(85):format(jpg){img_path}"


def _parse_detail_page(html):
    """Extract description, attributes and colour options from a product page."""
    m = re.search(r'id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return {}
    P = json.loads(m.group(1))
    hydrate = _hydrate_factory(P)

    detail = None
    cousins = None
    for i, raw in enumerate(P):
        if isinstance(raw, dict):
            if detail is None and "text_description" in raw and "data" in raw and "designer_name" in raw:
                detail = hydrate(i)
            if cousins is None and "cousins" in raw:
                cousins = hydrate(i).get("cousins")
        if detail is not None and cousins is not None:
            break

    if detail is None:
        return {}

    data = detail.get("data") or {}
    colour_options = []
    for c in (cousins or []):
        colour_options.append({
            "id":     c.get("id"),
            "colour": _clean_cousin_colour(c.get("name")),
            "path":   c.get("path"),
            "url":    f"{BASE_SITE}{c.get('path')}" if c.get("path") else None,
            "image":  _img(c.get("img_path")),
            "image_hires": _img(c.get("img_path"), hires=True),
        })

    return {
        "colour":           data.get("Colour"),
        "material":         data.get("Material"),
        "model_fit":        {"wears": data.get("Model wears"), "height": data.get("Model Height")},
        "description":      detail.get("text_description"),
        "description_html": detail.get("description"),
        "attributes":       data,
        "colour_options":   colour_options,
    }


# ---------------------------------------------------------------------------
# SKU endpoint (sizes + live stock)
# ---------------------------------------------------------------------------
def fetch_sizes(product_id, product_url=None, retries=1, timeout=12):
    """Live sizes/stock for ONE product. Returns (sizes_list_or_None, status)."""
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json",
        "Referer": product_url or f"{BASE_SITE}/",
    }
    url = SKU_URL.format(pid=product_id)
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                if attempt < retries:
                    time.sleep(1.5)
                    continue
                return None, "failed"
            resp.raise_for_status()
            skus = resp.json()
        except (requests.exceptions.RequestException, ValueError):
            if attempt < retries:
                time.sleep(1.0)
                continue
            return None, "failed"

        sizes = []
        for sku in skus:
            label = (sku.get("fields") or {}).get("Size") or sku.get("label") or sku.get("value")
            try:
                price = float(sku.get("reduced_price") or sku.get("price"))
            except (TypeError, ValueError):
                price = None
            sizes.append({
                "size":      label,
                "available": sku.get("available", False),
                "stock":     sku.get("quantity"),
                "price":     price,
                "barcode":   sku.get("barcode"),
            })
        return sizes, ("ok" if sizes else "empty")

    return None, "failed"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def get_product_details(product_id, product_url, name=None,
                        with_sizes=True, with_page=True, timeout=15):
    """Fetch full on-demand detail for ONE product.

    Args:
        product_id  : Superbalist product id.
        product_url : the product page URL (from the catalogue entry's "url").
        name        : optional fallback name (used if the page can't be parsed).
        with_sizes  : also fetch live sizes/stock from the SKU endpoint.
        with_page   : also fetch the product page for description + colour options.

    Returns a merged dict:
        id, name, brand, colour, material, model_fit, description,
        description_html, attributes, colour_options, sizes, in_stock_sizes,
        page_status, sizes_status
    """
    result = {
        "id": product_id,
        "name": name,
        "brand": None,
        "colour": None,
        "material": None,
        "model_fit": None,
        "description": None,
        "description_html": None,
        "attributes": {},
        "colour_options": [],
        "sizes": [],
        "in_stock_sizes": [],
        "page_status": "skipped",
        "sizes_status": "skipped",
    }

    # 1) product page -> description, colour, material, colour options
    if with_page and product_url:
        try:
            resp = _session.get(
                product_url,
                headers={"User-Agent": _UA,
                         "Accept": "text/html,application/xhtml+xml"},
                timeout=timeout,
            )
            resp.raise_for_status()
            parsed = _parse_detail_page(resp.text)
            if parsed:
                result.update({k: v for k, v in parsed.items()})
                result["page_status"] = "ok"
            else:
                result["page_status"] = "empty"
        except requests.exceptions.RequestException:
            result["page_status"] = "failed"

    # 2) SKU endpoint -> live sizes/stock
    if with_sizes:
        sizes, status = fetch_sizes(product_id, product_url)
        result["sizes_status"] = status
        if sizes:
            result["sizes"] = sizes
            result["in_stock_sizes"] = [s["size"] for s in sizes if s.get("available")]

    return result


# Quick manual test:  python superbalist_product.py
if __name__ == "__main__":
    url = ("https://superbalist.com/men/tops/t-shirts-vests/"
           "regular-fit-polo-shirt-light-blue-1330413005/1764584")
    d = get_product_details(1764584, product_url=url,
                            name="Regular fit polo shirt - light blue")
    # trim images list noise for printing
    print(json.dumps(d, indent=2, ensure_ascii=False))

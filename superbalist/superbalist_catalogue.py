"""
Superbalist Catalogue Builder  (FREE — no Apify, no API token)
==============================================================
Pulls garment data straight from Superbalist's server-rendered pages and its
public SKU endpoint. Zero cost.

HOW IT WORKS
    1. Fetch each browse/category page (plain HTTP GET).
    2. Extract the embedded Nuxt payload (__NUXT_DATA__) and reconstruct the
       products from its devalue (index-referenced) format.
    3. Normalise each product (name, brand, price, images, category, colour).
    4. Enrich with real sizes + live stock from the public SKU endpoint.
    5. Save a clean catalogue to superbalist_catalogue.json.

SETUP
    pip install requests

RUN
    python superbalist_catalogue.py
"""

import re
import json
import time
import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
BASE_SITE = "https://superbalist.com"

# Category pages to pull. Add any /browse/<gender>/<group>/<subcategory> path.
# Each page returns ~70 products for free. Add "?page=2" etc. for more depth.
TARGET_URLS = [
    "https://superbalist.com/browse/men/tops/t-shirts-vests",
    "https://superbalist.com/browse/men/tops/shirts",
    "https://superbalist.com/browse/women/tops/t-shirts-vests-camis",
]

FETCH_SIZES = False         # Catalogue = listing only (free, no rate limits).
                            # Sizes/stock are fetched ON DEMAND per product at
                            # try-on time — see superbalist_product.py.
                            # (Set True only for a one-off bulk export, and expect
                            #  rate-limiting past ~50 products.)
SKU_DELAY = 0.6             # base pause between SKU calls (seconds)
SKU_MAX_RETRIES = 4         # retries per product on rate-limit / error
SKU_BACKOFF = 2.0          # backoff base: waits 2s, 4s, 8s, ... between retries
OUTPUT = "superbalist_catalogue.json"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ---------------------------------------------------------------------------
# NUXT PAYLOAD PARSING (devalue format — values referenced by array index)
# ---------------------------------------------------------------------------
def extract_products(html):
    """Pull the product objects out of a browse page's embedded Nuxt payload."""
    m = re.search(r'id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return []
    P = json.loads(m.group(1))

    hydrated = {}

    def hydrate(index):
        # negative indices are devalue specials (undefined/hole/NaN/...)
        if isinstance(index, int) and index < 0:
            return None
        if index in hydrated:
            return hydrated[index]
        value = P[index]
        if value is None or not isinstance(value, (list, dict)):
            hydrated[index] = value
            return value
        if isinstance(value, list):
            # a string first element = type-tagged value e.g. ["SuperbalistAsset", 411]
            if len(value) > 0 and isinstance(value[0], str):
                out = [value[0]] + [hydrate(x) for x in value[1:]]
                hydrated[index] = out
                return out
            out = []
            hydrated[index] = out
            for n in value:
                out.append(hydrate(n))
            return out
        out = {}
        hydrated[index] = out
        for k, v in value.items():
            out[k] = hydrate(v)
        return out

    products, seen = [], set()
    for i, node in enumerate(P):
        if (isinstance(node, dict) and "designer_name" in node
                and "short_name" in node and "price_range" in node):
            prod = hydrate(i)
            pid = prod.get("id")
            if pid not in seen:
                seen.add(pid)
                products.append(prod)
    return products


# ---------------------------------------------------------------------------
# NORMALISER (unchanged field logic — works on both Nuxt and Apify shapes)
# ---------------------------------------------------------------------------
IMG_THUMB_DIMS = "500x720"
IMG_FILTERS = "filters:quality(85):format(jpg)"


def build_image_urls(item):
    imgs = []
    for asset in (item.get("assets") or []):
        try:
            host = asset[1][0]
            props = asset[1][1]
            base = props.get("base_ssl_url")
            if host and base:
                imgs.append({
                    "full":  f"https://{host}/{base}",
                    "thumb": f"https://{host}/fit-in/{IMG_THUMB_DIMS}/{IMG_FILTERS}/{base}",
                })
        except (IndexError, KeyError, TypeError):
            continue
    return imgs


def parse_colour(name):
    if not name or " - " not in name:
        return None
    return name.rsplit(" - ", 1)[1].strip()


def parse_category(path):
    if not path:
        return {"gender": None, "category": None, "subcategory": None}
    parts = [p for p in path.split("/") if p]
    return {
        "gender":      parts[0] if len(parts) > 0 else None,
        "category":    parts[1] if len(parts) > 1 else None,
        "subcategory": parts[2] if len(parts) > 2 else None,
    }


def extract_price(item):
    pr = item.get("price_range") or {}
    has_discount = pr.get("has_discount", False)

    def price_of(node):
        if isinstance(node, dict):
            try:
                return float(node.get("price"))
            except (TypeError, ValueError):
                return None
        return None

    current = price_of(pr.get("min")) or price_of(pr.get("max"))
    was = price_of(pr.get("max")) if has_discount else None
    return current, was, has_discount


def normalise(item):
    cat = parse_category(item.get("path"))
    price, was, on_sale = extract_price(item)
    path = item.get("path")
    return {
        "id":            item.get("id"),
        "name":          item.get("short_name"),
        "brand":         item.get("designer_name"),
        "price":         price,
        "was_price":     was,
        "currency":      "ZAR",
        "on_sale":       item.get("is_on_sale", on_sale),
        "colour":        parse_colour(item.get("short_name")),
        "gender":        cat["gender"],
        "category":      cat["category"],
        "subcategory":   cat["subcategory"],
        "variant_label": item.get("variations_label"),
        "sizes":         None,   # filled below if FETCH_SIZES
        "images":        build_image_urls(item),
        "url":           f"{BASE_SITE}{path}" if path else None,
    }


# ---------------------------------------------------------------------------
# SIZES ENRICHMENT (public SKU endpoint — free)
# ---------------------------------------------------------------------------
SKU_URL = "https://superbalist.com/api/public/products/{pid}/skus"


def fetch_sizes(product_id, session, product_url=None):
    """Fetch sizes/stock for one product, with retries + backoff on rate limits.

    Returns (sizes, status):
      sizes  = list of size dicts (possibly empty if the product genuinely
               has no SKUs), or None if every attempt failed (unknown).
      status = "ok" | "empty" | "failed"
    """
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "application/json",
        # A plausible Referer helps avoid being treated as a bot.
        "Referer": product_url or "https://superbalist.com/",
    }
    url = SKU_URL.format(pid=product_id)

    for attempt in range(SKU_MAX_RETRIES):
        try:
            resp = session.get(url, headers=headers, timeout=15)
            # Rate limited or server error -> back off and retry
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = SKU_BACKOFF * (2 ** attempt)
                print(f"      rate-limited (HTTP {resp.status_code}); waiting {wait:.0f}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            skus = resp.json()
        except (requests.exceptions.RequestException, ValueError):
            wait = SKU_BACKOFF * (2 ** attempt)
            time.sleep(wait)
            continue

        # Success — parse the sizes
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

    # All retries exhausted
    return None, "failed"


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    sess = requests.Session()
    all_clean = []
    seen_ids = set()

    for url in TARGET_URLS:
        print(f"\nFetching listing: {url}")
        try:
            resp = sess.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"  failed: {e}")
            continue

        products = extract_products(resp.text)
        print(f"  {len(products)} products found")
        for p in products:
            if p.get("id") in seen_ids:
                continue
            seen_ids.add(p.get("id"))
            all_clean.append(normalise(p))

    print(f"\nTotal unique products: {len(all_clean)}")

    if FETCH_SIZES:
        print("\nFetching sizes/stock per product (public SKU endpoint)...")
        failures = 0
        consecutive_fail = 0
        for i, entry in enumerate(all_clean, 1):
            pid = entry.get("id")
            if pid:
                sizes, status = fetch_sizes(pid, sess, entry.get("url"))
                entry["sizes"] = sizes   # None when the fetch failed (unknown)
                name = (entry.get("name") or "")[:40]
                if status == "failed":
                    failures += 1
                    consecutive_fail += 1
                    print(f"  [{i}/{len(all_clean)}] {name:<40} FAILED (sizes unknown)")
                    # If the server is clearly pushing back, cool off before continuing
                    if consecutive_fail >= 3:
                        print("      multiple failures in a row — cooling off 30s...")
                        time.sleep(30)
                        consecutive_fail = 0
                else:
                    consecutive_fail = 0
                    n = len(sizes)
                    in_stock = sum(1 for s in sizes if s.get("available"))
                    print(f"  [{i}/{len(all_clean)}] {name:<40} {n} sizes, {in_stock} in stock")
            time.sleep(SKU_DELAY)

        if failures:
            print(f"\n  NOTE: {failures} product(s) have sizes=null (fetch failed, not truly empty).")
            print("  Re-run to retry just those, or raise SKU_DELAY if it keeps happening.")

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(all_clean, f, indent=2, ensure_ascii=False)
    print(f"\nSaved catalogue to {OUTPUT}  ({len(all_clean)} garments)")


if __name__ == "__main__":
    main()

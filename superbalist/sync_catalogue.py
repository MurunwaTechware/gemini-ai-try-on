"""
Nightly catalogue sync job (Postgres)
=====================================
Walks every clothing category page on Superbalist, parses each grid page
(free, server-rendered), and UPSERTS the products into the `products` table.

WHAT CHANGED (v2)
    - Pages through each category UNTIL IT RUNS OUT (was: only 3 pages), so a
      brand like Factorie now gets all its products, not just the few that
      happened to appear on the first 3 pages.
    - The category list now comes from category_urls.py (written by
      discover_categories.py), so there's one list to maintain.
    - Retries politely on HTTP 429 (rate limit) instead of skipping the rest
      of the category.

SETUP
    pip install requests psycopg2-binary
    $env:DATABASE_URL = "postgresql://postgres:<password>@localhost:5432/tryon"

RUN (from inside the superbalist/ folder)
    python sync_catalogue.py
    # first full run takes a while (tens of minutes) — that's expected

Reuses the parser from superbalist_catalogue.py (extract_products + normalise).
"""

import os
import sys
import time
import requests
import psycopg2
from psycopg2.extras import execute_values, Json

from superbalist_catalogue import extract_products, normalise

try:
    from category_urls import CATEGORY_URLS
except ImportError:
    sys.exit("category_urls.py not found — run:  python discover_categories.py  first.")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/tryon")

MAX_PAGES = 60          # safety cap per category; the loop stops earlier when a page is empty
PAGE_DELAY = 1.2        # polite pause between page fetches (seconds)
RETRY_429 = 3           # how many times to retry a rate-limited page
BACKOFF_429 = 20        # seconds to wait after a 429 (doubles each retry)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

UPSERT_SQL = """
INSERT INTO products
    (id, name, brand, price, was_price, currency, on_sale,
     gender, category, subcategory, colour, url, images, last_synced)
VALUES %s
ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    brand       = EXCLUDED.brand,
    price       = EXCLUDED.price,
    was_price   = EXCLUDED.was_price,
    currency    = EXCLUDED.currency,
    on_sale     = EXCLUDED.on_sale,
    gender      = EXCLUDED.gender,
    category    = EXCLUDED.category,
    subcategory = EXCLUDED.subcategory,
    colour      = EXCLUDED.colour,
    url         = EXCLUDED.url,
    images      = EXCLUDED.images,
    last_synced = now();
"""
VALUES_TEMPLATE = "(" + ",".join(["%s"] * 13) + ", now())"


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------
def fetch_page(session, page_url):
    """GET one grid page. Retries on 429 with backoff. Returns HTML or None."""
    wait = BACKOFF_429
    for attempt in range(RETRY_429 + 1):
        try:
            resp = session.get(page_url, headers=HEADERS, timeout=25)
        except requests.exceptions.RequestException as e:
            print(f"    network error: {e}")
            return None
        if resp.status_code == 429 and attempt < RETRY_429:
            print(f"    rate-limited — waiting {wait}s and retrying")
            time.sleep(wait)
            wait *= 2
            continue
        if resp.status_code >= 400:
            print(f"    HTTP {resp.status_code} — skipping rest of category")
            return None
        return resp.text
    return None


def fetch_category(url, session):
    """Pull every product in one category, page by page, until a page is empty."""
    out, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        page_url = url if page == 1 else f"{url}?page={page}"
        html = fetch_page(session, page_url)
        if html is None:
            break
        products = extract_products(html)
        if not products:
            break                                   # ran out of pages
        new = [normalise(p) for p in products]
        fresh = [e for e in new if e.get("id") not in seen]
        if not fresh:
            break                                   # site repeated the last page
        for e in fresh:
            seen.add(e.get("id"))
        out.extend(fresh)
        print(f"    page {page}: {len(fresh)} products")
        time.sleep(PAGE_DELAY)
    return out


def row_tuple(entry):
    return (
        entry.get("id"),
        entry.get("name"),
        entry.get("brand"),
        entry.get("price"),
        entry.get("was_price"),
        entry.get("currency") or "ZAR",
        bool(entry.get("on_sale")),
        entry.get("gender"),
        entry.get("category"),
        entry.get("subcategory"),
        entry.get("colour"),
        entry.get("url"),
        Json(entry.get("images") or []),
    )


def upsert(conn, rows):
    with conn, conn.cursor() as cur:
        execute_values(cur, UPSERT_SQL, rows, template=VALUES_TEMPLATE, page_size=200)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    started = time.time()
    session = requests.Session()
    conn = psycopg2.connect(DATABASE_URL)
    total_written = 0
    try:
        for i, url in enumerate(CATEGORY_URLS, 1):
            print(f"\n[{i}/{len(CATEGORY_URLS)}] {url}")
            entries = [e for e in fetch_category(url, session) if e.get("id") is not None]
            if entries:
                # write per category, so a crash halfway keeps what's done
                upsert(conn, [row_tuple(e) for e in entries])
                total_written += len(entries)
                print(f"    saved {len(entries)}")

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM products;")
            total = cur.fetchone()[0]
        mins = (time.time() - started) / 60
        print(f"\nDone in {mins:.1f} min. Upserted {total_written} rows; "
              f"products table now holds {total}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

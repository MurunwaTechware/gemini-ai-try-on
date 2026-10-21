"""
Phase 1 — Nightly catalogue sync job (Postgres)
===============================================
Walks the category registry, parses each grid page (free, server-rendered),
and UPSERTS the products into the `products` table. No per-product SKU calls,
so no rate limits. Run nightly via cron / Task Scheduler.

SETUP
    pip install requests psycopg2-binary
    psql "$DATABASE_URL" -f schema.sql          # once, to create the table
    export DATABASE_URL="postgresql://user:pass@host:5432/dbname"

RUN
    python sync_catalogue.py

Reuses the validated parser from superbalist_catalogue.py (extract_products +
normalise), so the two never drift apart — keep that file alongside this one.
"""

import os
import json
import time
import requests
import psycopg2
from psycopg2.extras import execute_values, Json

from superbalist_catalogue import extract_products, normalise

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/tryon")

# Category registry — the pages the sync walks. Add any /browse/... path.
# gender/category/subcategory are derived per-product from each path, so this
# list only needs the URLs.
CATEGORY_URLS = [
    # men (15)
    "https://superbalist.com/browse/men/formalwear/formal-pants",
    "https://superbalist.com/browse/men/formalwear/formal-shirts",
    "https://superbalist.com/browse/men/formalwear/jackets-blazers",
    "https://superbalist.com/browse/men/jackets-coats/jackets",
    "https://superbalist.com/browse/men/jeans-pants-shorts/jeans",
    "https://superbalist.com/browse/men/jeans-pants-shorts/pants-chinos",
    "https://superbalist.com/browse/men/jeans-pants-shorts/shorts",
    "https://superbalist.com/browse/men/sport/hoodies-sweats-jackets",
    "https://superbalist.com/browse/men/sport/sweatpants-shorts",
    "https://superbalist.com/browse/men/sport/t-shirts",
    "https://superbalist.com/browse/men/swimwear/swimwear",
    "https://superbalist.com/browse/men/tops/hoodies-sweats",
    "https://superbalist.com/browse/men/tops/knitwear",
    "https://superbalist.com/browse/men/tops/shirts",
    "https://superbalist.com/browse/men/tops/t-shirts-vests",
    # women (33)
    "https://superbalist.com/browse/women/bottoms/jeans",
    "https://superbalist.com/browse/women/bottoms/shorts",
    "https://superbalist.com/browse/women/bottoms/skirts",
    "https://superbalist.com/browse/women/bottoms/trousers",
    "https://superbalist.com/browse/women/dresses/casual",
    "https://superbalist.com/browse/women/dresses/formal",
    "https://superbalist.com/browse/women/dresses/jumpsuits-playsuits",
    "https://superbalist.com/browse/women/dresses/occasion",
    "https://superbalist.com/browse/women/jackets-coats/coats",
    "https://superbalist.com/browse/women/jackets-coats/jackets",
    "https://superbalist.com/browse/women/maternity/bottoms",
    "https://superbalist.com/browse/women/maternity/dresses-jumpsuits",
    "https://superbalist.com/browse/women/maternity/jackets-coats",
    "https://superbalist.com/browse/women/maternity/tops",
    "https://superbalist.com/browse/women/plus-size/bottoms-skirts",
    "https://superbalist.com/browse/women/plus-size/dresses",
    "https://superbalist.com/browse/women/plus-size/jackets-coats",
    "https://superbalist.com/browse/women/plus-size/jeans",
    "https://superbalist.com/browse/women/plus-size/sleepwear",
    "https://superbalist.com/browse/women/plus-size/swimwear",
    "https://superbalist.com/browse/women/plus-size/tops",
    "https://superbalist.com/browse/women/sport/bottoms",
    "https://superbalist.com/browse/women/sport/hoodies-sweats-jackets",
    "https://superbalist.com/browse/women/sport/plus-size",
    "https://superbalist.com/browse/women/sport/t-shirts",
    "https://superbalist.com/browse/women/swimwear/bikinis",
    "https://superbalist.com/browse/women/swimwear/kaftans-cover-ups",
    "https://superbalist.com/browse/women/swimwear/one-piece",
    "https://superbalist.com/browse/women/tops/blouses",
    "https://superbalist.com/browse/women/tops/hoodies-sweats",
    "https://superbalist.com/browse/women/tops/knitwear",
    "https://superbalist.com/browse/women/tops/shirts",
    "https://superbalist.com/browse/women/tops/t-shirts-vests-camis",
    # kids (41)
    "https://superbalist.com/browse/kids/baby-boys-0-2/babygrows-sleepsuits",
    "https://superbalist.com/browse/kids/baby-boys-0-2/jackets-knitwear",
    "https://superbalist.com/browse/kids/baby-boys-0-2/pants-jeans",
    "https://superbalist.com/browse/kids/baby-boys-0-2/sets",
    "https://superbalist.com/browse/kids/baby-boys-0-2/shorts",
    "https://superbalist.com/browse/kids/baby-boys-0-2/swimwear",
    "https://superbalist.com/browse/kids/baby-boys-0-2/tops",
    "https://superbalist.com/browse/kids/baby-girls-0-2/babygrows-sleepsuits",
    "https://superbalist.com/browse/kids/baby-girls-0-2/dresses-skirts",
    "https://superbalist.com/browse/kids/baby-girls-0-2/jackets-knitwear",
    "https://superbalist.com/browse/kids/baby-girls-0-2/pants-jeans",
    "https://superbalist.com/browse/kids/baby-girls-0-2/sets",
    "https://superbalist.com/browse/kids/baby-girls-0-2/shorts",
    "https://superbalist.com/browse/kids/baby-girls-0-2/swimwear",
    "https://superbalist.com/browse/kids/baby-girls-0-2/tops",
    "https://superbalist.com/browse/kids/boys-2-8/jackets-knitwear",
    "https://superbalist.com/browse/kids/boys-2-8/pants-jeans",
    "https://superbalist.com/browse/kids/boys-2-8/sets",
    "https://superbalist.com/browse/kids/boys-2-8/shorts",
    "https://superbalist.com/browse/kids/boys-2-8/swimwear",
    "https://superbalist.com/browse/kids/boys-2-8/tops",
    "https://superbalist.com/browse/kids/boys-8-16/jackets-knitwear",
    "https://superbalist.com/browse/kids/boys-8-16/pants-jeans",
    "https://superbalist.com/browse/kids/boys-8-16/sets",
    "https://superbalist.com/browse/kids/boys-8-16/shorts",
    "https://superbalist.com/browse/kids/boys-8-16/swimwear",
    "https://superbalist.com/browse/kids/boys-8-16/tops",
    "https://superbalist.com/browse/kids/girls-2-8/dresses-skirts",
    "https://superbalist.com/browse/kids/girls-2-8/jackets-knitwear",
    "https://superbalist.com/browse/kids/girls-2-8/pants-jeans",
    "https://superbalist.com/browse/kids/girls-2-8/sets",
    "https://superbalist.com/browse/kids/girls-2-8/shorts",
    "https://superbalist.com/browse/kids/girls-2-8/swimwear",
    "https://superbalist.com/browse/kids/girls-2-8/tops",
    "https://superbalist.com/browse/kids/girls-8-16/dresses-skirts",
    "https://superbalist.com/browse/kids/girls-8-16/jackets-knitwear",
    "https://superbalist.com/browse/kids/girls-8-16/pants-jeans",
    "https://superbalist.com/browse/kids/girls-8-16/sets",
    "https://superbalist.com/browse/kids/girls-8-16/shorts",
    "https://superbalist.com/browse/kids/girls-8-16/swimwear",
    "https://superbalist.com/browse/kids/girls-8-16/tops",
    # ... extend freely
]

MAX_PAGES = 3          # pages to pull per category (each ~72 products)
PAGE_DELAY = 1.0       # polite pause between page fetches (seconds)

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
# Each VALUES row supplies 13 fields; last_synced is set by now() in the template.
VALUES_TEMPLATE = "(" + ",".join(["%s"] * 13) + ", now())"


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------
def fetch_category(url, session):
    """Pull every product across MAX_PAGES of one category. Returns clean rows."""
    out = []
    for page in range(1, MAX_PAGES + 1):
        page_url = url if page == 1 else f"{url}?page={page}"
        try:
            resp = session.get(page_url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"    page {page} failed: {e}")
            break

        products = extract_products(resp.text)
        if not products:
            break   # no more pages
        out.extend(normalise(p) for p in products)
        print(f"    page {page}: {len(products)} products")
        time.sleep(PAGE_DELAY)
    return out


def row_tuple(entry):
    """Map a normalised product dict to the column order in UPSERT_SQL."""
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
        Json(entry.get("images") or []),   # list[{full,thumb}] -> JSONB
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    session = requests.Session()
    all_rows = {}    # id -> row dict (dedupe across categories)

    for url in CATEGORY_URLS:
        print(f"\nSyncing {url}")
        for entry in fetch_category(url, session):
            pid = entry.get("id")
            if pid is not None:
                all_rows[pid] = entry

    rows = [row_tuple(e) for e in all_rows.values()]
    print(f"\nTotal unique products to upsert: {len(rows)}")
    if not rows:
        print("Nothing to write. Check the category URLs.")
        return

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn, conn.cursor() as cur:
            execute_values(cur, UPSERT_SQL, rows, template=VALUES_TEMPLATE, page_size=200)
        # conn commits on exiting the `with conn` block
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM products;")
            total = cur.fetchone()[0]
        print(f"Upsert complete. products table now holds {total} rows.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

"""
Catalogue data-access layer (Postgres)
======================================
Clean Python functions the API calls instead of writing SQL inline.
All the grid/browse/filter/search/paginate/single-product queries live here.

SETUP
    pip install psycopg2-binary python-dotenv
    Set DATABASE_URL="postgresql://user:pass@host:5432/dbname" in the project's .env (one folder up),
    or in the environment, which wins over .env.

USAGE
    import catalogue_db as db
    page  = db.browse(gender="women", category="dresses", q="linen", page=1)
    item  = db.get_product(1764584)
    cards = db.brand_cards()

FILTER RULES (what each filter actually matches)
    q        -> name OR brand contains the text   (case-insensitive)
    colour   -> colour contains the text          (case-insensitive, so "black"
                matches "Black" and "black & dark blue patterned")
    brand    -> exact brand name
    gender / category / subcategory -> exact
    min_price / max_price -> current (possibly reduced) price
"""

import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv(Path(__file__).resolve().parent.parent / ".env")   # the project's .env; never overrides real env vars
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/tryon")
PAGE_SIZE = 24

# Allowed sort options -> SQL ORDER BY (whitelist; never interpolate user input)
_SORTS = {
    "newest":     "last_synced DESC, id DESC",
    "price_asc":  "price ASC NULLS LAST, id",
    "price_desc": "price DESC NULLS LAST, id",
}


def _conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def _run(sql, params=None):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()


def _escape_like(text):
    """Make user text safe inside LIKE: % and _ become literal characters."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where(gender=None, category=None, subcategory=None, brand=None,
           colour=None, min_price=None, max_price=None, q=None):
    """Build one WHERE clause shared by browse() and count(), so the page and
    its total can never disagree."""
    where, params = [], []

    if gender:
        where.append("gender = %s"); params.append(gender)
    if category:
        where.append("category = %s"); params.append(category)
    if subcategory:
        where.append("subcategory = %s"); params.append(subcategory)
    if brand:
        where.append("brand = %s"); params.append(brand)
    if colour:
        where.append("colour ILIKE %s"); params.append(f"%{_escape_like(colour)}%")
    if min_price is not None:
        where.append("price >= %s"); params.append(min_price)
    if max_price is not None:
        where.append("price <= %s"); params.append(max_price)
    if q:
        term = f"%{_escape_like(q.strip())}%"
        where.append("(name ILIKE %s OR brand ILIKE %s)"); params.extend([term, term])

    return ("WHERE " + " AND ".join(where)) if where else "", params


# ---------------------------------------------------------------------------
# Grid / browse (filters + search + sort + pagination, all combined)
# ---------------------------------------------------------------------------
def browse(gender=None, category=None, subcategory=None, brand=None,
           colour=None, min_price=None, max_price=None, q=None,
           sort="newest", page=1, page_size=PAGE_SIZE):
    """The main grid query. Any filter left as None is ignored.
    Search (q) now works TOGETHER with the other filters, so searching on a
    brand page stays inside that brand."""
    where_sql, params = _where(gender, category, subcategory, brand,
                               colour, min_price, max_price, q)
    order_sql = _SORTS.get(sort, _SORTS["newest"])
    offset = (max(page, 1) - 1) * page_size

    sql = f"""
        SELECT id, name, brand, price, was_price, on_sale, currency, colour,
               gender, category, subcategory, url, images
        FROM products
        {where_sql}
        ORDER BY {order_sql}
        LIMIT %s OFFSET %s;
    """
    return _run(sql, params + [page_size, offset])


def count(gender=None, category=None, subcategory=None, brand=None,
          colour=None, min_price=None, max_price=None, q=None):
    """Total matches for a filter set — used for page counts."""
    where_sql, params = _where(gender, category, subcategory, brand,
                               colour, min_price, max_price, q)
    rows = _run(f"SELECT count(*) AS n FROM products {where_sql};", params)
    return rows[0]["n"]


def search(term, gender=None, page=1, page_size=PAGE_SIZE):
    """Kept for backwards compatibility — browse(q=...) is preferred."""
    return browse(gender=gender, q=term, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# Single product (the product page's base row)
# ---------------------------------------------------------------------------
def get_product(product_id):
    rows = _run("SELECT * FROM products WHERE id = %s;", [product_id])
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Filter-menu helpers
# ---------------------------------------------------------------------------
def distinct_brands(gender=None):
    if gender:
        rows = _run("SELECT DISTINCT brand FROM products WHERE gender = %s AND brand IS NOT NULL ORDER BY brand;", [gender])
    else:
        rows = _run("SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL ORDER BY brand;")
    return [r["brand"] for r in rows]


def distinct_categories(gender=None, brand=None):
    """Categories that actually have products in this scope — so a brand page
    only offers categories that brand sells."""
    where, params = ["category IS NOT NULL"], []
    if gender:
        where.append("gender = %s"); params.append(gender)
    if brand:
        where.append("brand = %s"); params.append(brand)
    rows = _run(f"SELECT DISTINCT category FROM products WHERE {' AND '.join(where)} ORDER BY category;", params)
    return [r["category"] for r in rows]


def brand_cards(gender=None):
    """One row per brand for the Brands page: name, how many products we hold,
    and a cover image taken from that brand's most recently synced product."""
    gender_sql = "AND gender = %s" if gender else ""
    params = [gender, gender] if gender else []
    sql = f"""
        SELECT b.brand AS name, b.n AS count, cover.img AS img
        FROM (
            SELECT brand, count(*) AS n
            FROM products
            WHERE brand IS NOT NULL {gender_sql}
            GROUP BY brand
        ) b
        LEFT JOIN LATERAL (
            SELECT COALESCE(images->0->>'full', images->0->>'thumb') AS img
            FROM products p
            WHERE p.brand = b.brand {gender_sql.replace('gender', 'p.gender')}
              AND jsonb_typeof(p.images) = 'array'
              AND jsonb_array_length(p.images) > 0
            ORDER BY p.last_synced DESC
            LIMIT 1
        ) cover ON true
        ORDER BY b.brand;
    """
    return _run(sql, params)


# Quick manual smoke test: python catalogue_db.py
if __name__ == "__main__":
    print("Brands (men):", distinct_brands("men")[:10])
    print("Black items:", count(colour="black"))
    first = browse(q="shirt", page_size=5)
    for p in first:
        print(f"  {p['id']}  {p['brand']:<18} R{p['price']}  {p['name'][:40]}")

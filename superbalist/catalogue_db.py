"""
Phase 1 — Catalogue data-access layer (Postgres)
================================================
Clean Python functions your app calls instead of writing SQL inline.
All the grid/browse/filter/search/paginate/single-product queries live here.

SETUP
    pip install psycopg2-binary
    export DATABASE_URL="postgresql://user:pass@host:5432/dbname"

USAGE
    import catalogue_db as db
    page = db.browse(gender="women", category="dresses", page=1)
    hits = db.search("linen shirt")
    item = db.get_product(1764584)
    brands = db.distinct_brands("men")
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/tryon")
PAGE_SIZE = 24

# Allowed sort options -> SQL ORDER BY (whitelist; never interpolate user input)
_SORTS = {
    "newest":     "last_synced DESC",
    "price_asc":  "price ASC NULLS LAST",
    "price_desc": "price DESC NULLS LAST",
}


def _conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def _run(sql, params=None):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Grid / browse with filters + pagination
# ---------------------------------------------------------------------------
def browse(gender=None, category=None, subcategory=None, brand=None,
           colour=None, min_price=None, max_price=None,
           sort="newest", page=1, page_size=PAGE_SIZE):
    """The main grid query. Any filter left as None is ignored.
    Returns a list of product rows (dicts) for the requested page."""
    where, params = [], []

    def add(clause, value):
        where.append(clause)
        params.append(value)

    if gender:      add("gender = %s", gender)
    if category:    add("category = %s", category)
    if subcategory: add("subcategory = %s", subcategory)
    if brand:       add("brand = %s", brand)
    if colour:      add("colour = %s", colour)
    if min_price is not None: add("price >= %s", min_price)
    if max_price is not None: add("price <= %s", max_price)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order_sql = _SORTS.get(sort, _SORTS["newest"])
    offset = (max(page, 1) - 1) * page_size

    sql = f"""
        SELECT id, name, brand, price, was_price, on_sale, colour,
               gender, category, subcategory, url, images
        FROM products
        {where_sql}
        ORDER BY {order_sql}
        LIMIT %s OFFSET %s;
    """
    return _run(sql, params + [page_size, offset])


def count(gender=None, category=None, subcategory=None, brand=None,
          colour=None, min_price=None, max_price=None):
    """Total matches for a filter set — use it to compute page counts / 'Show More'."""
    where, params = [], []

    def add(clause, value):
        where.append(clause)
        params.append(value)

    if gender:      add("gender = %s", gender)
    if category:    add("category = %s", category)
    if subcategory: add("subcategory = %s", subcategory)
    if brand:       add("brand = %s", brand)
    if colour:      add("colour = %s", colour)
    if min_price is not None: add("price >= %s", min_price)
    if max_price is not None: add("price <= %s", max_price)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = _run(f"SELECT count(*) AS n FROM products {where_sql};", params)
    return rows[0]["n"]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search(term, gender=None, page=1, page_size=PAGE_SIZE):
    """Case-insensitive name search. (Add the pg_trgm index from schema.sql
    to keep this fast once the table grows.)"""
    where = ["name ILIKE %s"]
    params = ["%" + term + "%"]
    if gender:
        where.append("gender = %s")
        params.append(gender)
    offset = (max(page, 1) - 1) * page_size
    sql = f"""
        SELECT id, name, brand, price, colour, url, images
        FROM products
        WHERE {' AND '.join(where)}
        ORDER BY last_synced DESC
        LIMIT %s OFFSET %s;
    """
    return _run(sql, params + [page_size, offset])


# ---------------------------------------------------------------------------
# Single product (the product page's base row)
# ---------------------------------------------------------------------------
def get_product(product_id):
    rows = _run("SELECT * FROM products WHERE id = %s;", [product_id])
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Filter-menu helpers (populate dropdowns)
# ---------------------------------------------------------------------------
def distinct_brands(gender=None):
    if gender:
        rows = _run("SELECT DISTINCT brand FROM products WHERE gender = %s AND brand IS NOT NULL ORDER BY brand;", [gender])
    else:
        rows = _run("SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL ORDER BY brand;")
    return [r["brand"] for r in rows]


def distinct_categories(gender=None):
    if gender:
        rows = _run("SELECT DISTINCT category FROM products WHERE gender = %s AND category IS NOT NULL ORDER BY category;", [gender])
    else:
        rows = _run("SELECT DISTINCT category FROM products WHERE category IS NOT NULL ORDER BY category;")
    return [r["category"] for r in rows]


# Quick manual smoke test: python catalogue_db.py
if __name__ == "__main__":
    import json
    print("Brands (men):", distinct_brands("men")[:10])
    first_page = browse(gender="men", category="tops", page=1, page_size=5)
    print(f"\nFirst {len(first_page)} men's tops:")
    for p in first_page:
        print(f"  {p['id']}  {p['brand']:<18} R{p['price']}  {p['name'][:40]}")

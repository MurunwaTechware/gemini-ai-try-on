-- ============================================================
--  Phase 1 schema — garment catalogue (Postgres)
--  Run once:  psql "$DATABASE_URL" -f schema.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS products (
    id           BIGINT PRIMARY KEY,         -- Superbalist product id (unique key for upsert)
    name         TEXT NOT NULL,
    brand        TEXT,
    price        NUMERIC(10,2),
    was_price    NUMERIC(10,2),              -- original price when on sale
    currency     TEXT DEFAULT 'ZAR',
    on_sale      BOOLEAN DEFAULT FALSE,
    gender       TEXT,                        -- 'men' | 'women' | 'kids'
    category     TEXT,                        -- 'tops'
    subcategory  TEXT,                        -- 't-shirts-vests'
    colour       TEXT,
    url          TEXT,                         -- product page link
    images       JSONB,                        -- list of {full, thumb} image URLs
    first_seen   TIMESTAMPTZ DEFAULT now(),    -- when first inserted
    last_synced  TIMESTAMPTZ DEFAULT now()     -- when the sync job last touched it
);

-- Indexes that make the grid filters fast (these ARE database indexes —
-- lookup structures, not the catalogue itself).
CREATE INDEX IF NOT EXISTS idx_products_gender_cat   ON products (gender, category);
CREATE INDEX IF NOT EXISTS idx_products_brand        ON products (brand);
CREATE INDEX IF NOT EXISTS idx_products_price        ON products (price);
CREATE INDEX IF NOT EXISTS idx_products_last_synced  ON products (last_synced DESC);

-- OPTIONAL: fast fuzzy name search. Needs the pg_trgm extension.
-- Uncomment both lines if you want quick ILIKE '%term%' searches at scale.
-- CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- CREATE INDEX IF NOT EXISTS idx_products_name_trgm ON products USING gin (name gin_trgm_ops);

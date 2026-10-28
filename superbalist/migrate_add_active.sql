-- ============================================================
--  Migration: add the `active` flag for stale-product hiding
--  Run once on each database (local and Cloud SQL):
--      psql "$DATABASE_URL" -f migrate_add_active.sql
--  Safe to run more than once (IF NOT EXISTS guards everything).
-- ============================================================

-- Products the sync hasn't seen recently get active=false and are hidden from
-- the app — but kept in the table, so nothing is lost and it's reversible.
ALTER TABLE products ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;

-- The grid queries filter on active, so index it alongside the common filters.
CREATE INDEX IF NOT EXISTS idx_products_active ON products (active);
CREATE INDEX IF NOT EXISTS idx_products_active_gender_cat ON products (active, gender, category);

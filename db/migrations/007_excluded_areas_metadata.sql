-- Track excluded-area provenance so refreshes are repeatable and auditable.

ALTER TABLE lla.excluded_areas
    ADD COLUMN IF NOT EXISTS county_fips CHAR(5),
    ADD COLUMN IF NOT EXISTS name TEXT,
    ADD COLUMN IF NOT EXISTS source_id TEXT,
    ADD COLUMN IF NOT EXISTS source_url TEXT,
    ADD COLUMN IF NOT EXISTS notes TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_lla_excluded_areas_county_type
    ON lla.excluded_areas (county_fips, area_type);

CREATE UNIQUE INDEX IF NOT EXISTS idx_lla_excluded_areas_source_feature
    ON lla.excluded_areas (source, source_id)
    WHERE source IS NOT NULL AND source_id IS NOT NULL;

-- Zoning crawler layer: municipality checklist, code-host tracking, per-district rules

-- Enrich jurisdictions so each municipality carries its GIS name + unincorporated flag
ALTER TABLE lla.jurisdictions ADD COLUMN IF NOT EXISTS gis_name TEXT;
ALTER TABLE lla.jurisdictions ADD COLUMN IF NOT EXISTS is_unincorporated BOOLEAN NOT NULL DEFAULT false;

-- Where each jurisdiction's rules live + crawl progress (one row per source_type)
CREATE TABLE IF NOT EXISTS lla.jurisdiction_sources (
    source_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    jurisdiction_id UUID NOT NULL REFERENCES lla.jurisdictions(jurisdiction_id) ON DELETE CASCADE,
    source_type TEXT NOT NULL DEFAULT 'zoning_code'
        CHECK (source_type IN ('zoning_code', 'zoning_map', 'flu')),
    provider TEXT,        -- municode | ecode360 | amlegal | city_site | arcgis
    url TEXT,
    code_section TEXT,    -- e.g. "Chapter 30 - Zoning"
    crawl_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (crawl_status IN ('pending', 'found', 'crawled', 'extracted', 'failed', 'not_available')),
    last_crawled_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (jurisdiction_id, source_type)
);

CREATE INDEX IF NOT EXISTS idx_lla_jurisdiction_sources_status
    ON lla.jurisdiction_sources (source_type, crawl_status);

-- Per zoning district rules extracted from each jurisdiction's code (the crawler output)
CREATE TABLE IF NOT EXISTS lla.zoning_districts (
    district_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    jurisdiction_id UUID NOT NULL REFERENCES lla.jurisdictions(jurisdiction_id) ON DELETE CASCADE,
    district_code TEXT NOT NULL,
    district_name TEXT,
    category TEXT,            -- residential | commercial | mixed_use | industrial | agricultural | civic | other
    allows_residential BOOLEAN,
    allows_multifamily BOOLEAN,
    max_density_du_ac NUMERIC,
    max_height_ft NUMERIC,
    max_height_stories NUMERIC,
    max_far NUMERIC,
    min_lot_sf NUMERIC,
    max_lot_coverage NUMERIC,
    front_setback_ft NUMERIC,
    side_setback_ft NUMERIC,
    rear_setback_ft NUMERIC,
    parking_per_unit NUMERIC,
    code_citation TEXT,
    source_url TEXT,
    raw_excerpt TEXT,
    confidence TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    extraction_model TEXT,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (jurisdiction_id, district_code)
);

CREATE INDEX IF NOT EXISTS idx_lla_zoning_districts_juris
    ON lla.zoning_districts (jurisdiction_id);
CREATE INDEX IF NOT EXISTS idx_lla_zoning_districts_category
    ON lla.zoning_districts (category);

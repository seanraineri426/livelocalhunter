-- Source-backed utility allowance schedules for affordable gross-rent checks.
-- Allowances are stored separately from FHFC/HUD gross rent limits because the
-- applicable PHA, utility profile, unit type, and owner/tenant-paid split can vary.

CREATE TABLE IF NOT EXISTS lla.utility_allowances (
    utility_allowance_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    county_fips CHAR(5) NOT NULL,
    county_name TEXT NOT NULL,
    year INTEGER NOT NULL,
    bedroom_count INTEGER NOT NULL CHECK (bedroom_count >= 0 AND bedroom_count <= 8),
    allowance_monthly NUMERIC(12, 2) NOT NULL CHECK (allowance_monthly >= 0),
    jurisdiction_name TEXT,
    pha_name TEXT NOT NULL,
    source_area TEXT,
    unit_type TEXT,
    utility_profile TEXT NOT NULL,
    effective_date DATE,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence TEXT NOT NULL DEFAULT 'medium'
        CHECK (confidence IN ('low', 'medium', 'high')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (county_fips, year, bedroom_count, pha_name, source_area, unit_type, utility_profile)
);

CREATE INDEX IF NOT EXISTS idx_lla_utility_allowances_lookup
    ON lla.utility_allowances (county_fips, year DESC, bedroom_count, confidence);

CREATE INDEX IF NOT EXISTS idx_lla_utility_allowances_raw
    ON lla.utility_allowances USING GIN (raw);

ALTER TABLE lla.utility_allowances ENABLE ROW LEVEL SECURITY;

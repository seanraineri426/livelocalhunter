-- Financial feasibility MVP for parcel-level Live Local screening.
-- Additive only: keeps legacy lla.ami_rent_limits and lla.feasibility intact.

CREATE TABLE IF NOT EXISTS lla.rent_limits (
    rent_limit_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    county_fips CHAR(5) NOT NULL,
    county_name TEXT NOT NULL,
    year INTEGER NOT NULL,
    program TEXT NOT NULL DEFAULT 'FHFC Rental Programs',
    ami_band INTEGER NOT NULL CHECK (ami_band > 0 AND ami_band <= 140),
    bedroom_count INTEGER NOT NULL CHECK (bedroom_count >= 0 AND bedroom_count <= 6),
    max_monthly_rent NUMERIC(12, 2) NOT NULL CHECK (max_monthly_rent >= 0),
    effective_date DATE,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (county_fips, year, program, ami_band, bedroom_count)
);

CREATE INDEX IF NOT EXISTS idx_lla_rent_limits_lookup
    ON lla.rent_limits (county_fips, year DESC, ami_band, bedroom_count);

CREATE INDEX IF NOT EXISTS idx_lla_rent_limits_raw
    ON lla.rent_limits USING GIN (raw);

ALTER TABLE lla.millage
    ADD COLUMN IF NOT EXISTS jurisdiction_name TEXT,
    ADD COLUMN IF NOT EXISTS effective_date DATE,
    ADD COLUMN IF NOT EXISTS opt_out_source_url TEXT,
    ADD COLUMN IF NOT EXISTS millage_source_url TEXT,
    ADD COLUMN IF NOT EXISTS raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_lla_millage_county_tax_year
    ON lla.millage (tax_year, authority_type, opted_out_middle);

CREATE TABLE IF NOT EXISTS lla.parcel_scenarios (
    scenario_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    parcel_id UUID NOT NULL REFERENCES lla.parcels(parcel_id) ON DELETE CASCADE,
    scenario_name TEXT NOT NULL DEFAULT 'base',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'needs_review', 'reviewed', 'archived')),
    assumptions_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    feasibility_output_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    tax_exemption_output_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    cost_audit_jsonb JSONB,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lla_parcel_scenarios_parcel_updated
    ON lla.parcel_scenarios (parcel_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_lla_parcel_scenarios_status
    ON lla.parcel_scenarios (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS lla.parcel_notes (
    note_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    parcel_id UUID NOT NULL REFERENCES lla.parcels(parcel_id) ON DELETE CASCADE,
    note_type TEXT NOT NULL DEFAULT 'general',
    note TEXT NOT NULL,
    source_url TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lla_parcel_notes_parcel_created
    ON lla.parcel_notes (parcel_id, created_at DESC);

CREATE TABLE IF NOT EXISTS lla.parcel_review_status (
    parcel_id UUID PRIMARY KEY REFERENCES lla.parcels(parcel_id) ON DELETE CASCADE,
    review_status TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (review_status IN ('unreviewed', 'needs_review', 'watch', 'pursue', 'fail')),
    reviewer TEXT,
    reviewed_at TIMESTAMPTZ,
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

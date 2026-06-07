-- Live Local Act schema (isolated from existing public.* tables)

CREATE SCHEMA IF NOT EXISTS lla;

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS lla.jurisdictions (
    jurisdiction_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    county_fips CHAR(5) NOT NULL,
    jurisdiction_type TEXT NOT NULL CHECK (jurisdiction_type IN ('county', 'municipality')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, county_fips)
);

CREATE TABLE IF NOT EXISTS lla.sites (
    site_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stage TEXT NOT NULL DEFAULT 'prospect',
    owner_contact JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lla.parcels (
    parcel_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    county_fips CHAR(5) NOT NULL,
    source_parcel_id TEXT NOT NULL,
    source_parcel_id_normalized TEXT,
    geom GEOMETRY(MultiPolygon, 4326) NOT NULL,
    acreage NUMERIC,
    lot_sf NUMERIC,
    zoning_code TEXT,
    use_class TEXT,
    jurisdiction_id UUID REFERENCES lla.jurisdictions(jurisdiction_id),
    valid_from DATE,
    valid_to DATE,
    source TEXT NOT NULL DEFAULT 'regrid',
    as_of_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (county_fips, source_parcel_id)
);

CREATE INDEX IF NOT EXISTS idx_lla_parcels_geom ON lla.parcels USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_lla_parcels_county ON lla.parcels (county_fips);
CREATE INDEX IF NOT EXISTS idx_lla_parcels_use_class ON lla.parcels (use_class);

CREATE TABLE IF NOT EXISTS lla.parcel_lineage (
    predecessor_parcel_id UUID NOT NULL REFERENCES lla.parcels(parcel_id) ON DELETE CASCADE,
    successor_parcel_id UUID NOT NULL REFERENCES lla.parcels(parcel_id) ON DELETE CASCADE,
    PRIMARY KEY (predecessor_parcel_id, successor_parcel_id)
);

CREATE TABLE IF NOT EXISTS lla.site_parcels (
    site_id UUID NOT NULL REFERENCES lla.sites(site_id) ON DELETE CASCADE,
    parcel_id UUID NOT NULL REFERENCES lla.parcels(parcel_id) ON DELETE CASCADE,
    PRIMARY KEY (site_id, parcel_id)
);

CREATE TABLE IF NOT EXISTS lla.jurisdiction_params (
    jurisdiction_id UUID PRIMARY KEY REFERENCES lla.jurisdictions(jurisdiction_id) ON DELETE CASCADE,
    max_density_du_ac NUMERIC,
    max_far NUMERIC,
    far_2023_snapshot NUMERIC,
    zoning_crosswalk_ref TEXT,
    base_parking_per_unit NUMERIC,
    params_version TEXT NOT NULL DEFAULT 'v1',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lla.millage (
    millage_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    jurisdiction_id UUID NOT NULL REFERENCES lla.jurisdictions(jurisdiction_id) ON DELETE CASCADE,
    authority_name TEXT NOT NULL,
    authority_type TEXT NOT NULL CHECK (authority_type IN ('county', 'municipal', 'school', 'special')),
    millage NUMERIC NOT NULL,
    opted_out_middle BOOLEAN NOT NULL DEFAULT false,
    county_has_adequate_supply BOOLEAN,
    tax_year INTEGER NOT NULL,
    UNIQUE (jurisdiction_id, authority_name, tax_year)
);

CREATE TABLE IF NOT EXISTS lla.local_requirements (
    requirement_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    jurisdiction_id UUID NOT NULL REFERENCES lla.jurisdictions(jurisdiction_id) ON DELETE CASCADE,
    req_type TEXT NOT NULL,
    value JSONB NOT NULL,
    code_citation TEXT,
    effective_date DATE NOT NULL,
    preemption_status TEXT NOT NULL CHECK (preemption_status IN ('preempted', 'surviving', 'contested')),
    preempted_by_version TEXT,
    notes TEXT,
    confidence TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lla.excluded_areas (
    excluded_area_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    geom GEOMETRY(MultiPolygon, 4326) NOT NULL,
    area_type TEXT NOT NULL CHECK (area_type IN ('airport', 'wekiva', 'everglades', 'waterfront')),
    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lla_excluded_areas_geom ON lla.excluded_areas USING GIST (geom);

CREATE TABLE IF NOT EXISTS lla.transit_stops (
    stop_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stop_name TEXT,
    agency TEXT,
    geom GEOMETRY(Point, 4326) NOT NULL,
    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lla_transit_stops_geom ON lla.transit_stops USING GIST (geom);

CREATE TABLE IF NOT EXISTS lla.ami_rent_limits (
    rent_limit_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    county_fips CHAR(5) NOT NULL,
    year INTEGER NOT NULL,
    ami_band INTEGER NOT NULL CHECK (ami_band IN (80, 120)),
    max_rent NUMERIC NOT NULL,
    source TEXT,
    UNIQUE (county_fips, year, ami_band)
);

CREATE TABLE IF NOT EXISTS lla.entitlement (
    entitlement_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES lla.sites(site_id) ON DELETE CASCADE,
    parcel_id UUID REFERENCES lla.parcels(parcel_id) ON DELETE CASCADE,
    eligible BOOLEAN NOT NULL,
    failed_reasons TEXT[] NOT NULL DEFAULT '{}',
    max_units NUMERIC,
    max_height_stories NUMERIC,
    buildable_sf NUMERIC,
    required_parking NUMERIC,
    statute_version TEXT NOT NULL,
    params_version TEXT NOT NULL,
    confidence TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (site_id IS NOT NULL OR parcel_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_lla_entitlement_parcel ON lla.entitlement (parcel_id);
CREATE INDEX IF NOT EXISTS idx_lla_entitlement_eligible ON lla.entitlement (eligible);

CREATE TABLE IF NOT EXISTS lla.feasibility (
    feasibility_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES lla.sites(site_id) ON DELETE CASCADE,
    noi NUMERIC,
    stabilized_value NUMERIC,
    total_cost NUMERIC,
    spread_bps NUMERIC,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lla.permits (
    permit_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    parcel_id UUID REFERENCES lla.parcels(parcel_id) ON DELETE SET NULL,
    permit_type TEXT,
    status TEXT,
    issued_date DATE,
    finalized_date DATE,
    likely_lla BOOLEAN,
    source TEXT,
    source_permit_id TEXT,
    raw JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_permit_id)
);

INSERT INTO lla.jurisdictions (name, county_fips, jurisdiction_type)
VALUES
    ('Miami-Dade County', '12086', 'county'),
    ('Broward County', '12011', 'county'),
    ('Palm Beach County', '12099', 'county')
ON CONFLICT (name, county_fips) DO NOTHING;

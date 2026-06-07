-- Miami-Dade parcel enrichment from MD_LandInformation/MapServer/19.
--
-- Layer 19 (Municipal Zoning) provides MUNICNAME plus GENRLLUTYPE/ZONE.
-- GENRLLUTYPE is the audit-preferred general land-use signal, while the
-- original parcel PRIMARY_ZONE remains in lla.parcels.zoning_code.

ALTER TABLE lla.parcels
    ADD COLUMN IF NOT EXISTS zoning_general_use TEXT,
    ADD COLUMN IF NOT EXISTS zoning_map_zone TEXT,
    ADD COLUMN IF NOT EXISTS zoning_map_description TEXT,
    ADD COLUMN IF NOT EXISTS zoning_map_municipality TEXT,
    ADD COLUMN IF NOT EXISTS zoning_rescue BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS zoning_enriched_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_lla_parcels_zoning_general_use
    ON lla.parcels (county_fips, zoning_general_use)
    WHERE is_candidate;

CREATE INDEX IF NOT EXISTS idx_lla_parcels_jurisdiction_candidate
    ON lla.parcels (county_fips, jurisdiction_id)
    WHERE is_candidate;

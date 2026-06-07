-- Broward FLU / zoning-rescue enrichment fields.
--
-- Broward's parcel feed does not include local zoning. These columns preserve
-- a county Future Land Use spatial join so review buckets can be audited and
-- later rescued where FLU indicates commercial or mixed-use potential.

ALTER TABLE lla.parcels
    ADD COLUMN IF NOT EXISTS flu_code TEXT,
    ADD COLUMN IF NOT EXISTS flu_class TEXT,
    ADD COLUMN IF NOT EXISTS zoning_rescue BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS zoning_rescue_source TEXT,
    ADD COLUMN IF NOT EXISTS zoning_rescue_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_lla_parcels_broward_zoning_rescue
    ON lla.parcels (county_fips, zoning_rescue)
    WHERE county_fips = '12011';

-- Additive site-address columns for lla.parcels.
--
-- The parcels table previously stored only the county folio/source parcel id. The
-- county appraiser ArcGIS sources already expose a physical site address (Miami-Dade
-- TRUE_SITE_ADDR/TRUE_SITE_CITY/TRUE_SITE_ZIP_CODE, Broward PHY_ADDR1/PHY_CITY/PHY_ZIPCD,
-- Palm Beach SITE_ADDR_STR/MUNICIPALITY/ZIP1) but the ingest pipeline dropped them.
-- These columns let us surface the site address in parcel chat and the web UI next to
-- the folio. Coverage is partial (vacant/aggregate tracts often have no site address),
-- so all columns are nullable.

ALTER TABLE lla.parcels ADD COLUMN IF NOT EXISTS site_address TEXT;
ALTER TABLE lla.parcels ADD COLUMN IF NOT EXISTS site_city TEXT;
ALTER TABLE lla.parcels ADD COLUMN IF NOT EXISTS site_zip TEXT;
ALTER TABLE lla.parcels ADD COLUMN IF NOT EXISTS address_source TEXT;
ALTER TABLE lla.parcels ADD COLUMN IF NOT EXISTS address_updated_at TIMESTAMPTZ;

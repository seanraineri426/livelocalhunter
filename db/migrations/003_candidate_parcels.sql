-- Candidate parcel classification layer
--
-- Current-use/zoning source fields differ by county, so classification is done
-- in Python during ingest. These columns persist the decision so downstream
-- eligibility/scoring can distinguish true candidates from review/safety-net
-- buckets and audit why a parcel was kept.

ALTER TABLE lla.parcels
    ADD COLUMN IF NOT EXISTS is_candidate BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS candidate_bucket TEXT,
    ADD COLUMN IF NOT EXISTS candidate_reason TEXT,
    ADD COLUMN IF NOT EXISTS normalized_use TEXT;

CREATE INDEX IF NOT EXISTS idx_lla_parcels_candidate
    ON lla.parcels (county_fips, is_candidate, candidate_bucket);

CREATE INDEX IF NOT EXISTS idx_lla_parcels_normalized_use
    ON lla.parcels (normalized_use);

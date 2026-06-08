-- Preserve parcel-level statutory massing provenance and legal-input limitations.

ALTER TABLE lla.entitlement
    ADD COLUMN IF NOT EXISTS massing_flags TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS massing_inputs JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_lla_entitlement_massing_flags
    ON lla.entitlement USING GIN (massing_flags);

-- Parcel-level eligibility runs upsert one entitlement row per parcel.

CREATE UNIQUE INDEX IF NOT EXISTS idx_lla_entitlement_unique_parcel
    ON lla.entitlement (parcel_id)
    WHERE parcel_id IS NOT NULL;

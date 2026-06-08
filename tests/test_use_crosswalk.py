from __future__ import annotations

from lla.use_crosswalk import categorize_land_use, parcel_zoning_land_use


def test_parcel_zoning_overrides_current_use_bucket_when_nonqualifying():
    row = {
        "candidate_bucket": "core_commercial",
        "normalized_use": "commercial",
        "zoning_general_use": "RSF",
    }

    decision = categorize_land_use(row)

    assert decision.eligible is False
    assert decision.reason == "parcel zoning via zoning_general_use=RSF"


def test_parcel_zoning_qualifies_mixed_use_code():
    row = {
        "candidate_bucket": "multifamily_redevelopment_review",
        "zoning_general_use": "RC",
    }

    zoning = parcel_zoning_land_use(row)
    decision = categorize_land_use(row)

    assert zoning is not None
    assert zoning.category == "mixed_use"
    assert decision.eligible is True
    assert decision.category == "mixed_use"

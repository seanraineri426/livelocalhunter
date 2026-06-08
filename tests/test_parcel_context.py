from __future__ import annotations

from lla.parcel_context import _entitlement_massing_fields, _summary_sections


def test_ineligible_parcel_strips_massing_from_context_fields():
    parcel = {
        "eligible": False,
        "failed_reasons": ["not_lla_land_category"],
        "max_units": 25208,
        "max_height_stories": 3,
        "buildable_sf": 999999,
        "required_parking": 100,
        "massing_flags": ["oversized_parcel_review_required"],
        "massing_inputs": {"binding_constraint": "density"},
    }

    massing = _entitlement_massing_fields(parcel)
    assert massing["max_units"] is None
    assert massing["buildable_sf"] is None
    assert massing["massing_flags"] == []
    assert massing["massing_inputs"] == {}


def test_ineligible_summary_hides_massing_and_keeps_eligibility_reasons():
    parcel = {
        "eligible": False,
        "failed_reasons": ["not_lla_land_category"],
        "entitlement_confidence": "high",
        "max_units": 25208,
        "massing_flags": ["oversized_parcel_review_required"],
        "massing_inputs": {},
        "parcel_id": "p1",
        "source_parcel_id": "3530210010010",
        "county_fips": "12086",
        "jurisdiction_name": "Doral",
    }
    summary = _summary_sections(parcel, [], [], [], None)

    assert summary["eligibility"]["status"] == "ineligible"
    assert summary["eligibility"]["failed_reasons"] == ["not_lla_land_category"]
    assert summary["massing"]["max_units"] is None
    assert summary["massing"]["applies"] is False
    assert summary["flags"] == []


def test_eligible_parcel_keeps_massing_fields():
    parcel = {
        "eligible": True,
        "failed_reasons": [],
        "max_units": 120,
        "buildable_sf": 50000,
        "max_height_stories": 5,
        "required_parking": 180,
        "massing_flags": [],
        "massing_inputs": {"binding_constraint": "far"},
    }

    massing = _entitlement_massing_fields(parcel)
    assert massing["max_units"] == 120
    assert massing["buildable_sf"] == 50000

from __future__ import annotations

from decimal import Decimal

from lla.massing import (
    AVG_UNIT_NET_SF,
    DEFAULT_LOT_COVERAGE,
    MASSING_COLUMNS_SQL,
    UNIT_GROSS_EFFICIENCY,
    _coverage_fraction,
    clear_ineligible_entitlement_massing,
    compute_massing,
    reconcile_max_units,
)


def test_clear_ineligible_entitlement_massing_sql_targets_stale_rows_only():
    assert "max_units = NULL" in MASSING_COLUMNS_SQL
    assert "massing_inputs = '{}'::jsonb" in MASSING_COLUMNS_SQL
    assert callable(clear_ineligible_entitlement_massing)


def test_coverage_fraction_normalizes_percent_and_fraction():
    assert _coverage_fraction(Decimal("40")) == Decimal("0.40")
    assert _coverage_fraction(Decimal("0.8")) == Decimal("0.8")
    assert _coverage_fraction(Decimal("100")) == Decimal("1")
    assert _coverage_fraction(0) is None
    assert _coverage_fraction(None) is None


def test_reconcile_takes_binding_minimum():
    rec = reconcile_max_units(
        acreage=Decimal("1"),
        lot_sf=Decimal("43560"),
        density_du_ac=Decimal("75"),
        statutory_far=Decimal("1.5"),
        height_stories=Decimal("3"),
        parking_ratio=Decimal("1.5"),
        max_lot_coverage=Decimal("40"),
        front_setback_ft=Decimal("25"),
        side_setback_ft=Decimal("25"),
        rear_setback_ft=Decimal("25"),
        zoning_matched=True,
    )
    # max_units must equal the smallest of the three candidate constraints.
    assert rec.max_units == min(
        rec.density_limited_units, rec.far_limited_units, rec.envelope_limited_units
    )
    assert rec.binding_constraint in {"density", "far", "footprint_height"}
    binding_value = {
        "density": rec.density_limited_units,
        "far": rec.far_limited_units,
        "footprint_height": rec.envelope_limited_units,
    }[rec.binding_constraint]
    assert binding_value == rec.max_units


def test_far_units_use_efficiency_and_avg_unit_size():
    rec = reconcile_max_units(
        acreage=Decimal("10"),
        lot_sf=Decimal("435600"),
        density_du_ac=Decimal("1000"),  # density intentionally non-binding
        statutory_far=Decimal("1.5"),
        height_stories=Decimal("100"),  # height intentionally non-binding
        parking_ratio=Decimal("1.5"),
        max_lot_coverage=Decimal("100"),
        zoning_matched=True,
    )
    far_buildable = Decimal("1.5") * Decimal("435600")
    expected = int((far_buildable * UNIT_GROSS_EFFICIENCY / AVG_UNIT_NET_SF))
    assert rec.binding_constraint == "far"
    assert rec.far_limited_units == expected
    assert rec.max_units == expected


def test_envelope_caps_units_when_height_is_low():
    # Low height + low coverage should make footprint x floors the binding constraint
    # and keep buildable_sf at or below the FAR cap.
    rec = reconcile_max_units(
        acreage=Decimal("100"),
        lot_sf=Decimal("4356000"),
        density_du_ac=Decimal("75"),
        statutory_far=Decimal("1.5"),
        height_stories=Decimal("3"),
        parking_ratio=Decimal("1.5"),
        max_lot_coverage=None,  # forces default coverage
        zoning_matched=False,
    )
    assert rec.binding_constraint == "footprint_height"
    assert rec.lot_coverage_fraction == DEFAULT_LOT_COVERAGE
    assert rec.buildable_sf <= rec.far_buildable_sf
    assert "lot_coverage_defaulted" in rec.flags
    assert "envelope_uses_default_lot_coverage" in rec.flags


def test_defaults_flagged_when_zoning_unmatched():
    rec = reconcile_max_units(
        acreage=Decimal("0.5"),
        lot_sf=Decimal("21780"),
        density_du_ac=Decimal("15"),
        statutory_far=Decimal("1.5"),
        height_stories=Decimal("3"),
        parking_ratio=Decimal("1.5"),
        zoning_matched=False,
    )
    assert "setbacks_defaulted" in rec.flags
    assert "lot_coverage_defaulted" in rec.flags


def _doral_megaparcel_row():
    return {
        "parcel_id": "ce238670-3dc5-44c8-95a6-600513ac60a6",
        "county_fips": "12086",
        "acreage": Decimal("453.6944214876033"),
        "lot_sf": Decimal("19762929.0"),
        "max_density_du_ac": Decimal("75"),
        "max_far": Decimal("1.5"),
        "base_parking_per_unit": Decimal("1.5"),
        "zoning_code": "6119",
        "subject_zoning_matched": False,
        "confidence": "medium",
    }


def test_doral_megaparcel_no_longer_pure_density_product():
    result = compute_massing(_doral_megaparcel_row())
    # Before the fix this returned floor(75 * 453.69) = 34027 with no envelope check.
    assert result.max_units < 34027
    assert result.max_units == result.massing_inputs["envelope_limited_units"]
    assert result.massing_inputs["binding_constraint"] == "footprint_height"
    assert "oversized_parcel_review_required" in result.massing_flags
    assert "manual_site_boundary_required" in result.massing_flags
    assert "parcel_zoning_unmatched_review_required" in result.massing_flags
    assert result.massing_inputs["parcel_zoning_match"] == "missing_or_unmatched"
    assert result.massing_inputs["land_category_reason"] == "no commercial/industrial/mixed-use signal"
    # Oversized aggregate tracts must not be marked high confidence.
    assert result.confidence == "low"


def test_compute_massing_records_binding_constraint_and_buildable_cap():
    result = compute_massing(_doral_megaparcel_row())
    inputs = result.massing_inputs
    assert "binding_constraint" in inputs
    assert Decimal(inputs["buildable_sf"]) <= Decimal(inputs["far_buildable_sf"])
    assert result.buildable_sf == Decimal(inputs["buildable_sf"])


def test_typical_lot_produces_plausible_units():
    row = {
        "parcel_id": "p-typical",
        "county_fips": "12086",
        "acreage": Decimal("1.151"),
        "lot_sf": Decimal("50135"),
        "max_density_du_ac": Decimal("75"),
        "max_far": Decimal("1.5"),
        "base_parking_per_unit": Decimal("1.5"),
        "subject_zoning_matched": True,
        "subject_max_lot_coverage": Decimal("40"),
        "subject_front_setback_ft": Decimal("25"),
        "subject_side_setback_ft": Decimal("25"),
        "subject_rear_setback_ft": Decimal("25"),
        "subject_height_stories": Decimal("3"),
        "confidence": "high",
    }
    result = compute_massing(row)
    # A ~1 acre lot at 3 stories cannot plausibly exceed the density cap.
    assert 0 < result.max_units <= result.massing_inputs["density_limited_units"]
    assert result.max_units < 100

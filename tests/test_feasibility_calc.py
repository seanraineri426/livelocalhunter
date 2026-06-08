from __future__ import annotations

from lla.feasibility_calc import FeasibilityInputs, calculate_feasibility


def context(**entitlement_overrides):
    entitlement = {
        "eligible": True,
        "max_units": 100,
        "confidence": "high",
        "massing_flags": [],
    }
    entitlement.update(entitlement_overrides)
    return {"entitlement": entitlement}


def valid_inputs(**overrides):
    values = {
        "market_monthly_rent": 3000,
        "utilities_included": True,
        "required_yield_on_cost": "0.065",
        "acquisition_price": 5_000_000,
        "gross_sf": 120_000,
        "hard_cost_per_gross_sf": 240,
    }
    values.update(overrides)
    return FeasibilityInputs(**values)


def rent_limit(value=2200):
    return {"max_monthly_rent": value, "source_url": "https://example.test/rent"}


def utility_allowance(value=200):
    return {
        "allowance_monthly": value,
        "source_url": "https://example.test/utility",
        "warnings": ["utility_allowance_not_parcel_specific"],
    }


def test_basic_valid_gross_sf_hard_cost():
    result = calculate_feasibility(parcel_context=context(), inputs=valid_inputs(), affordable_rent_limit=rent_limit())
    assert result["costs"]["hard_cost_basis"] == "gross_sf"
    assert result["costs"]["hard_costs"] == 28_800_000
    assert result["income"]["gross_income"] == 3_216_000


def test_negative_supportable_land_value_fails():
    result = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(market_monthly_rent=1000, hard_cost_per_gross_sf=500),
        affordable_rent_limit=rent_limit(800),
    )
    assert result["costs"]["supportable_land_value"] < 0
    assert result["result"] == "fail"


def test_missing_acquisition_price_warns():
    result = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(acquisition_price=None),
        affordable_rent_limit=rent_limit(),
    )
    assert "acquisition_price_missing" in result["warnings"]
    assert "feasibility_ratio_missing_acquisition" in result["warnings"]


def test_missing_hard_cost_needs_review():
    result = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(gross_sf=None, hard_cost_per_gross_sf=None),
        affordable_rent_limit=rent_limit(),
    )
    assert "hard_cost_missing" in result["warnings"]
    assert result["result"] == "needs_review"


def test_per_unit_hard_cost_basis():
    result = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(gross_sf=None, hard_cost_per_gross_sf=None, hard_cost_per_unit=300_000),
        affordable_rent_limit=rent_limit(),
    )
    assert result["costs"]["hard_cost_basis"] == "hard_cost_per_unit"
    assert result["costs"]["hard_costs"] == 30_000_000


def test_total_hard_cost_basis():
    result = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(total_hard_cost=31_000_000, hard_cost_per_gross_sf=None),
        affordable_rent_limit=rent_limit(),
    )
    assert result["costs"]["hard_cost_basis"] == "total_hard_cost"
    assert result["costs"]["hard_costs"] == 31_000_000


def test_ratio_thresholds():
    pursue = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(acquisition_price=1_000_000, total_hard_cost=20_000_000),
        affordable_rent_limit=rent_limit(),
    )
    watch = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(acquisition_price=4_000_000, total_hard_cost=20_000_000),
        affordable_rent_limit=rent_limit(),
    )
    fail = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(acquisition_price=30_000_000, total_hard_cost=20_000_000),
        affordable_rent_limit=rent_limit(),
    )
    assert pursue["result"] == "pursue"
    assert watch["result"] == "watch"
    assert fail["result"] == "fail"


def test_default_40_60_unit_split():
    result = calculate_feasibility(parcel_context=context(), inputs=valid_inputs(), affordable_rent_limit=rent_limit())
    assert result["program"]["affordable_units"] == 40
    assert result["program"]["market_units"] == 60


def test_low_confidence_and_massing_flags_warn():
    result = calculate_feasibility(
        parcel_context=context(confidence="low", massing_flags=["historic_height_screen_missing"]),
        inputs=valid_inputs(),
        affordable_rent_limit=rent_limit(),
    )
    assert "entitlement_confidence_low" in result["warnings"]
    assert "massing_flag:historic_height_screen_missing" in result["warnings"]


def test_no_max_units_needs_review():
    result = calculate_feasibility(
        parcel_context=context(max_units=None),
        inputs=valid_inputs(total_units=None),
        affordable_rent_limit=rent_limit(),
    )
    assert "max_units_missing" in result["warnings"]
    assert result["result"] == "needs_review"


def test_user_units_above_massing_max_warns():
    result = calculate_feasibility(
        parcel_context=context(max_units=37),
        inputs=valid_inputs(total_units=120),
        affordable_rent_limit=rent_limit(),
    )
    assert "total_units_exceeds_massing_max_units" in result["warnings"]


def test_utility_allowance_subtracted_from_gross_rent_limit():
    result = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(utilities_included=False),
        affordable_rent_limit=rent_limit(2200),
        utility_allowance=utility_allowance(175),
    )
    assert result["rents"]["gross_rent_limit"] == 2200
    assert result["rents"]["utility_allowance"] == 175
    assert result["rents"]["tenant_paid_rent_limit"] == 2025
    assert result["rents"]["affordable_monthly_rent"] == 2025
    assert result["rents"]["affordable_rent_source"] == "stored_tenant_paid_rent_limit"


def test_missing_utility_allowance_warns_without_zero_subtraction():
    result = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(utilities_included=False),
        affordable_rent_limit=rent_limit(2200),
        utility_allowance={},
    )
    assert "utility_allowance_missing" in result["warnings"]
    assert "affordable_rent_missing" in result["warnings"]
    assert result["rents"]["tenant_paid_rent_limit"] is None
    assert result["rents"]["affordable_monthly_rent"] == 0


def test_override_above_tenant_paid_limit_warns():
    result = calculate_feasibility(
        parcel_context=context(),
        inputs=valid_inputs(utilities_included=False, affordable_monthly_rent_override=2100),
        affordable_rent_limit=rent_limit(2200),
        utility_allowance=utility_allowance(175),
    )
    assert "affordable_rent_user_override" in result["warnings"]
    assert "affordable_rent_override_exceeds_tenant_paid_limit" in result["warnings"]
    assert "affordable_rent_override_exceeds_gross_limit" in result["warnings"]
    assert result["rents"]["affordable_monthly_rent"] == 2100

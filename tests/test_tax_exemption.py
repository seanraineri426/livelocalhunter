from __future__ import annotations

from decimal import Decimal

from lla.tax_exemption import AffordableUnitMix, estimate_exemption


def _millage_row(**overrides):
    row = {
        "authority_name": "School Board Operating",
        "authority_type": "school",
        "millage": "6.5",
        "opted_out_middle": None,
        "county_has_adequate_supply": True,
        "opt_out_source_url": "https://example.test/opt-out",
        "millage_source_url": "https://example.test/millage",
    }
    row.update(overrides)
    return row


def test_unknown_opt_out_is_conservative_for_81_to_120_tier():
    result = estimate_exemption(
        assessed_value=Decimal("10000000"),
        total_units=100,
        affordable_mix=AffordableUnitMix(units_at_or_below_80_ami=80, units_81_to_120_ami=20),
        millage_rows=[_millage_row()],
    )
    authority = result["authorities"][0]
    assert authority["exempt_value_80_ami"] > 0
    assert authority["exempt_value_81_to_120_ami"] == 0.0
    assert "opt_out_unknown:School Board Operating" in result["warnings"]


def test_verified_non_opt_out_applies_75_percent_tier():
    result = estimate_exemption(
        assessed_value=Decimal("10000000"),
        total_units=100,
        affordable_mix=AffordableUnitMix(units_at_or_below_80_ami=80, units_81_to_120_ami=20),
        millage_rows=[_millage_row(opted_out_middle=False)],
    )
    authority = result["authorities"][0]
    assert authority["exempt_value_81_to_120_ami"] > 0
    assert "opt_out_unknown:School Board Operating" not in result["warnings"]

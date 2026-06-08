from __future__ import annotations

import pytest

from lla.feasibility_defaults import get_scenario_template, list_scenario_templates, merge_template_assumptions


def test_required_scenario_templates_exist():
    names = {template["template_name"] for template in list_scenario_templates()}
    assert names == {
        "conservative",
        "base_case",
        "aggressive",
        "internal_cost_advantage",
        "tax_exemption_case",
        "no_tax_benefit_case",
    }


def test_template_prefills_financial_and_source_flags():
    template = get_scenario_template("base_case")
    assumptions = template["assumptions"]
    for key in {
        "hard_cost_basis",
        "soft_cost_pct",
        "contingency_pct",
        "financing_carry_pct",
        "vacancy_rate",
        "opex_rate",
        "required_yield_on_cost",
        "affordable_share",
        "market_share",
        "utilities_included",
        "use_utility_allowance",
        "include_tax_exemption",
        "use_latest_market_rent_source",
        "rent_year",
        "tax_year",
    }:
        assert key in assumptions


def test_explicit_assumptions_override_template():
    merged = merge_template_assumptions({"vacancy_rate": 0.09}, template_name="base_case")
    assert merged["template_name"] == "base_case"
    assert merged["vacancy_rate"] == 0.09


def test_unknown_template_raises():
    with pytest.raises(ValueError):
        get_scenario_template("missing")

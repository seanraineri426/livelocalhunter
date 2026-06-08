"""Reusable scenario template defaults for feasibility screening.

The database migration seeds the same templates in ``lla.scenario_templates`` for
reporting and future admin workflows. Python keeps the canonical runtime copy so
the API and tests can serve templates without requiring a live database.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SCENARIO_TEMPLATES: dict[str, dict[str, Any]] = {
    "conservative": {
        "template_name": "conservative",
        "label": "Conservative",
        "description": "Higher costs, higher vacancy and yield, lower market exposure.",
        "assumptions": {
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "soft_cost_pct": 0.24,
            "contingency_pct": 0.08,
            "financing_carry_pct": 0.10,
            "vacancy_rate": 0.07,
            "opex_rate": 0.38,
            "required_yield_on_cost": 0.0725,
            "affordable_share": 0.45,
            "market_share": 0.55,
            "utilities_included": False,
            "use_utility_allowance": True,
            "include_tax_exemption": True,
            "use_latest_market_rent_source": True,
            "rent_year": 2026,
            "tax_year": 2026,
        },
    },
    "base_case": {
        "template_name": "base_case",
        "label": "Base Case",
        "description": "Default underwriting case aligned with the feasibility calculator defaults.",
        "assumptions": {
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "soft_cost_pct": 0.20,
            "contingency_pct": 0.05,
            "financing_carry_pct": 0.08,
            "vacancy_rate": 0.05,
            "opex_rate": 0.35,
            "required_yield_on_cost": 0.065,
            "affordable_share": 0.40,
            "market_share": 0.60,
            "utilities_included": False,
            "use_utility_allowance": True,
            "include_tax_exemption": True,
            "use_latest_market_rent_source": True,
            "rent_year": 2026,
            "tax_year": 2026,
        },
    },
    "aggressive": {
        "template_name": "aggressive",
        "label": "Aggressive",
        "description": "Lower cost load and yield with higher market-rate share.",
        "assumptions": {
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "soft_cost_pct": 0.18,
            "contingency_pct": 0.04,
            "financing_carry_pct": 0.07,
            "vacancy_rate": 0.04,
            "opex_rate": 0.32,
            "required_yield_on_cost": 0.06,
            "affordable_share": 0.40,
            "market_share": 0.60,
            "utilities_included": False,
            "use_utility_allowance": True,
            "include_tax_exemption": True,
            "use_latest_market_rent_source": True,
            "rent_year": 2026,
            "tax_year": 2026,
        },
    },
    "internal_cost_advantage": {
        "template_name": "internal_cost_advantage",
        "label": "Internal Cost Advantage",
        "description": "Base rent and vacancy case with reduced hard/soft cost load for in-house delivery.",
        "assumptions": {
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "hard_cost_discount_pct": 0.06,
            "soft_cost_pct": 0.18,
            "contingency_pct": 0.04,
            "financing_carry_pct": 0.075,
            "vacancy_rate": 0.05,
            "opex_rate": 0.34,
            "required_yield_on_cost": 0.0625,
            "affordable_share": 0.40,
            "market_share": 0.60,
            "utilities_included": False,
            "use_utility_allowance": True,
            "include_tax_exemption": True,
            "use_latest_market_rent_source": True,
            "rent_year": 2026,
            "tax_year": 2026,
        },
    },
    "tax_exemption_case": {
        "template_name": "tax_exemption_case",
        "label": "Tax Exemption Case",
        "description": "Base case that includes the stored Live Local tax exemption estimate.",
        "assumptions": {
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "soft_cost_pct": 0.20,
            "contingency_pct": 0.05,
            "financing_carry_pct": 0.08,
            "vacancy_rate": 0.05,
            "opex_rate": 0.35,
            "required_yield_on_cost": 0.065,
            "affordable_share": 0.40,
            "market_share": 0.60,
            "utilities_included": False,
            "use_utility_allowance": True,
            "include_tax_exemption": True,
            "use_latest_market_rent_source": True,
            "rent_year": 2026,
            "tax_year": 2026,
        },
    },
    "no_tax_benefit_case": {
        "template_name": "no_tax_benefit_case",
        "label": "No Tax Benefit Case",
        "description": "Base operating case with property tax savings excluded.",
        "assumptions": {
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "soft_cost_pct": 0.20,
            "contingency_pct": 0.05,
            "financing_carry_pct": 0.08,
            "vacancy_rate": 0.05,
            "opex_rate": 0.35,
            "required_yield_on_cost": 0.065,
            "affordable_share": 0.40,
            "market_share": 0.60,
            "utilities_included": False,
            "use_utility_allowance": True,
            "include_tax_exemption": False,
            "use_latest_market_rent_source": True,
            "rent_year": 2026,
            "tax_year": 2026,
        },
    },
}


def list_scenario_templates() -> list[dict[str, Any]]:
    """Return a JSON-safe copy of all active runtime templates."""

    return [deepcopy(template) for template in SCENARIO_TEMPLATES.values()]


def get_scenario_template(template_name: str | None) -> dict[str, Any] | None:
    """Return a copy of one template or ``None`` when no name is provided."""

    if not template_name:
        return None
    try:
        return deepcopy(SCENARIO_TEMPLATES[template_name])
    except KeyError as exc:
        raise ValueError(f"Unknown scenario template: {template_name}") from exc


def merge_template_assumptions(
    assumptions: dict[str, Any] | None = None,
    *,
    template_name: str | None = None,
) -> dict[str, Any]:
    """Apply a template first, then let explicit assumptions override it."""

    merged: dict[str, Any] = {}
    template = get_scenario_template(template_name)
    if template:
        merged.update(template["assumptions"])
        merged["template_name"] = template["template_name"]
    merged.update(assumptions or {})
    return merged

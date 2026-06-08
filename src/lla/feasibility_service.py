"""Shared orchestration for parcel-level feasibility workflows."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from lla.cost_audit import audit_cost_assumptions
from lla.db import get_engine
from lla.feasibility_calc import FeasibilityInputs, calculate_feasibility
from lla.feasibility_defaults import merge_template_assumptions
from lla.market_rents import latest_market_rent_source
from lla.parcel_context import build_parcel_context
from lla.rent_limits import lookup_rent_limit, lookup_utility_allowance, rent_limit_to_dict, utility_allowance_to_dict
from lla.tax_exemption import AffordableUnitMix, estimate_exemption, fetch_millage_rows


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def inputs_from_assumptions(assumptions: dict[str, Any]) -> FeasibilityInputs:
    allowed = set(FeasibilityInputs.__dataclass_fields__)
    return FeasibilityInputs(**{key: value for key, value in assumptions.items() if key in allowed})


def save_scenario(
    *,
    parcel_id: str,
    scenario_name: str,
    assumptions: dict[str, Any],
    feasibility_output: dict[str, Any],
    tax_exemption_output: dict[str, Any],
    cost_audit: dict[str, Any] | None = None,
    status: str | None = None,
) -> str:
    scenario_status = status or ("needs_review" if feasibility_output.get("result") == "needs_review" else "draft")
    engine = get_engine()
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO lla.parcel_scenarios (
                    parcel_id,
                    scenario_name,
                    status,
                    assumptions_jsonb,
                    feasibility_output_jsonb,
                    tax_exemption_output_jsonb,
                    cost_audit_jsonb,
                    updated_at
                )
                VALUES (
                    CAST(:parcel_id AS uuid),
                    :scenario_name,
                    :status,
                    CAST(:assumptions AS jsonb),
                    CAST(:feasibility AS jsonb),
                    CAST(:tax_exemption AS jsonb),
                    CAST(:cost_audit AS jsonb),
                    now()
                )
                RETURNING scenario_id::text
                """
            ),
            {
                "parcel_id": parcel_id,
                "scenario_name": scenario_name,
                "status": scenario_status,
                "assumptions": json.dumps(assumptions, default=json_default),
                "feasibility": json.dumps(feasibility_output, default=json_default),
                "tax_exemption": json.dumps(tax_exemption_output, default=json_default),
                "cost_audit": json.dumps(cost_audit, default=json_default) if cost_audit else None,
            },
        ).scalar_one()


def compute_parcel_feasibility(
    *,
    parcel_id: str,
    assumptions: dict[str, Any] | None = None,
    template_name: str | None = None,
    run_cost_audit: bool = False,
) -> dict[str, Any]:
    """Build context and run deterministic feasibility with existing modules."""

    merged_assumptions = merge_template_assumptions(assumptions, template_name=template_name)
    context = build_parcel_context(parcel_id=parcel_id)
    parcel = context["parcel"]
    entitlement = context["entitlement"]
    engine = get_engine()

    requested_bedrooms = int(merged_assumptions.get("bedrooms") or FeasibilityInputs.bedrooms)
    market_source = None
    with engine.connect() as conn:
        if merged_assumptions.get("use_latest_market_rent_source", True):
            market_source = latest_market_rent_source(
                parcel_id=parcel["parcel_id"],
                bedroom_count=requested_bedrooms,
                conn=conn,
            ) or latest_market_rent_source(parcel_id=parcel["parcel_id"], conn=conn)
            if market_source and not merged_assumptions.get("market_monthly_rent"):
                merged_assumptions["market_monthly_rent"] = market_source["market_rent_monthly"]
            if market_source and market_source.get("vacancy_rate") is not None and "vacancy_rate" not in merged_assumptions:
                merged_assumptions["vacancy_rate"] = market_source["vacancy_rate"]

        inputs = inputs_from_assumptions(merged_assumptions)
        rent_year = int(merged_assumptions.get("rent_year") or 2026)
        tax_year = int(merged_assumptions.get("tax_year") or 2026)
        rent_limit = lookup_rent_limit(
            county_fips=parcel["county_fips"],
            year=rent_year,
            ami_band=inputs.affordable_ami_band,
            bedroom_count=inputs.bedrooms,
            conn=conn,
        )
        utility_allowance = lookup_utility_allowance(
            county_fips=parcel["county_fips"],
            year=rent_year,
            bedroom_count=inputs.bedrooms,
            conn=conn,
        )
        millage_rows = fetch_millage_rows(
            conn,
            jurisdiction_id=(context.get("jurisdiction") or {}).get("jurisdiction_id"),
            tax_year=tax_year,
        )

    total_units = int(inputs.total_units or entitlement.get("max_units") or 0)
    affordable_units = int(total_units * float(Decimal(str(inputs.affordable_share))))
    if merged_assumptions.get("include_tax_exemption", True):
        tax_output = estimate_exemption(
            assessed_value=merged_assumptions.get("assessed_value") or merged_assumptions.get("estimated_assessed_value"),
            total_units=total_units,
            affordable_mix=AffordableUnitMix(
                units_at_or_below_80_ami=int(merged_assumptions.get("units_at_or_below_80_ami") or 0),
                units_81_to_120_ami=int(merged_assumptions.get("units_81_to_120_ami") or affordable_units),
            ),
            millage_rows=millage_rows,
        )
    else:
        tax_output = {
            "estimated_total_tax_savings": 0.0,
            "authorities": [],
            "warnings": ["tax_exemption_excluded_by_template"],
        }

    feasibility_output = calculate_feasibility(
        parcel_context=context,
        inputs=inputs,
        affordable_rent_limit=rent_limit_to_dict(rent_limit),
        utility_allowance=utility_allowance_to_dict(utility_allowance)
        if merged_assumptions.get("use_utility_allowance", True)
        else {},
        tax_exemption=tax_output,
    )
    if market_source:
        feasibility_output.setdefault("rents", {})["market_rent_source"] = market_source

    audit_output = None
    if run_cost_audit:
        audit_output = audit_cost_assumptions(
            parcel_context=context,
            assumptions=merged_assumptions,
            feasibility_output=feasibility_output,
        )

    return {
        "parcel_id": parcel["parcel_id"],
        "template_name": template_name,
        "assumptions": merged_assumptions,
        "rent_limit": rent_limit_to_dict(rent_limit),
        "utility_allowance": utility_allowance_to_dict(utility_allowance),
        "market_rent_source": market_source,
        "tax_exemption": tax_output,
        "feasibility": feasibility_output,
        "cost_audit": audit_output,
    }

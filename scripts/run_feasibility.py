#!/usr/bin/env python3
"""Run parcel-level financial feasibility and optionally save a scenario."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS  # noqa: E402
from lla.cost_audit import audit_cost_assumptions  # noqa: E402
from lla.db import get_engine  # noqa: E402
from lla.feasibility_calc import FeasibilityInputs, calculate_feasibility  # noqa: E402
from lla.parcel_context import build_parcel_context  # noqa: E402
from lla.rent_limits import lookup_rent_limit, rent_limit_to_dict  # noqa: E402
from lla.tax_exemption import AffordableUnitMix, estimate_exemption, fetch_millage_rows  # noqa: E402


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _load_assumptions(raw: str) -> dict[str, Any]:
    path = Path(raw)
    if path.exists():
        return json.loads(path.read_text())
    return json.loads(raw)


def _inputs_from_assumptions(assumptions: dict[str, Any]) -> FeasibilityInputs:
    allowed = set(FeasibilityInputs.__dataclass_fields__)
    return FeasibilityInputs(**{key: value for key, value in assumptions.items() if key in allowed})


def _save_scenario(
    *,
    parcel_id: str,
    scenario_name: str,
    assumptions: dict[str, Any],
    feasibility_output: dict[str, Any],
    tax_exemption_output: dict[str, Any],
    cost_audit: dict[str, Any] | None,
) -> str:
    status = "needs_review" if feasibility_output.get("result") == "needs_review" else "draft"
    engine = get_engine()
    with engine.begin() as conn:
        scenario_id = conn.execute(
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
                "status": status,
                "assumptions": json.dumps(assumptions, default=_json_default),
                "feasibility": json.dumps(feasibility_output, default=_json_default),
                "tax_exemption": json.dumps(tax_exemption_output, default=_json_default),
                "cost_audit": json.dumps(cost_audit, default=_json_default) if cost_audit else None,
            },
        ).scalar_one()
    return scenario_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    lookup = parser.add_mutually_exclusive_group(required=True)
    lookup.add_argument("--parcel-id")
    lookup.add_argument("--folio")
    parser.add_argument("--county", choices=sorted(COUNTY_FIPS), help="Required for ambiguous folio lookup")
    parser.add_argument("--assumptions", required=True, help="JSON string or path to assumptions JSON")
    parser.add_argument("--scenario-name", default="base")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--tax-year", type=int, default=2026)
    parser.add_argument("--rent-year", type=int, default=2026)
    args = parser.parse_args()

    assumptions = _load_assumptions(args.assumptions)
    inputs = _inputs_from_assumptions(assumptions)
    context = build_parcel_context(parcel_id=args.parcel_id, folio=args.folio, county=args.county)
    parcel = context["parcel"]
    entitlement = context["entitlement"]

    engine = get_engine()
    with engine.connect() as conn:
        rent_limit = lookup_rent_limit(
            county_fips=parcel["county_fips"],
            year=args.rent_year,
            ami_band=inputs.affordable_ami_band,
            bedroom_count=inputs.bedrooms,
            conn=conn,
        )
        millage_rows = fetch_millage_rows(
            conn,
            jurisdiction_id=(context.get("jurisdiction") or {}).get("jurisdiction_id"),
            tax_year=args.tax_year,
        )

    total_units = int(inputs.total_units or entitlement.get("max_units") or 0)
    affordable_units = int(total_units * float(Decimal(str(inputs.affordable_share))))
    assessed_value = assumptions.get("assessed_value") or assumptions.get("estimated_assessed_value")
    tax_output = estimate_exemption(
        assessed_value=assessed_value,
        total_units=total_units,
        affordable_mix=AffordableUnitMix(
            units_at_or_below_80_ami=int(assumptions.get("units_at_or_below_80_ami") or 0),
            units_81_to_120_ami=int(assumptions.get("units_81_to_120_ami") or affordable_units),
        ),
        millage_rows=millage_rows,
    )
    feasibility_output = calculate_feasibility(
        parcel_context=context,
        inputs=inputs,
        affordable_rent_limit=rent_limit_to_dict(rent_limit),
        tax_exemption=tax_output,
    )
    audit_output = audit_cost_assumptions(
        parcel_context=context,
        assumptions=assumptions,
        feasibility_output=feasibility_output,
    ) if args.audit else None

    scenario_id = None
    if args.save:
        scenario_id = _save_scenario(
            parcel_id=parcel["parcel_id"],
            scenario_name=args.scenario_name,
            assumptions=assumptions,
            feasibility_output=feasibility_output,
            tax_exemption_output=tax_output,
            cost_audit=audit_output,
        )

    print(
        json.dumps(
            {
                "parcel_id": parcel["parcel_id"],
                "scenario_id": scenario_id,
                "scenario_name": args.scenario_name,
                "assumptions": assumptions,
                "rent_limit": rent_limit_to_dict(rent_limit),
                "tax_exemption": tax_output,
                "feasibility": feasibility_output,
                "cost_audit": audit_output,
            },
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
    )


if __name__ == "__main__":
    main()

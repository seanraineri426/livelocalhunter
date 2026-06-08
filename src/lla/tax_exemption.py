"""Deterministic Live Local / Missing Middle ad valorem exemption estimate."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


MIN_MISSING_MIDDLE_UNITS = 71


@dataclass(frozen=True)
class AffordableUnitMix:
    units_at_or_below_80_ami: int = 0
    units_81_to_120_ami: int = 0

    @property
    def total_affordable_units(self) -> int:
        return self.units_at_or_below_80_ami + self.units_81_to_120_ami


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def fetch_millage_rows(conn: Connection, *, jurisdiction_id: str | None, tax_year: int) -> list[dict[str, Any]]:
    if not jurisdiction_id:
        return []
    rows = conn.execute(
        text(
            """
            SELECT
                authority_name,
                authority_type,
                millage,
                opted_out_middle,
                county_has_adequate_supply,
                tax_year,
                opt_out_source_url,
                millage_source_url
            FROM lla.millage
            WHERE jurisdiction_id = CAST(:jurisdiction_id AS uuid)
              AND tax_year = :tax_year
            ORDER BY authority_type, authority_name
            """
        ),
        {"jurisdiction_id": jurisdiction_id, "tax_year": tax_year},
    ).mappings()
    return [dict(row) for row in rows]


def estimate_exemption(
    *,
    assessed_value: Decimal | int | float | str | None,
    total_units: int,
    affordable_mix: AffordableUnitMix,
    millage_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Estimate exempt value and tax savings by authority.

    This is not a tax opinion. It applies the stored millage and opt-out facts to
    the statutory tiers: 100% exemption for units <=80% AMI and 75% for units
    above 80% and <=120% AMI, subject to authority-level opt-out where recorded.
    """

    warnings: list[str] = []
    assessed = _decimal(assessed_value)
    total_units = int(total_units or 0)
    if total_units <= 0:
        warnings.append("total_units_missing")
    if assessed <= 0:
        warnings.append("assessed_value_missing")
    if affordable_mix.total_affordable_units < MIN_MISSING_MIDDLE_UNITS:
        warnings.append("below_71_affordable_unit_threshold")
    if not millage_rows:
        warnings.append("millage_rows_missing")

    if total_units <= 0 or assessed <= 0:
        unit_value = Decimal("0")
    else:
        unit_value = assessed / Decimal(total_units)

    value_80 = unit_value * Decimal(max(affordable_mix.units_at_or_below_80_ami, 0))
    value_120 = unit_value * Decimal(max(affordable_mix.units_81_to_120_ami, 0))

    authority_results: list[dict[str, Any]] = []
    total_tax_savings = Decimal("0")
    total_exempt_value_weighted = Decimal("0")

    threshold_met = affordable_mix.total_affordable_units >= MIN_MISSING_MIDDLE_UNITS
    for row in millage_rows:
        millage = _decimal(row.get("millage"))
        opted_out = row.get("opted_out_middle")
        if opted_out is None:
            warnings.append(f"opt_out_unknown:{row.get('authority_name')}")
        if row.get("county_has_adequate_supply") is None:
            warnings.append(f"adequate_supply_unknown:{row.get('authority_name')}")
        exempt_80 = value_80 if threshold_met else Decimal("0")
        if not threshold_met or opted_out is True:
            exempt_120 = Decimal("0")
        elif opted_out is False:
            exempt_120 = value_120 * Decimal("0.75")
        else:
            exempt_120 = Decimal("0")
        exempt_value = exempt_80 + exempt_120
        tax_savings = exempt_value * millage / Decimal("1000")
        total_tax_savings += tax_savings
        total_exempt_value_weighted += exempt_value
        authority_results.append(
            {
                "authority_name": row.get("authority_name"),
                "authority_type": row.get("authority_type"),
                "millage": float(millage),
                "opted_out_middle": opted_out,
                "county_has_adequate_supply": row.get("county_has_adequate_supply"),
                "exempt_value_80_ami": float(exempt_80),
                "exempt_value_81_to_120_ami": float(exempt_120),
                "estimated_tax_savings": float(tax_savings),
                "opt_out_source_url": row.get("opt_out_source_url"),
                "millage_source_url": row.get("millage_source_url"),
            }
        )

    return {
        "legal_basis": {
            "statute": "Fla. Stat. 196.1978(3)",
            "source_url": "https://www.flsenate.gov/Laws/Statutes/2025/0196.1978",
            "caveat": "Screening estimate only; property appraiser and FHFC certification control exemption eligibility.",
        },
        "inputs": {
            "assessed_value": float(assessed),
            "total_units": total_units,
            "units_at_or_below_80_ami": affordable_mix.units_at_or_below_80_ami,
            "units_81_to_120_ami": affordable_mix.units_81_to_120_ami,
            "unit_value_assumption": "assessed_value / total_units",
        },
        "threshold_met": threshold_met,
        "estimated_total_exempt_value_weighted": float(total_exempt_value_weighted),
        "estimated_total_tax_savings": float(total_tax_savings),
        "authorities": authority_results,
        "warnings": sorted(dict.fromkeys(warnings)),
    }

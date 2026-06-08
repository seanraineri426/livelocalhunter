"""Pure financial feasibility calculator for parcel-level Live Local screens."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


DEFAULT_AFFORDABLE_SHARE = Decimal("0.40")
DEFAULT_MARKET_SHARE = Decimal("0.60")


@dataclass(frozen=True)
class FeasibilityInputs:
    total_units: int | None = None
    affordable_share: Decimal | int | float | str = DEFAULT_AFFORDABLE_SHARE
    market_share: Decimal | int | float | str = DEFAULT_MARKET_SHARE
    bedrooms: int = 2
    affordable_ami_band: int = 120
    affordable_monthly_rent_override: Decimal | int | float | str | None = None
    utilities_included: bool | str = False
    market_monthly_rent: Decimal | int | float | str | None = None
    vacancy_rate: Decimal | int | float | str = Decimal("0.05")
    opex_rate: Decimal | int | float | str = Decimal("0.35")
    required_yield_on_cost: Decimal | int | float | str | None = None
    acquisition_price: Decimal | int | float | str | None = None
    gross_sf: Decimal | int | float | str | None = None
    net_rentable_sf: Decimal | int | float | str | None = None
    hard_cost_per_gross_sf: Decimal | int | float | str | None = None
    hard_cost_per_unit: Decimal | int | float | str | None = None
    total_hard_cost: Decimal | int | float | str | None = None
    soft_cost_pct: Decimal | int | float | str = Decimal("0.20")
    contingency_pct: Decimal | int | float | str = Decimal("0.05")
    financing_carry_pct: Decimal | int | float | str = Decimal("0.08")
    tax_savings: Decimal | int | float | str = Decimal("0")


def _decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _ratio(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _parcel_warning_context(parcel_context: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    entitlement = parcel_context.get("entitlement") or {}
    if entitlement.get("eligible") is False:
        warnings.append("parcel_ineligible")
    if entitlement.get("confidence") in {"low", "medium"}:
        warnings.append(f"entitlement_confidence_{entitlement.get('confidence')}")
    for flag in entitlement.get("massing_flags") or []:
        warnings.append(f"massing_flag:{flag}")
    if entitlement.get("max_units") in (None, 0):
        warnings.append("max_units_missing")
    return warnings


def _hard_cost(inputs: FeasibilityInputs, total_units: int, warnings: list[str]) -> tuple[Decimal | None, str | None]:
    total_hard = _decimal(inputs.total_hard_cost)
    if total_hard and total_hard > 0:
        return total_hard, "total_hard_cost"

    per_unit = _decimal(inputs.hard_cost_per_unit)
    if per_unit and per_unit > 0 and total_units > 0:
        return per_unit * Decimal(total_units), "hard_cost_per_unit"

    per_gsf = _decimal(inputs.hard_cost_per_gross_sf)
    gross_sf = _decimal(inputs.gross_sf)
    if per_gsf and per_gsf > 0 and gross_sf and gross_sf > 0:
        return per_gsf * gross_sf, "gross_sf"

    warnings.append("hard_cost_missing")
    return None, None


def calculate_feasibility(
    *,
    parcel_context: dict[str, Any],
    inputs: FeasibilityInputs,
    affordable_rent_limit: dict[str, Any] | None = None,
    utility_allowance: dict[str, Any] | None = None,
    tax_exemption: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warnings = _parcel_warning_context(parcel_context)
    entitlement = parcel_context.get("entitlement") or {}
    default_units = int(_decimal(entitlement.get("max_units"), Decimal("0")) or 0)
    total_units = int(inputs.total_units or default_units or 0)
    if total_units <= 0:
        warnings.append("total_units_missing")
    if inputs.total_units and default_units > 0 and total_units > default_units:
        warnings.append("total_units_exceeds_massing_max_units")

    affordable_share = _decimal(inputs.affordable_share, DEFAULT_AFFORDABLE_SHARE) or DEFAULT_AFFORDABLE_SHARE
    market_share = _decimal(inputs.market_share, DEFAULT_MARKET_SHARE) or DEFAULT_MARKET_SHARE
    if affordable_share + market_share != Decimal("1"):
        warnings.append("unit_share_sum_not_one")

    affordable_units = int(math.floor(total_units * float(affordable_share)))
    market_units = max(total_units - affordable_units, 0)

    market_rent = _decimal(inputs.market_monthly_rent)
    if not market_rent or market_rent <= 0:
        warnings.append("market_rent_missing")
        market_rent = Decimal("0")

    rent_source = "missing"
    utilities_included = _bool(inputs.utilities_included)
    rent_limit_payload = affordable_rent_limit or {}
    utility_payload = utility_allowance or {}
    for warning in rent_limit_payload.get("warnings") or []:
        warnings.append(str(warning))
    for warning in utility_payload.get("warnings") or []:
        warnings.append(str(warning))

    gross_rent_limit = _decimal(rent_limit_payload.get("gross_rent_limit"))
    if gross_rent_limit is None:
        gross_rent_limit = _decimal(rent_limit_payload.get("max_monthly_rent"))
    utility_allowance_value = _decimal(utility_payload.get("allowance_monthly"))
    tenant_paid_rent_limit: Decimal | None = None

    if gross_rent_limit and gross_rent_limit > 0:
        if utilities_included:
            tenant_paid_rent_limit = gross_rent_limit
            utility_allowance_value = Decimal("0")
            warnings.append("utilities_included_assumption")
        elif utility_allowance_value is not None:
            tenant_paid_rent_limit = max(gross_rent_limit - utility_allowance_value, Decimal("0"))
        else:
            warnings.append("utility_allowance_missing")

    affordable_rent = _decimal(inputs.affordable_monthly_rent_override)
    if affordable_rent and affordable_rent > 0:
        rent_source = "user_override"
        warnings.append("affordable_rent_user_override")
        if gross_rent_limit and gross_rent_limit > 0:
            if utilities_included and affordable_rent > gross_rent_limit:
                warnings.append("affordable_rent_override_exceeds_gross_limit")
            elif utility_allowance_value is not None:
                override_gross_rent = affordable_rent + utility_allowance_value
                if tenant_paid_rent_limit is not None and affordable_rent > tenant_paid_rent_limit:
                    warnings.append("affordable_rent_override_exceeds_tenant_paid_limit")
                if override_gross_rent > gross_rent_limit:
                    warnings.append("affordable_rent_override_exceeds_gross_limit")
    elif tenant_paid_rent_limit and tenant_paid_rent_limit > 0:
        affordable_rent = tenant_paid_rent_limit
        rent_source = "stored_tenant_paid_rent_limit"
    else:
        affordable_rent = Decimal("0")
        warnings.append("affordable_rent_missing")

    vacancy_rate = _decimal(inputs.vacancy_rate, Decimal("0")) or Decimal("0")
    opex_rate = _decimal(inputs.opex_rate, Decimal("0")) or Decimal("0")
    tax_savings = _decimal(inputs.tax_savings, Decimal("0")) or Decimal("0")
    if tax_exemption:
        tax_savings = _decimal(tax_exemption.get("estimated_total_tax_savings"), tax_savings) or tax_savings

    gross_income = ((Decimal(market_units) * market_rent) + (Decimal(affordable_units) * affordable_rent)) * Decimal("12")
    vacancy_loss = gross_income * vacancy_rate
    effective_income = gross_income - vacancy_loss
    opex = effective_income * opex_rate
    noi_before_tax_savings = effective_income - opex
    noi = noi_before_tax_savings + tax_savings

    required_yield = _decimal(inputs.required_yield_on_cost)
    if not required_yield or required_yield <= 0:
        warnings.append("required_yield_on_cost_missing")
        supportable_total_project_cost = None
    else:
        supportable_total_project_cost = noi / required_yield

    hard_cost, hard_cost_basis = _hard_cost(inputs, total_units, warnings)
    soft_cost_pct = _decimal(inputs.soft_cost_pct, Decimal("0")) or Decimal("0")
    contingency_pct = _decimal(inputs.contingency_pct, Decimal("0")) or Decimal("0")
    financing_carry_pct = _decimal(inputs.financing_carry_pct, Decimal("0")) or Decimal("0")
    soft_costs = hard_cost * soft_cost_pct if hard_cost is not None else None
    contingency = hard_cost * contingency_pct if hard_cost is not None else None
    financing_carry = hard_cost * financing_carry_pct if hard_cost is not None else None
    tdc_excluding_land = (
        hard_cost + (soft_costs or Decimal("0")) + (contingency or Decimal("0")) + (financing_carry or Decimal("0"))
        if hard_cost is not None
        else None
    )

    supportable_land_value = (
        supportable_total_project_cost - tdc_excluding_land
        if supportable_total_project_cost is not None and tdc_excluding_land is not None
        else None
    )
    acquisition_price = _decimal(inputs.acquisition_price)
    if acquisition_price is None:
        warnings.append("acquisition_price_missing")

    feasibility_ratio = None
    if supportable_land_value is not None and acquisition_price and acquisition_price > 0:
        feasibility_ratio = supportable_land_value / acquisition_price
    elif supportable_land_value is not None and not acquisition_price:
        warnings.append("feasibility_ratio_missing_acquisition")

    result = "needs_review"
    if "max_units_missing" in warnings or "hard_cost_missing" in warnings or "market_rent_missing" in warnings:
        result = "needs_review"
    elif feasibility_ratio is not None:
        if feasibility_ratio >= Decimal("1.10"):
            result = "pursue"
        elif feasibility_ratio >= Decimal("0.90"):
            result = "watch"
        else:
            result = "fail"
    elif supportable_land_value is not None and supportable_land_value < 0:
        result = "fail"

    per_unit = Decimal(total_units) if total_units > 0 else None
    formulas = {
        "gross_income": "((market_units * market_monthly_rent) + (affordable_units * affordable_monthly_rent)) * 12",
        "effective_income": "gross_income * (1 - vacancy_rate)",
        "noi": "effective_income - (effective_income * opex_rate) + tax_savings",
        "supportable_total_project_cost": "NOI / required_yield_on_cost",
        "tdc_excluding_land": "hard_cost + soft_costs + contingency + financing_carry",
        "supportable_land_value": "supportable_total_project_cost - tdc_excluding_land",
        "feasibility_ratio": "supportable_land_value / acquisition_price",
    }

    return {
        "result": result,
        "warnings": sorted(dict.fromkeys(warnings)),
        "program": {
            "total_units": total_units,
            "affordable_units": affordable_units,
            "market_units": market_units,
            "affordable_share": _ratio(affordable_share),
            "market_share": _ratio(market_share),
            "bedrooms": inputs.bedrooms,
            "affordable_ami_band": inputs.affordable_ami_band,
            "utilities_included": utilities_included,
        },
        "rents": {
            "market_monthly_rent": _money(market_rent),
            "affordable_monthly_rent": _money(affordable_rent),
            "affordable_rent_source": rent_source,
            "gross_rent_limit": _money(gross_rent_limit),
            "utility_allowance": _money(utility_allowance_value),
            "tenant_paid_rent_limit": _money(tenant_paid_rent_limit),
            "rent_limit": affordable_rent_limit or {},
            "utility_allowance_source": utility_allowance or {},
        },
        "income": {
            "gross_income": _money(gross_income),
            "vacancy_loss": _money(vacancy_loss),
            "effective_income": _money(effective_income),
            "opex": _money(opex),
            "noi_before_tax_savings": _money(noi_before_tax_savings),
            "tax_savings": _money(tax_savings),
            "noi": _money(noi),
        },
        "costs": {
            "hard_cost_basis": hard_cost_basis,
            "hard_costs": _money(hard_cost),
            "soft_costs": _money(soft_costs),
            "contingency": _money(contingency),
            "financing_carry": _money(financing_carry),
            "tdc_excluding_land": _money(tdc_excluding_land),
            "supportable_total_project_cost": _money(supportable_total_project_cost),
            "supportable_land_value": _money(supportable_land_value),
        },
        "metrics": {
            "acquisition_price": _money(acquisition_price),
            "feasibility_ratio": _ratio(feasibility_ratio),
            "noi_per_unit": _money(noi / per_unit) if per_unit else None,
            "hard_cost_per_unit": _money(hard_cost / per_unit) if hard_cost is not None and per_unit else None,
            "tdc_excluding_land_per_unit": _money(tdc_excluding_land / per_unit)
            if tdc_excluding_land is not None and per_unit
            else None,
            "supportable_land_value_per_unit": _money(supportable_land_value / per_unit)
            if supportable_land_value is not None and per_unit
            else None,
        },
        "tax_exemption": tax_exemption or {},
        "formulas": formulas,
    }

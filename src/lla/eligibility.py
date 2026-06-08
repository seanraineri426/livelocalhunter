"""v0 Live Local Act eligibility gates."""

from __future__ import annotations

from typing import Any

from lla.use_crosswalk import CONFIDENCE_ORDER, categorize_land_use


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    if hasattr(row, "_mapping"):
        return row._mapping.get(key, default)
    return getattr(row, key, default)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().upper() in {"1", "T", "TRUE", "Y", "YES"}


def _lowest_confidence(values: list[str]) -> str:
    return min(values, key=lambda value: CONFIDENCE_ORDER.get(value, 0))


def eligibility(parcel_row: Any) -> dict[str, Any]:
    """Evaluate v0 eligibility gates for a parcel-like row.

    The spatial excluded-area gate is computed by the caller in PostGIS and passed
    as ``intersects_excluded_area`` plus ``excluded_area_count``.
    """

    failed_reasons: list[str] = []
    confidence_values: list[str] = []

    land_use = categorize_land_use(parcel_row)
    confidence_values.append(land_use.confidence)
    if not land_use.eligible:
        failed_reasons.append("not_lla_land_category")

    excluded_area_count = _get(parcel_row, "excluded_area_count")
    intersects_excluded_area = _bool(_get(parcel_row, "intersects_excluded_area"))

    if intersects_excluded_area:
        failed_reasons.append("intersects_excluded_area")
        confidence_values.append("high")
    elif not failed_reasons and (excluded_area_count is None or int(excluded_area_count) == 0):
        confidence_values.append("low")

    return {
        "eligible": not failed_reasons,
        "failed_reasons": failed_reasons,
        "confidence": _lowest_confidence(confidence_values or ["low"]),
    }

"""Market rent source provenance helpers."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from lla.db import get_engine


VALID_SOURCE_TYPES = {"costar", "broker", "internal", "manual", "other"}
VALID_CONFIDENCE = {"low", "medium", "high"}


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _money(value: Any) -> float | None:
    decimal = _decimal(value)
    return float(decimal) if decimal is not None else None


def validate_market_rent_source(source: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a market rent source payload."""

    normalized = dict(source)
    source_type = normalized.get("source_type")
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(f"source_type must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}")

    market_rent = _decimal(normalized.get("market_rent_monthly"))
    if market_rent is None or market_rent < 0:
        raise ValueError("market_rent_monthly must be a non-negative number")
    normalized["market_rent_monthly"] = float(market_rent)

    bedroom_count = normalized.get("bedroom_count")
    if bedroom_count is not None:
        bedroom_count = int(bedroom_count)
        if bedroom_count < 0 or bedroom_count > 8:
            raise ValueError("bedroom_count must be between 0 and 8")
        normalized["bedroom_count"] = bedroom_count

    vacancy_rate = _decimal(normalized.get("vacancy_rate"))
    if vacancy_rate is not None:
        if vacancy_rate < 0 or vacancy_rate > 1:
            raise ValueError("vacancy_rate must be between 0 and 1")
        normalized["vacancy_rate"] = float(vacancy_rate)

    confidence = normalized.get("confidence")
    if confidence is not None and confidence not in VALID_CONFIDENCE:
        raise ValueError(f"confidence must be one of: {', '.join(sorted(VALID_CONFIDENCE))}")

    return normalized


def latest_market_rent_source(
    *,
    parcel_id: str,
    bedroom_count: int | None = None,
    conn: Connection | None = None,
    engine: Engine | None = None,
) -> dict[str, Any] | None:
    """Return the latest parcel-specific market rent source when present."""

    owns_connection = conn is None
    if conn is None:
        engine = engine or get_engine()
        conn = engine.connect()

    try:
        if conn.execute(text("SELECT to_regclass('lla.market_rent_sources')")).scalar() is None:
            return None
        row = conn.execute(
            text(
                """
                SELECT
                    market_rent_source_id::text,
                    parcel_id::text,
                    county_fips,
                    source_type,
                    report_name,
                    report_date::text AS report_date,
                    submarket,
                    bedroom_count,
                    market_rent_monthly,
                    rent_psf,
                    vacancy_rate,
                    concessions_notes,
                    confidence,
                    notes,
                    source_file_ref,
                    created_by,
                    created_at,
                    updated_at
                FROM lla.market_rent_sources
                WHERE parcel_id = CAST(:parcel_id AS uuid)
                  AND (:bedroom_count IS NULL OR bedroom_count = :bedroom_count)
                ORDER BY report_date DESC NULLS LAST, updated_at DESC
                LIMIT 1
                """
            ),
            {"parcel_id": parcel_id, "bedroom_count": bedroom_count},
        ).mappings().first()
    finally:
        if owns_connection:
            conn.close()

    if not row:
        return None
    result = dict(row)
    result["market_rent_monthly"] = _money(result.get("market_rent_monthly"))
    result["rent_psf"] = _money(result.get("rent_psf"))
    vacancy_rate = _decimal(result.get("vacancy_rate"))
    result["vacancy_rate"] = float(vacancy_rate) if vacancy_rate is not None else None
    return result

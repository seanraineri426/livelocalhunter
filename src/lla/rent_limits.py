"""Bedroom-specific affordable rent limit lookup.

This module reads stored source-backed rows from Supabase. It does not crawl or
derive rent limits at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from lla.db import get_engine


@dataclass(frozen=True)
class RentLimitResult:
    county_fips: str
    year: int
    ami_band: int
    bedroom_count: int
    max_monthly_rent: Decimal | None
    source: str | None
    source_url: str | None
    effective_date: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class UtilityAllowanceResult:
    county_fips: str
    year: int
    bedroom_count: int
    allowance_monthly: Decimal | None
    jurisdiction_name: str | None
    pha_name: str | None
    source_area: str | None
    unit_type: str | None
    utility_profile: str | None
    confidence: str | None
    source: str | None
    source_url: str | None
    effective_date: str | None
    warnings: tuple[str, ...]


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def lookup_rent_limit(
    *,
    county_fips: str,
    year: int,
    ami_band: int,
    bedroom_count: int,
    conn: Connection | None = None,
    engine: Engine | None = None,
) -> RentLimitResult:
    """Return the best stored rent limit row plus warnings.

    The lookup prefers an exact year. If none exists, it falls back to the latest
    earlier year for the same county, AMI band, and bedroom count.
    """

    if bedroom_count < 0:
        raise ValueError("bedroom_count must be non-negative")
    if ami_band <= 0:
        raise ValueError("ami_band must be positive")

    owns_connection = conn is None
    if conn is None:
        engine = engine or get_engine()
        conn = engine.connect()

    try:
        row = conn.execute(
            text(
                """
                SELECT
                    county_fips,
                    year,
                    ami_band,
                    bedroom_count,
                    max_monthly_rent,
                    source,
                    source_url,
                    effective_date::text AS effective_date
                FROM lla.rent_limits
                WHERE county_fips = :county_fips
                  AND year <= :year
                  AND ami_band = :ami_band
                  AND bedroom_count = :bedroom_count
                ORDER BY year DESC
                LIMIT 1
                """
            ),
            {
                "county_fips": county_fips,
                "year": year,
                "ami_band": ami_band,
                "bedroom_count": bedroom_count,
            },
        ).mappings().first()
    finally:
        if owns_connection:
            conn.close()

    warnings: list[str] = []
    if not row:
        return RentLimitResult(
            county_fips=county_fips,
            year=year,
            ami_band=ami_band,
            bedroom_count=bedroom_count,
            max_monthly_rent=None,
            source=None,
            source_url=None,
            effective_date=None,
            warnings=("rent_limit_missing",),
        )

    if int(row["year"]) != int(year):
        warnings.append("rent_limit_prior_year_used")

    return RentLimitResult(
        county_fips=str(row["county_fips"]),
        year=int(row["year"]),
        ami_band=int(row["ami_band"]),
        bedroom_count=int(row["bedroom_count"]),
        max_monthly_rent=_decimal(row["max_monthly_rent"]),
        source=row.get("source"),
        source_url=row.get("source_url"),
        effective_date=row.get("effective_date"),
        warnings=tuple(warnings),
    )


def lookup_utility_allowance(
    *,
    county_fips: str,
    year: int,
    bedroom_count: int,
    conn: Connection | None = None,
    engine: Engine | None = None,
) -> UtilityAllowanceResult:
    """Return the best stored utility allowance row plus warnings.

    The lookup prefers an exact year and high-confidence rows. If none exists, it
    falls back to the latest earlier year for the same county and bedroom count.
    """

    if bedroom_count < 0:
        raise ValueError("bedroom_count must be non-negative")

    owns_connection = conn is None
    if conn is None:
        engine = engine or get_engine()
        conn = engine.connect()

    try:
        row = conn.execute(
            text(
                """
                SELECT
                    county_fips,
                    year,
                    bedroom_count,
                    allowance_monthly,
                    jurisdiction_name,
                    pha_name,
                    source_area,
                    unit_type,
                    utility_profile,
                    confidence,
                    source,
                    source_url,
                    effective_date::text AS effective_date
                FROM lla.utility_allowances
                WHERE county_fips = :county_fips
                  AND year <= :year
                  AND bedroom_count = :bedroom_count
                ORDER BY
                    year DESC,
                    CASE confidence
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        ELSE 3
                    END,
                    effective_date DESC NULLS LAST
                LIMIT 1
                """
            ),
            {
                "county_fips": county_fips,
                "year": year,
                "bedroom_count": bedroom_count,
            },
        ).mappings().first()
    finally:
        if owns_connection:
            conn.close()

    warnings: list[str] = []
    if not row:
        return UtilityAllowanceResult(
            county_fips=county_fips,
            year=year,
            bedroom_count=bedroom_count,
            allowance_monthly=None,
            jurisdiction_name=None,
            pha_name=None,
            source_area=None,
            unit_type=None,
            utility_profile=None,
            confidence=None,
            source=None,
            source_url=None,
            effective_date=None,
            warnings=("utility_allowance_missing",),
        )

    if int(row["year"]) != int(year):
        warnings.append("utility_allowance_prior_year_used")
    if row.get("confidence") in {"low", "medium"}:
        warnings.append(f"utility_allowance_confidence_{row.get('confidence')}")
    if row.get("source_area") or row.get("pha_name"):
        warnings.append("utility_allowance_not_parcel_specific")

    return UtilityAllowanceResult(
        county_fips=str(row["county_fips"]),
        year=int(row["year"]),
        bedroom_count=int(row["bedroom_count"]),
        allowance_monthly=_decimal(row["allowance_monthly"]),
        jurisdiction_name=row.get("jurisdiction_name"),
        pha_name=row.get("pha_name"),
        source_area=row.get("source_area"),
        unit_type=row.get("unit_type"),
        utility_profile=row.get("utility_profile"),
        confidence=row.get("confidence"),
        source=row.get("source"),
        source_url=row.get("source_url"),
        effective_date=row.get("effective_date"),
        warnings=tuple(warnings),
    )


def rent_limit_to_dict(result: RentLimitResult) -> dict[str, Any]:
    return {
        "county_fips": result.county_fips,
        "year": result.year,
        "ami_band": result.ami_band,
        "bedroom_count": result.bedroom_count,
        "max_monthly_rent": float(result.max_monthly_rent) if result.max_monthly_rent is not None else None,
        "source": result.source,
        "source_url": result.source_url,
        "effective_date": result.effective_date,
        "warnings": list(result.warnings),
    }


def utility_allowance_to_dict(result: UtilityAllowanceResult) -> dict[str, Any]:
    return {
        "county_fips": result.county_fips,
        "year": result.year,
        "bedroom_count": result.bedroom_count,
        "allowance_monthly": float(result.allowance_monthly) if result.allowance_monthly is not None else None,
        "jurisdiction_name": result.jurisdiction_name,
        "pha_name": result.pha_name,
        "source_area": result.source_area,
        "unit_type": result.unit_type,
        "utility_profile": result.utility_profile,
        "confidence": result.confidence,
        "source": result.source,
        "source_url": result.source_url,
        "effective_date": result.effective_date,
        "warnings": list(result.warnings),
    }

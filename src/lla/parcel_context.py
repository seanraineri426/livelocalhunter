"""Parcel intelligence context packets for Live Local Act chat.

This module is the factual boundary for parcel chat. It assembles stored parcel,
eligibility, massing, zoning, jurisdiction, and exclusion facts into a JSON-safe
payload that an LLM can explain without inventing eligibility or massing values.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from lla.config import COUNTY_FIPS
from lla.db import get_engine
from lla.use_crosswalk import categorize_land_use


COUNTY_LABELS = {value: key for key, value in COUNTY_FIPS.items()}


class ParcelContextError(RuntimeError):
    """Raised when a parcel context packet cannot be assembled."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def to_context_json(context: dict[str, Any], *, indent: int = 2) -> str:
    """Serialize a context packet consistently for CLI/debugging and LLM input."""

    return json.dumps(_jsonable(context), indent=indent, sort_keys=True)


def _county_fips(county: str | None) -> str | None:
    if county is None:
        return None
    return COUNTY_FIPS.get(county, county)


def _fetch_parcel(
    conn: Connection,
    *,
    parcel_id: str | None,
    folio: str | None,
    county: str | None,
) -> dict[str, Any]:
    if parcel_id:
        where = "p.parcel_id = CAST(:parcel_id AS uuid)"
        params: dict[str, Any] = {"parcel_id": parcel_id}
    elif folio:
        where = """
            (
                p.source_parcel_id = :folio
                OR p.source_parcel_id_normalized = :folio
            )
            AND (:county_fips IS NULL OR p.county_fips = :county_fips)
        """
        params = {"folio": folio, "county_fips": _county_fips(county)}
    else:
        raise ParcelContextError("Provide parcel_id or folio.")

    row = conn.execute(
        text(
            f"""
            SELECT
                p.parcel_id::text,
                p.county_fips,
                p.source_parcel_id,
                p.source_parcel_id_normalized,
                p.acreage,
                p.lot_sf,
                ST_Area(p.geom::geography) * 10.76391041671 AS geom_area_sf,
                ST_Y(ST_Centroid(p.geom)) AS centroid_lat,
                ST_X(ST_Centroid(p.geom)) AS centroid_lon,
                p.zoning_code,
                p.use_class,
                p.jurisdiction_id::text,
                p.valid_from,
                p.valid_to,
                p.source,
                p.as_of_date,
                p.is_candidate,
                p.candidate_bucket,
                p.candidate_reason,
                p.normalized_use,
                p.zoning_general_use,
                p.zoning_map_zone,
                p.zoning_map_description,
                p.zoning_map_municipality,
                p.zoning_rescue,
                p.zoning_rescue_source,
                p.zoning_enriched_at,
                p.flu_code,
                p.flu_class,
                p.zoning_rescue_updated_at,
                j.name AS jurisdiction_name,
                j.jurisdiction_type,
                j.gis_name AS jurisdiction_gis_name,
                j.is_unincorporated,
                jp.max_density_du_ac,
                jp.max_far AS jurisdiction_max_far,
                jp.far_2023_snapshot,
                jp.zoning_crosswalk_ref,
                jp.base_parking_per_unit,
                jp.params_version AS jurisdiction_params_version,
                jp.updated_at AS jurisdiction_params_updated_at,
                e.entitlement_id::text,
                e.eligible,
                e.failed_reasons,
                e.max_units,
                e.max_height_stories,
                e.buildable_sf,
                e.required_parking,
                e.statute_version,
                e.params_version AS entitlement_params_version,
                e.confidence AS entitlement_confidence,
                e.computed_at AS entitlement_computed_at,
                e.massing_flags,
                e.massing_inputs
            FROM lla.parcels p
            LEFT JOIN lla.jurisdictions j ON j.jurisdiction_id = p.jurisdiction_id
            LEFT JOIN lla.jurisdiction_params jp ON jp.jurisdiction_id = p.jurisdiction_id
            LEFT JOIN lla.entitlement e ON e.parcel_id = p.parcel_id
            WHERE {where}
            ORDER BY p.as_of_date DESC NULLS LAST, p.updated_at DESC
            LIMIT 1
            """
        ),
        params,
    ).mappings().first()
    if not row:
        raise ParcelContextError("Parcel not found.")
    return dict(row)


def _fetch_matched_zoning(conn: Connection, parcel: dict[str, Any]) -> list[dict[str, Any]]:
    if not parcel.get("jurisdiction_id"):
        return []
    if not parcel.get("zoning_code") and not parcel.get("zoning_map_zone"):
        return []
    rows = conn.execute(
        text(
            """
            SELECT
                z.district_id::text,
                z.district_code,
                z.district_name,
                z.category,
                z.allows_residential,
                z.allows_multifamily,
                z.max_density_du_ac,
                z.max_height_ft,
                z.max_height_stories,
                z.max_far,
                z.min_lot_sf,
                z.max_lot_coverage,
                z.front_setback_ft,
                z.side_setback_ft,
                z.rear_setback_ft,
                z.parking_per_unit,
                z.code_citation,
                z.source_url,
                z.confidence,
                z.extraction_model,
                z.extracted_at,
                CASE
                    WHEN lower(trim(z.district_code)) = lower(trim(coalesce(:zoning_code, ''))) THEN 'parcel.zoning_code'
                    WHEN lower(trim(z.district_code)) = lower(trim(coalesce(:zoning_map_zone, ''))) THEN 'parcel.zoning_map_zone'
                    ELSE 'jurisdiction_context'
                END AS match_source
            FROM lla.zoning_districts z
            WHERE z.jurisdiction_id = CAST(:jurisdiction_id AS uuid)
              AND lower(trim(z.district_code)) IN (
                  lower(trim(coalesce(:zoning_code, ''))),
                  lower(trim(coalesce(:zoning_map_zone, '')))
              )
            ORDER BY
                CASE z.confidence
                    WHEN 'high' THEN 3
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 1
                    ELSE 0
                END DESC,
                z.district_code
            LIMIT 10
            """
        ),
        {
            "jurisdiction_id": parcel["jurisdiction_id"],
            "zoning_code": parcel.get("zoning_code"),
            "zoning_map_zone": parcel.get("zoning_map_zone"),
        },
    ).mappings()
    return [dict(row) for row in rows]


def _fetch_excluded_intersections(conn: Connection, parcel_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT
                e.excluded_area_id::text,
                e.area_type,
                e.county_fips,
                e.name,
                e.source,
                e.source_id,
                e.source_url,
                e.notes,
                ST_Area(ST_Intersection(p.geom, e.geom)::geography) * 10.76391041671 AS intersection_sf
            FROM lla.parcels p
            JOIN lla.excluded_areas e
              ON p.geom && e.geom
             AND ST_Intersects(p.geom, e.geom)
            WHERE p.parcel_id = CAST(:parcel_id AS uuid)
            ORDER BY intersection_sf DESC NULLS LAST, e.area_type, e.name
            LIMIT 20
            """
        ),
        {"parcel_id": parcel_id},
    ).mappings()
    return [dict(row) for row in rows]


def _fetch_latest_scenario(conn: Connection, parcel_id: str) -> dict[str, Any] | None:
    if conn.execute(text("SELECT to_regclass('lla.parcel_scenarios')")).scalar() is None:
        return None
    row = conn.execute(
        text(
            """
            SELECT
                scenario_id::text,
                scenario_name,
                status,
                assumptions_jsonb,
                feasibility_output_jsonb,
                tax_exemption_output_jsonb,
                cost_audit_jsonb,
                created_at,
                updated_at
            FROM lla.parcel_scenarios
            WHERE parcel_id = CAST(:parcel_id AS uuid)
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ),
        {"parcel_id": parcel_id},
    ).mappings().first()
    return dict(row) if row else None


def _scenario_summary(scenario: dict[str, Any] | None) -> dict[str, Any] | None:
    if not scenario:
        return None
    feasibility = scenario.get("feasibility_output_jsonb") or {}
    return {
        "scenario_id": scenario.get("scenario_id"),
        "scenario_name": scenario.get("scenario_name"),
        "status": scenario.get("status"),
        "result": feasibility.get("result"),
        "updated_at": scenario.get("updated_at"),
        "template_name": (scenario.get("assumptions_jsonb") or {}).get("template_name"),
        "warnings_count": len(feasibility.get("warnings") or []),
        "program": feasibility.get("program") or {},
        "metrics": feasibility.get("metrics") or {},
    }


def _fetch_latest_market_rent_source(conn: Connection, parcel_id: str) -> dict[str, Any] | None:
    if conn.execute(text("SELECT to_regclass('lla.market_rent_sources')")).scalar() is None:
        return None
    row = conn.execute(
        text(
            """
            SELECT
                market_rent_source_id::text,
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
                updated_at
            FROM lla.market_rent_sources
            WHERE parcel_id = CAST(:parcel_id AS uuid)
            ORDER BY report_date DESC NULLS LAST, updated_at DESC
            LIMIT 1
            """
        ),
        {"parcel_id": parcel_id},
    ).mappings().first()
    return dict(row) if row else None


def _data_gaps(parcel: dict[str, Any], zoning: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> list[str]:
    gaps: list[str] = []
    if not parcel.get("jurisdiction_id"):
        gaps.append("jurisdiction_id_missing")
    if not parcel.get("entitlement_id"):
        gaps.append("entitlement_not_computed")
    if parcel.get("eligible") is True and parcel.get("max_units") is None:
        gaps.append("eligible_without_massing")
    if not parcel.get("acreage") and not parcel.get("lot_sf"):
        gaps.append("parcel_area_missing_from_source")
    if not parcel.get("zoning_code") and not parcel.get("zoning_map_zone"):
        gaps.append("zoning_code_missing")
    if (parcel.get("zoning_code") or parcel.get("zoning_map_zone")) and not zoning:
        gaps.append("zoning_district_not_matched")
    if not parcel.get("jurisdiction_params_version"):
        gaps.append("jurisdiction_params_missing")
    if not parcel.get("source_parcel_id_normalized"):
        gaps.append("normalized_folio_missing")
    if parcel.get("massing_flags"):
        gaps.extend(str(flag) for flag in parcel["massing_flags"] if str(flag).endswith("_missing"))
    if excluded and parcel.get("eligible") is True:
        gaps.append("eligible_but_intersects_excluded_area")
    return sorted(dict.fromkeys(gaps))


def _summary_sections(
    parcel: dict[str, Any],
    zoning: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    data_gaps: list[str],
    market_rent_source: dict[str, Any] | None,
) -> dict[str, Any]:
    land_use = categorize_land_use(parcel)
    jurisdiction = parcel.get("jurisdiction_name") or "unknown jurisdiction"
    county = COUNTY_LABELS.get(parcel.get("county_fips"), parcel.get("county_fips"))
    folio = parcel.get("source_parcel_id")
    eligibility_state = (
        "eligible"
        if parcel.get("eligible") is True
        else "ineligible"
        if parcel.get("eligible") is False
        else "not computed"
    )
    flags = list(parcel.get("massing_flags") or [])

    return {
        "identity": (
            f"Parcel {parcel.get('parcel_id')} is folio/source parcel {folio} in "
            f"{county}, {jurisdiction}."
        ),
        "eligibility": {
            "status": eligibility_state,
            "failed_reasons": list(parcel.get("failed_reasons") or []),
            "confidence": parcel.get("entitlement_confidence"),
            "land_use_signal": {
                "eligible": land_use.eligible,
                "category": land_use.category,
                "reason": land_use.reason,
                "confidence": land_use.confidence,
            },
            "excluded_area_intersections": len(excluded),
        },
        "massing": {
            "max_units": parcel.get("max_units"),
            "buildable_sf": parcel.get("buildable_sf"),
            "max_height_stories": parcel.get("max_height_stories"),
            "required_parking": parcel.get("required_parking"),
            "inputs": parcel.get("massing_inputs") or {},
        },
        "flags": flags,
        "data_gaps": data_gaps,
        "source_provenance": {
            "parcel_source": parcel.get("source"),
            "parcel_as_of_date": parcel.get("as_of_date"),
            "entitlement_computed_at": parcel.get("entitlement_computed_at"),
            "statute_version": parcel.get("statute_version"),
            "entitlement_params_version": parcel.get("entitlement_params_version"),
            "jurisdiction_params_version": parcel.get("jurisdiction_params_version"),
            "zoning_crosswalk_ref": parcel.get("zoning_crosswalk_ref"),
            "matched_zoning_sources": [
                {
                    "district_code": row.get("district_code"),
                    "code_citation": row.get("code_citation"),
                    "source_url": row.get("source_url"),
                    "confidence": row.get("confidence"),
                }
                for row in zoning
            ],
            "excluded_area_sources": [
                {
                    "area_type": row.get("area_type"),
                    "name": row.get("name"),
                    "source": row.get("source"),
                    "source_url": row.get("source_url"),
                }
                for row in excluded
            ],
            "latest_market_rent_source": market_rent_source,
        },
    }


def build_parcel_context(
    parcel_id: str | None = None,
    *,
    folio: str | None = None,
    county: str | None = None,
    address: str | None = None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Build a JSON-safe parcel context packet.

    Lookup currently supports the internal ``parcel_id`` or county folio/source
    parcel id. The database does not yet store a parcel address field; passing
    ``address`` raises a clear error instead of performing fuzzy or invented lookup.
    """

    if address:
        raise ParcelContextError("Address lookup is not available because parcels do not store address fields yet.")

    engine = engine or get_engine()
    with engine.connect() as conn:
        parcel = _fetch_parcel(conn, parcel_id=parcel_id, folio=folio, county=county)
        matched_zoning = _fetch_matched_zoning(conn, parcel)
        excluded_intersections = _fetch_excluded_intersections(conn, parcel["parcel_id"])
        latest_scenario = _fetch_latest_scenario(conn, parcel["parcel_id"])
        latest_market_rent_source = _fetch_latest_market_rent_source(conn, parcel["parcel_id"])

    data_gaps = _data_gaps(parcel, matched_zoning, excluded_intersections)
    latest_scenario_summary = _scenario_summary(latest_scenario)
    summary = _summary_sections(parcel, matched_zoning, excluded_intersections, data_gaps, latest_market_rent_source)

    context = {
        "context_version": "parcel-intelligence-v1",
        "parcel": {
            "parcel_id": parcel.get("parcel_id"),
            "county_fips": parcel.get("county_fips"),
            "county": COUNTY_LABELS.get(parcel.get("county_fips"), parcel.get("county_fips")),
            "source_parcel_id": parcel.get("source_parcel_id"),
            "source_parcel_id_normalized": parcel.get("source_parcel_id_normalized"),
            "acreage": parcel.get("acreage"),
            "lot_sf": parcel.get("lot_sf"),
            "geom_area_sf": parcel.get("geom_area_sf"),
            "centroid": {
                "lat": parcel.get("centroid_lat"),
                "lon": parcel.get("centroid_lon"),
            },
            "source": parcel.get("source"),
            "as_of_date": parcel.get("as_of_date"),
        },
        "candidate": {
            "is_candidate": parcel.get("is_candidate"),
            "candidate_bucket": parcel.get("candidate_bucket"),
            "candidate_reason": parcel.get("candidate_reason"),
            "normalized_use": parcel.get("normalized_use"),
            "use_class": parcel.get("use_class"),
        },
        "enrichment": {
            "zoning_code": parcel.get("zoning_code"),
            "zoning_general_use": parcel.get("zoning_general_use"),
            "zoning_map_zone": parcel.get("zoning_map_zone"),
            "zoning_map_description": parcel.get("zoning_map_description"),
            "zoning_map_municipality": parcel.get("zoning_map_municipality"),
            "zoning_rescue": parcel.get("zoning_rescue"),
            "zoning_rescue_source": parcel.get("zoning_rescue_source"),
            "zoning_enriched_at": parcel.get("zoning_enriched_at"),
            "flu_code": parcel.get("flu_code"),
            "flu_class": parcel.get("flu_class"),
            "zoning_rescue_updated_at": parcel.get("zoning_rescue_updated_at"),
        },
        "jurisdiction": {
            "jurisdiction_id": parcel.get("jurisdiction_id"),
            "name": parcel.get("jurisdiction_name"),
            "type": parcel.get("jurisdiction_type"),
            "gis_name": parcel.get("jurisdiction_gis_name"),
            "is_unincorporated": parcel.get("is_unincorporated"),
        },
        "jurisdiction_params": {
            "max_density_du_ac": parcel.get("max_density_du_ac"),
            "max_far": parcel.get("jurisdiction_max_far"),
            "far_2023_snapshot": parcel.get("far_2023_snapshot"),
            "base_parking_per_unit": parcel.get("base_parking_per_unit"),
            "params_version": parcel.get("jurisdiction_params_version"),
            "updated_at": parcel.get("jurisdiction_params_updated_at"),
            "zoning_crosswalk_ref": parcel.get("zoning_crosswalk_ref"),
        },
        "matched_zoning_districts": matched_zoning,
        "excluded_area_intersections": excluded_intersections,
        "entitlement": {
            "entitlement_id": parcel.get("entitlement_id"),
            "eligible": parcel.get("eligible"),
            "failed_reasons": parcel.get("failed_reasons") or [],
            "max_units": parcel.get("max_units"),
            "max_height_stories": parcel.get("max_height_stories"),
            "buildable_sf": parcel.get("buildable_sf"),
            "required_parking": parcel.get("required_parking"),
            "statute_version": parcel.get("statute_version"),
            "params_version": parcel.get("entitlement_params_version"),
            "confidence": parcel.get("entitlement_confidence"),
            "computed_at": parcel.get("entitlement_computed_at"),
            "massing_flags": parcel.get("massing_flags") or [],
            "massing_inputs": parcel.get("massing_inputs") or {},
        },
        "latest_scenario": latest_scenario,
        "latest_scenario_summary": latest_scenario_summary,
        "latest_market_rent_source": latest_market_rent_source,
        "summary": summary,
    }
    return _jsonable(context)

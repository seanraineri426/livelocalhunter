"""Thin FastAPI wrapper around existing Live Local Hunter modules."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text

from lla.config import COUNTY_FIPS
from lla.cost_audit import audit_cost_assumptions
from lla.db import get_engine
from lla.feasibility_defaults import list_scenario_templates
from lla.feasibility_service import compute_parcel_feasibility, json_default, save_scenario
from lla.massing_audit import run_massing_audit
from lla.parcel_chat import ParcelChatError, chat_about_parcel
from lla.parcel_context import ParcelContextError, build_parcel_context


app = FastAPI(title="Live Local Hunter API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class FeasibilityRequest(BaseModel):
    assumptions: dict[str, Any] = Field(default_factory=dict)
    template_name: str | None = None


class CostAuditRequest(BaseModel):
    assumptions: dict[str, Any] = Field(default_factory=dict)
    template_name: str | None = None
    feasibility: dict[str, Any] | None = None


class MassingAuditRequest(BaseModel):
    use_ai: bool = False
    model: str | None = None


class ChatRequest(BaseModel):
    message: str
    scenario: dict[str, Any] | None = None


class ScenarioCreateRequest(BaseModel):
    scenario_name: str = "base"
    assumptions: dict[str, Any] = Field(default_factory=dict)
    template_name: str | None = None
    run_cost_audit: bool = False


class StatusPatchRequest(BaseModel):
    review_status: str
    reviewer: str | None = None
    notes: str | None = None


class NoteCreateRequest(BaseModel):
    note: str
    note_type: str = "general"
    source_url: str | None = None
    created_by: str | None = None


def _county_fips(county: str | None) -> str | None:
    if not county:
        return None
    return COUNTY_FIPS.get(county, county)


def _json_param(payload: dict[str, Any] | None) -> str | None:
    return json.dumps(payload, default=json_default) if payload is not None else None


def _loads_geojson_geometry(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return json.loads(value)


def _parcel_status(row: dict[str, Any]) -> str:
    if row.get("review_status"):
        return row["review_status"]
    if row.get("eligible") is True:
        return "eligible"
    if row.get("eligible") is False:
        return "ineligible"
    return "unknown"


@app.get("/parcels/identify")
def identify_parcel(
    lng: float = Query(ge=-180, le=180),
    lat: float = Query(ge=-90, le=90),
) -> dict[str, Any]:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                WITH clicked_point AS (
                    SELECT ST_SetSRID(ST_MakePoint(:lng, :lat), 4326) AS geom
                )
                SELECT
                    p.parcel_id::text,
                    p.county_fips,
                    p.source_parcel_id,
                    p.source_parcel_id_normalized,
                    p.site_address,
                    p.site_city,
                    p.site_zip,
                    p.lot_sf,
                    p.acreage,
                    p.zoning_code,
                    p.use_class,
                    p.is_candidate,
                    p.candidate_bucket,
                    p.candidate_reason,
                    p.normalized_use,
                    j.name AS jurisdiction,
                    e.eligible,
                    e.failed_reasons,
                    e.max_units,
                    e.confidence,
                    e.massing_flags,
                    rs.review_status,
                    ST_Area(p.geom::geography) * 10.76391041671 AS geom_area_sf
                FROM lla.parcels p
                CROSS JOIN clicked_point pt
                LEFT JOIN lla.jurisdictions j ON j.jurisdiction_id = p.jurisdiction_id
                LEFT JOIN lla.entitlement e ON e.parcel_id = p.parcel_id
                LEFT JOIN lla.parcel_review_status rs ON rs.parcel_id = p.parcel_id
                WHERE p.geom && pt.geom
                  AND (ST_Contains(p.geom, pt.geom) OR ST_Intersects(p.geom, pt.geom))
                  AND (p.valid_to IS NULL OR p.valid_to >= CURRENT_DATE)
                ORDER BY
                    ST_Area(p.geom::geography) ASC NULLS LAST,
                    p.is_candidate DESC NULLS LAST,
                    p.as_of_date DESC NULLS LAST,
                    p.updated_at DESC
                LIMIT 1
                """
            ),
            {"lng": lng, "lat": lat},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="No parcel found at this location.")

    data = dict(row)
    data["status"] = _parcel_status(data)
    return data


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/parcels/search")
def search_parcels(
    folio: str | None = Query(default=None),
    county: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    if not folio and not county:
        raise HTTPException(status_code=400, detail="Provide folio and/or county.")
    where = ["(:county_fips IS NULL OR p.county_fips = :county_fips)"]
    if folio:
        where.append("(p.source_parcel_id ILIKE :folio_like OR p.source_parcel_id_normalized ILIKE :folio_like)")
    query = f"""
        SELECT
            p.parcel_id::text,
            p.county_fips,
            p.source_parcel_id,
            p.source_parcel_id_normalized,
            p.site_address,
            p.site_city,
            p.site_zip,
            p.lot_sf,
            p.acreage,
            p.zoning_code,
            p.use_class,
            p.is_candidate,
            p.candidate_bucket,
            p.candidate_reason,
            p.normalized_use,
            e.eligible,
            e.failed_reasons,
            e.max_units,
            e.confidence,
            e.massing_flags
        FROM lla.parcels p
        LEFT JOIN lla.entitlement e ON e.parcel_id = p.parcel_id
        WHERE {' AND '.join(where)}
        ORDER BY p.as_of_date DESC NULLS LAST, p.updated_at DESC
        LIMIT :limit
    """
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(query),
            {
                "county_fips": _county_fips(county),
                "folio_like": f"%{folio}%" if folio else None,
                "limit": limit,
            },
        ).mappings()
        return {"results": [dict(row) for row in rows]}


@app.get("/parcels/{parcel_id}/geometry")
def parcel_geometry(parcel_id: str) -> dict[str, Any]:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    p.parcel_id::text,
                    p.source_parcel_id AS folio,
                    p.site_address,
                    p.site_city,
                    p.site_zip,
                    p.is_candidate,
                    p.candidate_bucket,
                    e.eligible,
                    rs.review_status,
                    CASE
                        WHEN p.geom IS NULL OR ST_IsEmpty(p.geom) THEN NULL
                        WHEN ST_SRID(p.geom) = 4326 THEN ST_AsGeoJSON(p.geom)
                        WHEN ST_SRID(p.geom) = 0 THEN ST_AsGeoJSON(ST_SetSRID(p.geom, 4326))
                        ELSE ST_AsGeoJSON(ST_Transform(p.geom, 4326))
                    END AS geometry
                FROM lla.parcels p
                LEFT JOIN lla.entitlement e ON e.parcel_id = p.parcel_id
                LEFT JOIN lla.parcel_review_status rs ON rs.parcel_id = p.parcel_id
                WHERE p.parcel_id = CAST(:parcel_id AS uuid)
                ORDER BY p.as_of_date DESC NULLS LAST, p.updated_at DESC
                LIMIT 1
                """
            ),
            {"parcel_id": parcel_id},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Parcel not found.")

    data = dict(row)
    return {
        "type": "Feature",
        "geometry": _loads_geojson_geometry(data.pop("geometry")),
        "properties": {
            "parcel_id": data["parcel_id"],
            "folio": data.get("folio"),
            "eligible": data.get("eligible"),
            "status": _parcel_status(data),
            "address": data.get("site_address"),
            "city": data.get("site_city"),
            "zip": data.get("site_zip"),
            "is_candidate": data.get("is_candidate"),
            "candidate_bucket": data.get("candidate_bucket"),
        },
    }


@app.get("/parcels/{parcel_id}/context")
def parcel_context(parcel_id: str) -> dict[str, Any]:
    try:
        return build_parcel_context(parcel_id=parcel_id)
    except ParcelContextError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/parcels/{parcel_id}/massing-audit")
def parcel_massing_audit(
    parcel_id: str,
    use_ai: bool = Query(default=False),
    model: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        context = build_parcel_context(parcel_id=parcel_id)
        return {"parcel_id": parcel_id, "massing_audit": run_massing_audit(context, use_ai=use_ai, model=model)}
    except ParcelContextError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/parcels/{parcel_id}/massing-audit")
def post_parcel_massing_audit(parcel_id: str, request: MassingAuditRequest) -> dict[str, Any]:
    try:
        context = build_parcel_context(parcel_id=parcel_id)
        return {
            "parcel_id": parcel_id,
            "massing_audit": run_massing_audit(context, use_ai=request.use_ai, model=request.model),
        }
    except ParcelContextError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/scenario-templates")
def scenario_templates() -> dict[str, Any]:
    return {"templates": list_scenario_templates()}


@app.post("/parcels/{parcel_id}/feasibility")
def run_feasibility(parcel_id: str, request: FeasibilityRequest) -> dict[str, Any]:
    try:
        return compute_parcel_feasibility(
            parcel_id=parcel_id,
            assumptions=request.assumptions,
            template_name=request.template_name,
        )
    except (ParcelContextError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/parcels/{parcel_id}/cost-audit")
def run_cost_audit(parcel_id: str, request: CostAuditRequest) -> dict[str, Any]:
    try:
        if request.feasibility is None:
            result = compute_parcel_feasibility(
                parcel_id=parcel_id,
                assumptions=request.assumptions,
                template_name=request.template_name,
            )
            feasibility = result["feasibility"]
            context = build_parcel_context(parcel_id=parcel_id)
            assumptions = result["assumptions"]
        else:
            feasibility = request.feasibility
            context = build_parcel_context(parcel_id=parcel_id)
            assumptions = request.assumptions
        return {
            "parcel_id": parcel_id,
            "cost_audit": audit_cost_assumptions(
                parcel_context=context,
                assumptions=assumptions,
                feasibility_output=feasibility,
            ),
        }
    except (ParcelContextError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/parcels/{parcel_id}/chat")
def parcel_chat(parcel_id: str, request: ChatRequest) -> dict[str, Any]:
    message = request.message
    if request.scenario:
        message = f"{message}\n\nScenario JSON:\n{json.dumps(request.scenario, default=json_default, sort_keys=True)}"
    try:
        return chat_about_parcel(parcel_id=parcel_id, messages=[{"role": "user", "content": message}])
    except (ParcelChatError, ParcelContextError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/parcels/{parcel_id}/scenarios")
def list_scenarios(parcel_id: str) -> dict[str, Any]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    scenario_id::text,
                    parcel_id::text,
                    scenario_name,
                    status,
                    assumptions_jsonb,
                    feasibility_output_jsonb,
                    tax_exemption_output_jsonb,
                    cost_audit_jsonb,
                    created_by,
                    created_at,
                    updated_at
                FROM lla.parcel_scenarios
                WHERE parcel_id = CAST(:parcel_id AS uuid)
                ORDER BY updated_at DESC
                """
            ),
            {"parcel_id": parcel_id},
        ).mappings()
        return {"scenarios": [dict(row) for row in rows]}


@app.post("/parcels/{parcel_id}/scenarios")
def create_scenario(parcel_id: str, request: ScenarioCreateRequest) -> dict[str, Any]:
    try:
        result = compute_parcel_feasibility(
            parcel_id=parcel_id,
            assumptions=request.assumptions,
            template_name=request.template_name,
            run_cost_audit=request.run_cost_audit,
        )
        scenario_id = save_scenario(
            parcel_id=parcel_id,
            scenario_name=request.scenario_name,
            assumptions=result["assumptions"],
            feasibility_output=result["feasibility"],
            tax_exemption_output=result["tax_exemption"],
            cost_audit=result["cost_audit"],
        )
        return {"scenario_id": scenario_id, **result}
    except (ParcelContextError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/parcels/{parcel_id}/status")
def patch_status(parcel_id: str, request: StatusPatchRequest) -> dict[str, Any]:
    if request.review_status not in {"unreviewed", "needs_review", "watch", "pursue", "fail"}:
        raise HTTPException(status_code=400, detail="Unsupported review_status.")
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO lla.parcel_review_status (parcel_id, review_status, reviewer, reviewed_at, notes, updated_at)
                VALUES (CAST(:parcel_id AS uuid), :review_status, :reviewer, now(), :notes, now())
                ON CONFLICT (parcel_id) DO UPDATE
                SET
                    review_status = EXCLUDED.review_status,
                    reviewer = EXCLUDED.reviewer,
                    reviewed_at = now(),
                    notes = EXCLUDED.notes,
                    updated_at = now()
                RETURNING parcel_id::text, review_status, reviewer, reviewed_at, notes, updated_at
                """
            ),
            {
                "parcel_id": parcel_id,
                "review_status": request.review_status,
                "reviewer": request.reviewer,
                "notes": request.notes,
            },
        ).mappings().one()
    return {"status": dict(row)}


@app.post("/parcels/{parcel_id}/notes")
def create_note(parcel_id: str, request: NoteCreateRequest) -> dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO lla.parcel_notes (parcel_id, note_type, note, source_url, created_by)
                VALUES (CAST(:parcel_id AS uuid), :note_type, :note, :source_url, :created_by)
                RETURNING note_id::text, parcel_id::text, note_type, note, source_url, created_by, created_at
                """
            ),
            {
                "parcel_id": parcel_id,
                "note_type": request.note_type,
                "note": request.note,
                "source_url": request.source_url,
                "created_by": request.created_by,
            },
        ).mappings().one()
    return {"note": dict(row)}

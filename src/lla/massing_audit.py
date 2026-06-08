"""Deterministic and optional AI review of stored Live Local massing output.

The calculator remains the source of truth. This module only reviews the stored
parcel context and massing audit trail for reasonableness, ambiguity, and human
review triggers.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from lla.config import get_env, require_env
from lla.parcel_context import to_context_json


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"

SYSTEM_PROMPT = """You are an advisory reviewer for Live Local Act zoning and massing screening.

Architecture and boundaries:
- Legal/zoning sources are extracted into structured constraints.
- A deterministic geometric solver calculates massing options and final max_units.
- Deterministic sanity heuristics flag obvious errors and ambiguity.
- You explain ambiguity and review needs only.

Return strict JSON only with keys: status, summary, findings, human_review_items, caveats.
Do not recalculate units, FAR, density, parking, height, or eligibility. Do not invent zoning,
legal facts, parcel boundaries, missing code rules, or source citations. Treat the supplied
parcel context and deterministic audit as the only facts. Cite context fields and deterministic
flag ids when explaining. Say unknown when a fact is missing. The deterministic calculator
output remains canonical unless a human updates the source data or formulas."""

REVIEW_MASSING_FLAGS = {
    "height_within_1mi_uses_jurisdiction_rollup": (
        "height_rollup_used",
        "Height uses jurisdiction rollup",
        "The height input was not parcel-specific and should be verified against the statutory one-mile height rule.",
        "Verify nearby tallest eligible building height and any single-family adjacency cap.",
    ),
    "historic_height_screen_missing": (
        "historic_height_missing",
        "Historic height screen missing",
        "The calculator did not have historic height data available for the height comparison.",
        "Have staff or counsel verify applicable height history before relying on this result.",
    ),
    "subject_zoning_height_not_matched": (
        "subject_zoning_height_not_matched",
        "Subject zoning height not matched",
        "The subject zoning district was not matched for parcel-specific height, lot coverage, or setback inputs.",
        "Match the parcel zoning district to structured zoning constraints.",
    ),
    "parcel_zoning_unmatched_review_required": (
        "subject_zoning_unmatched",
        "Subject zoning unmatched",
        "The parcel has a zoning signal but no matched zoning district in structured zoning data.",
        "Verify zoning code, zoning map zone, and crosswalk coverage.",
    ),
    "parcel_zoning_qualification_unverified": (
        "zoning_qualification_unverified",
        "Zoning qualification unverified",
        "The audit trail could not verify Live Local zoning qualification from a matched subject zoning district.",
        "Confirm the subject zoning allows commercial, industrial, or mixed-use qualification.",
    ),
    "manual_site_boundary_required": (
        "manual_site_boundary_required",
        "Manual site boundary required",
        "The stored parcel geometry may represent an aggregate tract rather than a developable site.",
        "Define the actual development site before relying on acreage, FAR, footprint, or unit counts.",
    ),
    "oversized_parcel_review_required": (
        "oversized_parcel_review_required",
        "Oversized parcel review required",
        "The parcel exceeds the aggregate-tract threshold used by the massing engine.",
        "Confirm this is a true single development site or split out the intended project boundary.",
    ),
    "surface_parking_may_not_fit_structured_parking_likely": (
        "surface_parking_may_not_fit",
        "Surface parking may not fit",
        "Estimated surface parking area exceeds residual open area after the building footprint.",
        "Test structured parking, shared parking, reductions, or a different footprint assumption.",
    ),
}


def _num(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value: Any) -> str:
    return str(value or "").lower()


def _flag(
    flags: list[dict[str, Any]],
    *,
    flag_id: str,
    severity: str,
    category: str,
    title: str,
    explanation: str,
    recommended_action: str,
    confidence: str = "high",
    related_fields: list[str] | None = None,
) -> None:
    if any(existing["id"] == flag_id for existing in flags):
        return
    flags.append(
        {
            "id": flag_id,
            "severity": severity,
            "category": category,
            "title": title,
            "explanation": explanation,
            "recommended_action": recommended_action,
            "confidence": confidence,
            "related_fields": related_fields or [],
        }
    )


def _status(flags: list[dict[str, Any]], *, eligible: bool | None) -> str:
    if eligible is False:
        return "not_applicable"
    severities = {flag["severity"] for flag in flags}
    bad_input_categories = {"missing_massing", "impossible_output", "formula_consistency"}
    if any(flag["category"] in bad_input_categories and flag["severity"] == "high" for flag in flags):
        return "likely_bad_input"
    if severities.intersection({"high", "medium"}):
        return "review"
    return "ok"


def _bucket_ids(flags: list[dict[str, Any]], *, category: str | None = None) -> list[str]:
    selected = [flag["id"] for flag in flags if category is None or flag["category"] == category]
    return list(dict.fromkeys(selected))


def _summary(status: str, flags: list[dict[str, Any]], context: dict[str, Any]) -> str:
    entitlement = context.get("entitlement") or {}
    parcel = context.get("parcel") or {}
    if status == "not_applicable":
        reasons = entitlement.get("failed_reasons") or []
        reason_text = f" because eligibility failed: {', '.join(map(str, reasons[:3]))}" if reasons else " because the parcel is not eligible"
        return f"Massing audit is not applicable{reason_text}."
    if not flags:
        return "Stored massing output passes the deterministic reasonableness checks."
    high_count = sum(1 for flag in flags if flag["severity"] == "high")
    medium_count = sum(1 for flag in flags if flag["severity"] == "medium")
    units = entitlement.get("max_units")
    acres = parcel.get("acreage")
    return (
        f"Stored massing needs review: {high_count} high and {medium_count} medium deterministic flags "
        f"for {units if units is not None else 'unknown'} units on {acres if acres is not None else 'unknown'} acres."
    )


def deterministic_massing_audit(context: dict[str, Any]) -> dict[str, Any]:
    """Review stored parcel context and massing output with deterministic checks."""

    parcel = context.get("parcel") or {}
    entitlement = context.get("entitlement") or {}
    summary = context.get("summary") or {}
    candidate = context.get("candidate") or {}
    enrichment = context.get("enrichment") or {}
    jurisdiction_params = context.get("jurisdiction_params") or {}
    matched_zoning = context.get("matched_zoning_districts") or []
    inputs = entitlement.get("massing_inputs") or {}
    massing_flags = list(entitlement.get("massing_flags") or [])
    data_gaps = list((summary.get("data_gaps") or []))
    flags: list[dict[str, Any]] = []

    eligible = entitlement.get("eligible")
    if eligible is False:
        _flag(
            flags,
            flag_id="massing_not_applicable_ineligible",
            severity="info",
            category="eligibility",
            title="Massing not applicable",
            explanation="The stored eligibility result is false, so unit-count sanity checks are not applied.",
            recommended_action="Review failed_reasons if the eligibility result is unexpected.",
            related_fields=["entitlement.eligible", "entitlement.failed_reasons"],
        )
        status = "not_applicable"
        return {
            "summary": _summary(status, flags, context),
            "sanity_status": status,
            "flags": flags,
            "buckets": {
                "deterministic": _bucket_ids(flags),
                "ai_assisted": [],
                "human_required": [],
            },
        }

    max_units = _num(entitlement.get("max_units"))
    acreage = _num(parcel.get("acreage")) or ((_num(parcel.get("lot_sf")) or Decimal("0")) / Decimal("43560"))
    lot_sf = _num(parcel.get("lot_sf")) or (acreage * Decimal("43560") if acreage else None)
    height_stories = _num(entitlement.get("max_height_stories")) or _num(inputs.get("max_height_stories"))
    far = _num(jurisdiction_params.get("max_far")) or _num(inputs.get("far"))
    parking = _num(entitlement.get("required_parking"))
    binding = inputs.get("binding_constraint")
    density_limited = _num(inputs.get("density_limited_units"))
    far_limited = _num(inputs.get("far_limited_units"))
    envelope_limited = _num(inputs.get("envelope_limited_units"))
    footprint_sf = _num(inputs.get("footprint_sf"))
    surface_parking_sf = _num(inputs.get("surface_parking_sf_estimate"))
    land_category = _text(inputs.get("land_category") or candidate.get("candidate_bucket") or candidate.get("normalized_use"))
    zoning_category = _text(((inputs.get("subject_zoning") or {}).get("category")))
    zoning_general_use = _text(enrichment.get("zoning_general_use") or enrichment.get("zoning_map_description"))
    zoning_confidence = _text((inputs.get("subject_zoning") or {}).get("confidence") or inputs.get("parcel_zoning_confidence"))
    zoning_matched = bool((inputs.get("subject_zoning") or {}).get("matched")) or bool(matched_zoning)
    units_per_acre = (max_units / acreage) if max_units is not None and acreage and acreage > 0 else None

    if eligible is True and max_units is None:
        _flag(
            flags,
            flag_id="eligible_missing_massing",
            severity="high",
            category="missing_massing",
            title="Eligible parcel has no massing output",
            explanation="The parcel is marked eligible but max_units is missing from the entitlement record.",
            recommended_action="Run or inspect the deterministic massing job before using this parcel for screening.",
            related_fields=["entitlement.eligible", "entitlement.max_units"],
        )

    if max_units is not None and max_units <= 0 and eligible is True:
        _flag(
            flags,
            flag_id="eligible_nonpositive_units",
            severity="high",
            category="impossible_output",
            title="Eligible parcel has non-positive units",
            explanation="An eligible parcel should not generally produce zero or negative max_units unless a source input is broken or the site is physically unusable.",
            recommended_action="Inspect acreage, FAR, footprint, height, and zoning inputs before relying on this result.",
            related_fields=["entitlement.eligible", "entitlement.max_units", "entitlement.massing_inputs"],
        )

    if acreage and acreage > Decimal("50"):
        _flag(
            flags,
            flag_id="manual_site_boundary_required",
            severity="high",
            category="human_review",
            title="Manual site boundary required",
            explanation="The parcel is larger than 50 acres and may be an aggregate tract, campus, golf course, or government parcel rather than one developable site.",
            recommended_action="Define the actual project boundary and rerun deterministic massing on that site.",
            related_fields=["parcel.acreage", "entitlement.massing_flags"],
        )

    if max_units is not None and max_units > Decimal("5000"):
        oversized = acreage is not None and acreage > Decimal("50")
        _flag(
            flags,
            flag_id="implausibly_large_unit_count",
            severity="high",
            category="scale",
            title="Implausibly large unit count",
            explanation=(
                "The stored max_units exceeds 5,000. That is usually a bad parcel boundary or zoning extraction for a single project"
                + ("; this parcel is also oversized, which points to an aggregate tract review." if oversized else ".")
            ),
            recommended_action="Confirm whether this is a true master-plan tract; otherwise split the developable site boundary and rerun massing.",
            related_fields=["entitlement.max_units", "parcel.acreage", "entitlement.massing_inputs.binding_constraint"],
        )

    if units_per_acre is not None and max_units is not None:
        if units_per_acre > Decimal("500") and (height_stories is None or height_stories <= Decimal("10")):
            _flag(
                flags,
                flag_id="units_per_acre_too_high_for_height",
                severity="high",
                category="scale",
                title="Units per acre too high for height",
                explanation="The unit density is extremely high relative to the stored height assumption.",
                recommended_action="Check acreage, height, FAR, unit size, and parcel boundary before relying on this output.",
                related_fields=["entitlement.max_units", "parcel.acreage", "entitlement.max_height_stories"],
            )
        elif units_per_acre > Decimal("250") and far is not None and far <= Decimal("3"):
            _flag(
                flags,
                flag_id="units_per_acre_high_for_far",
                severity="medium",
                category="scale",
                title="Units per acre high for FAR",
                explanation="The unit density is high for the stored FAR assumption, which may indicate a small unit-size or parcel-area issue.",
                recommended_action="Review FAR, buildable square feet, average unit size, and lot area inputs.",
                related_fields=["jurisdiction_params.max_far", "entitlement.max_units", "parcel.acreage"],
            )

    if eligible is True and max_units is not None and acreage and acreage > 0 and units_per_acre is not None:
        low_density = units_per_acre < Decimal("2")
        live_local_like = any(token in f"{land_category} {zoning_category} {zoning_general_use}" for token in ("commercial", "mixed", "industrial"))
        sf_or_ag = any(token in f"{land_category} {zoning_category} {zoning_general_use}" for token in ("single", "agric", "rural"))
        if low_density and live_local_like and not sf_or_ag:
            _flag(
                flags,
                flag_id="suspiciously_low_density_live_local",
                severity="medium",
                category="source_extraction",
                title="Suspiciously low density for Live Local parcel",
                explanation="The output is below 2 du/ac on a parcel with commercial, industrial, or mixed-use signals. That can happen from bad acreage, single-family/agricultural zoning, or extracted density like 0.67 du/ac being applied incorrectly.",
                recommended_action="Verify the subject zoning category and the density source before using this parcel as a low-yield candidate.",
                related_fields=["entitlement.max_units", "parcel.acreage", "entitlement.massing_inputs.land_category"],
            )

    if eligible is True and max_units is not None and not binding:
        _flag(
            flags,
            flag_id="binding_constraint_missing",
            severity="medium",
            category="missing_massing",
            title="Binding constraint missing",
            explanation="The massing output is present but does not identify whether density, FAR, or footprint-height is binding.",
            recommended_action="Inspect the massing_inputs payload or rerun the massing job that records binding_constraint.",
            related_fields=["entitlement.massing_inputs.binding_constraint"],
        )

    if eligible is True and max_units is not None and not zoning_matched and (enrichment.get("zoning_code") or enrichment.get("zoning_map_zone")):
        _flag(
            flags,
            flag_id="massing_with_unmatched_subject_zoning",
            severity="high",
            category="zoning_ambiguity",
            title="Massing exists with unmatched subject zoning",
            explanation="The parcel has zoning identifiers but no matched structured subject zoning district, so geometry defaults or jurisdiction rollups may be driving the result.",
            recommended_action="Match the subject zoning district before treating the unit count as reliable.",
            related_fields=["enrichment.zoning_code", "enrichment.zoning_map_zone", "entitlement.massing_inputs.subject_zoning"],
        )

    if eligible is True and max_units is not None and zoning_confidence == "low":
        _flag(
            flags,
            flag_id="low_confidence_zoning_match",
            severity="medium",
            category="zoning_ambiguity",
            title="Low-confidence zoning signal",
            explanation="The massing output depends on a low-confidence zoning signal.",
            recommended_action="Verify zoning map, zoning district text, and structured extraction confidence.",
            related_fields=["entitlement.massing_inputs.parcel_zoning_confidence", "matched_zoning_districts"],
        )

    candidates = {
        "density": density_limited,
        "far": far_limited,
        "footprint_height": envelope_limited,
    }
    known_candidates = [value for value in candidates.values() if value is not None]
    if max_units is not None and known_candidates and max_units > min(known_candidates):
        _flag(
            flags,
            flag_id="max_units_exceeds_recorded_constraints",
            severity="high",
            category="formula_consistency",
            title="Max units exceeds recorded constraints",
            explanation="Stored max_units is greater than the minimum of density, FAR, and footprint-height candidate limits.",
            recommended_action="Treat this output as bad input until massing_inputs and max_units are reconciled.",
            related_fields=["entitlement.max_units", "entitlement.massing_inputs.density_limited_units", "entitlement.massing_inputs.far_limited_units", "entitlement.massing_inputs.envelope_limited_units"],
        )

    if density_limited and max_units is not None and density_limited > max(max_units * Decimal("5"), Decimal("5000")) and binding in {"far", "footprint_height"}:
        _flag(
            flags,
            flag_id="density_ceiling_not_binding",
            severity="info",
            category="constraint_explanation",
            title="Density ceiling is not binding",
            explanation="The density-only unit count is much larger than final max_units, but the recorded binding constraint is FAR or footprint-height.",
            recommended_action="Explain the lower final count using the envelope/FAR limits rather than changing the density assumption.",
            confidence="high",
            related_fields=["entitlement.massing_inputs.density_limited_units", "entitlement.massing_inputs.binding_constraint"],
        )

    if far_limited and envelope_limited and far_limited > 0 and envelope_limited > 0:
        ratio = max(far_limited, envelope_limited) / min(far_limited, envelope_limited)
        if ratio >= Decimal("5"):
            _flag(
                flags,
                flag_id="far_envelope_candidate_mismatch",
                severity="medium",
                category="assumption_mismatch",
                title="FAR and footprint-height candidates diverge",
                explanation="The FAR-limited and footprint-height-limited unit counts differ by at least 5x, which can signal missing coverage, setback, or height assumptions.",
                recommended_action="Review lot coverage, setbacks, height, and FAR extraction before accepting the binding constraint.",
                related_fields=["entitlement.massing_inputs.far_limited_units", "entitlement.massing_inputs.envelope_limited_units"],
            )

    if surface_parking_sf and lot_sf and parking and max_units is not None:
        if footprint_sf is not None:
            open_area_sf = max(lot_sf - footprint_sf, Decimal("0"))
            parking_ratio_to_open = surface_parking_sf / open_area_sf if open_area_sf > 0 else Decimal("999")
            if parking_ratio_to_open > Decimal("1"):
                severity = "high" if max_units >= Decimal("100") else "medium"
                _flag(
                    flags,
                    flag_id="surface_parking_may_not_fit",
                    severity=severity,
                    category="parking",
                    title="Surface parking may not fit",
                    explanation="Estimated surface parking area exceeds residual open area after the building footprint.",
                    recommended_action="Model structured parking, parking reductions, shared parking, or an alternative footprint.",
                    related_fields=["entitlement.required_parking", "entitlement.massing_inputs.surface_parking_sf_estimate", "entitlement.massing_inputs.footprint_sf"],
                )

    for source_flag in massing_flags:
        if source_flag in REVIEW_MASSING_FLAGS:
            flag_id, title, explanation, action = REVIEW_MASSING_FLAGS[source_flag]
            severity = "high" if source_flag in {"manual_site_boundary_required", "oversized_parcel_review_required", "parcel_zoning_unmatched_review_required", "parcel_zoning_qualification_unverified"} else "medium"
            category = "human_review" if source_flag in {"manual_site_boundary_required", "oversized_parcel_review_required", "height_within_1mi_uses_jurisdiction_rollup", "historic_height_screen_missing"} else "zoning_ambiguity"
            if source_flag == "surface_parking_may_not_fit_structured_parking_likely":
                category = "parking"
            _flag(
                flags,
                flag_id=flag_id,
                severity=severity,
                category=category,
                title=title,
                explanation=explanation,
                recommended_action=action,
                related_fields=["entitlement.massing_flags", "entitlement.massing_inputs"],
            )

    for gap in data_gaps:
        if gap in {"manual_site_boundary_required", "parcel_zoning_unmatched_review_required", "parcel_zoning_qualification_unverified"}:
            _flag(
                flags,
                flag_id=f"data_gap_{gap}",
                severity="high",
                category="human_review",
                title=gap.replace("_", " ").title(),
                explanation="Parcel context lists this data gap as requiring review before relying on massing.",
                recommended_action="Resolve this data gap in source data or document the human review outcome.",
                related_fields=["summary.data_gaps"],
            )

    status = _status(flags, eligible=eligible)
    human_required = [
        flag["id"]
        for flag in flags
        if flag["category"] == "human_review" or flag["severity"] == "high" or "human" in flag["recommended_action"].lower()
    ]
    return {
        "summary": _summary(status, flags, context),
        "sanity_status": status,
        "flags": flags,
        "buckets": {
            "deterministic": _bucket_ids(flags),
            "ai_assisted": [],
            "human_required": list(dict.fromkeys(human_required)),
        },
    }


def _ai_fallback(reason: str, *, model: str | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "summary": "AI massing audit is unavailable; use the deterministic audit and human review flags.",
        "findings": [],
        "human_review_items": [],
        "caveats": [reason, "AI audit is advisory only; deterministic massing output remains canonical."],
        "model": model,
    }


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def ai_massing_audit(
    context: dict[str, Any],
    deterministic_audit: dict[str, Any],
    *,
    model: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Ask OpenRouter to explain deterministic massing audit ambiguity as JSON."""

    selected_model = model or get_env("OPENROUTER_MODEL") or DEFAULT_MODEL
    try:
        api_key = require_env("OPENROUTER_API_KEY")
    except RuntimeError as exc:
        return _ai_fallback(f"OpenRouter is not configured: {exc}", model=selected_model)

    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": to_context_json(
                    {
                        "parcel_context": context,
                        "deterministic_massing_audit": deterministic_audit,
                        "instruction": (
                            "Explain the audit flags and ambiguity. Do not calculate a new max_units value "
                            "or add zoning/legal facts not present in the context."
                        ),
                    }
                ),
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return _ai_fallback(f"OpenRouter request failed: {exc}", model=selected_model)
    if response.status_code != 200:
        return _ai_fallback(f"OpenRouter returned {response.status_code}", model=selected_model)
    try:
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as exc:
        return _ai_fallback(f"OpenRouter returned invalid JSON: {exc}", model=selected_model)
    if not isinstance(parsed, dict):
        return _ai_fallback("OpenRouter JSON was not an object", model=selected_model)
    return {
        "status": str(parsed.get("status") or "reviewed"),
        "summary": str(parsed.get("summary") or ""),
        "findings": _json_list(parsed.get("findings")),
        "human_review_items": _json_list(parsed.get("human_review_items")),
        "caveats": _json_list(parsed.get("caveats")),
        "model": selected_model,
    }


def run_massing_audit(context: dict[str, Any], *, use_ai: bool = False, model: str | None = None) -> dict[str, Any]:
    deterministic = deterministic_massing_audit(context)
    result: dict[str, Any] = {"deterministic": deterministic}
    if use_ai:
        result["ai"] = ai_massing_audit(context, deterministic, model=model)
        ai_status = result["ai"].get("status")
        if ai_status and ai_status != "unavailable":
            deterministic["buckets"]["ai_assisted"] = ["ai_massing_audit"]
    return result

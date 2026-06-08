"""Normalize parcel use/enrichment fields into Live Local land categories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class LandUseDecision:
    eligible: bool
    category: str | None
    reason: str
    confidence: str


_BUCKET_CATEGORIES: dict[str, tuple[str | None, str]] = {
    "core_commercial": ("commercial", "high"),
    "core_industrial": ("industrial", "high"),
    "core_mixed_use": ("mixed_use", "high"),
    "underused_commercial_candidate": ("commercial", "medium"),
    "vacant_candidate": ("vacant_commercial_industrial", "medium"),
    "pud_flex_candidate": ("pud_flex", "medium"),
    "faith_owned_yigby_review": ("faith_owned_yigby", "low"),
    "zoning_rescue_commercial": ("commercial", "high"),
    "zoning_rescue_industrial": ("industrial", "high"),
    "zoning_rescue_mixed_use": ("mixed_use", "high"),
    "multifamily_redevelopment_review": (None, "medium"),
}

_NORMALIZED_USE_CATEGORIES: dict[str, tuple[str | None, str]] = {
    "commercial": ("commercial", "high"),
    "industrial": ("industrial", "high"),
    "mixed_use": ("mixed_use", "high"),
    "underused_commercial": ("commercial", "medium"),
    "vacant_candidate": ("vacant_commercial_industrial", "medium"),
    "pud_flex": ("pud_flex", "medium"),
    "faith_owned_yigby": ("faith_owned_yigby", "low"),
    "multifamily": (None, "medium"),
    "excluded": (None, "high"),
}

_COMMERCIAL_TERMS = (
    "COMM",
    "COMMERCE",
    "BUSINESS",
    "OFFICE",
    "RETAIL",
    "STORE",
    "SHOP",
    "HOTEL",
    "MOTEL",
    "SERVICE",
    "RESTAURANT",
    "MEDICAL",
    "ACTIVITY CENTER",
    "TOWN CENTER",
)
_INDUSTRIAL_TERMS = ("IND", "WARE", "MANUFACTUR", "MFG", "STORAGE", "DISTRIBUT")
_MIXED_USE_TERMS = ("MIX", "MIXED", "URBAN CENTER", "TRANSIT ORIENTED")
_RESIDENTIAL_TERMS = (
    "SINGLE FAMILY",
    "TOWNHOUSE",
    "CONDOMINIUM",
    "RESIDENTIAL",
    "MULTIFAMILY",
    "MOBILE HOME",
)
_NON_LLA_TERMS = (
    "AGRICULT",
    "CONSERVATION",
    "PRESERVE",
    "PUBLIC",
    "MUNICIPAL",
    "SCHOOL",
    "PARK",
    "RECREATION",
    "UTILITY",
    "WATER",
)

_ZONING_CODE_CATEGORIES: dict[str, tuple[str | None, str]] = {
    "C": ("commercial", "high"),
    "CC": ("commercial", "high"),
    "RC": ("mixed_use", "high"),
    "IC": ("industrial", "high"),
    "DMU": ("mixed_use", "high"),
    "CMU": ("mixed_use", "high"),
    "PUD": ("pud_flex", "medium"),
    "GU": (None, "medium"),
    "RSF": (None, "high"),
    "RMF": (None, "high"),
    "IPA": (None, "high"),
}


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    if hasattr(row, "_mapping"):
        return row._mapping.get(key, default)
    return getattr(row, key, default)


def _text(value: Any) -> str:
    return str(value or "").strip().upper()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return _text(value) in {"1", "T", "TRUE", "Y", "YES"}


def _decision_from_text(text: str) -> LandUseDecision | None:
    if not text:
        return None
    if any(term in text for term in _MIXED_USE_TERMS):
        return LandUseDecision(True, "mixed_use", f"text signal: {text}", "medium")
    if any(term in text for term in _INDUSTRIAL_TERMS):
        return LandUseDecision(True, "industrial", f"text signal: {text}", "medium")
    if any(term in text for term in _COMMERCIAL_TERMS):
        return LandUseDecision(True, "commercial", f"text signal: {text}", "medium")
    if any(term in text for term in _NON_LLA_TERMS):
        return LandUseDecision(False, None, f"non-LLA text signal: {text}", "medium")
    if any(term in text for term in _RESIDENTIAL_TERMS):
        return LandUseDecision(False, None, f"residential text signal: {text}", "medium")
    return None


def _category_decision(category: str | None, source: str, confidence: str) -> LandUseDecision:
    if category:
        return LandUseDecision(True, category, source, confidence)
    return LandUseDecision(False, None, source, confidence)


def parcel_zoning_land_use(parcel_row: Any) -> LandUseDecision | None:
    """Return a parcel-specific zoning/FLU land-use signal when the data is clear.

    Current use can explain why a parcel was ingested as a candidate, but Live
    Local eligibility starts with the subject parcel's zoning/permitted use. This
    helper is intentionally conservative: numeric/unknown zoning codes do not get
    guessed into an eligible category.
    """

    for key in ("zoning_general_use", "flu_class", "zoning_map_zone"):
        raw = _text(_get(parcel_row, key))
        if not raw:
            continue
        category = _ZONING_CODE_CATEGORIES.get(raw)
        if category:
            return _category_decision(category[0], f"parcel zoning via {key}={raw}", category[1])

    for key in ("zoning_map_description", "zoning_general_use", "flu_class", "zoning_code"):
        decision = _decision_from_text(_text(_get(parcel_row, key)))
        if decision:
            return LandUseDecision(decision.eligible, decision.category, f"parcel zoning via {key}", decision.confidence)

    return None


def categorize_land_use(parcel_row: Any) -> LandUseDecision:
    """Return the v0 Live Local land category decision for a parcel row.

    County ingest stores first-pass candidate buckets plus later zoning/FLU rescue
    fields. This function favors explicit rescue/enrichment signals before falling
    back to current-use buckets and raw text.
    """

    zoning_rescue = _bool(_get(parcel_row, "zoning_rescue"))
    bucket = _text(_get(parcel_row, "candidate_bucket")).lower()
    normalized_use = _text(_get(parcel_row, "normalized_use")).lower()
    parcel_zoning = parcel_zoning_land_use(parcel_row)

    if parcel_zoning and parcel_zoning.confidence == "high":
        return parcel_zoning

    if zoning_rescue:
        for key in ("candidate_bucket", "normalized_use"):
            raw = _text(_get(parcel_row, key)).lower()
            mapping = _BUCKET_CATEGORIES.get(raw) or _NORMALIZED_USE_CATEGORIES.get(raw)
            if mapping and mapping[0]:
                return _category_decision(mapping[0], f"zoning rescue via {key}={raw}", "high")

        for key in ("zoning_general_use", "flu_class", "zoning_map_description", "zoning_code"):
            decision = _decision_from_text(_text(_get(parcel_row, key)))
            if decision and decision.eligible:
                return LandUseDecision(True, decision.category, f"zoning rescue via {key}", "medium")

        return LandUseDecision(True, "zoning_rescue", "zoning_rescue=true", "medium")

    if parcel_zoning:
        return parcel_zoning

    if bucket in _BUCKET_CATEGORIES:
        category, confidence = _BUCKET_CATEGORIES[bucket]
        return _category_decision(category, f"candidate_bucket={bucket}", confidence)

    if normalized_use in _NORMALIZED_USE_CATEGORIES:
        category, confidence = _NORMALIZED_USE_CATEGORIES[normalized_use]
        return _category_decision(category, f"normalized_use={normalized_use}", confidence)

    for key in ("zoning_general_use", "flu_class", "use_class", "zoning_code"):
        decision = _decision_from_text(_text(_get(parcel_row, key)))
        if decision:
            return decision

    return LandUseDecision(False, None, "no commercial/industrial/mixed-use signal", "low")

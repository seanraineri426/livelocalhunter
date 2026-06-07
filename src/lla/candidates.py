"""County-specific Live Local parcel candidate classification.

The candidate ingest is deliberately broader than "current commercial use":
Live Local eligibility depends on land being zoned/permitted for commercial,
industrial, or mixed use, including flex/PUD areas. Current property-appraiser
use is only a first-pass screen, so the rules keep safety-net buckets where
zoning/FLU can later rescue a parcel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lla.gis_sources import ParcelSource


@dataclass(frozen=True)
class CandidateDecision:
    include: bool
    bucket: str
    reason: str
    normalized_use: str


_INCLUDE_BUCKETS = {
    "core_commercial",
    "core_industrial",
    "core_mixed_use",
    "vacant_candidate",
    "underused_commercial_candidate",
    "pud_flex_candidate",
    "faith_owned_yigby_review",
    "multifamily_redevelopment_review",
    "zoning_rescue_commercial",
    "zoning_rescue_industrial",
    "zoning_rescue_mixed_use",
}


def _text(value: Any) -> str:
    return str(value or "").strip().upper()


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lot_sf(props: dict[str, Any], source: ParcelSource) -> float | None:
    if source.lot_sf_field:
        n = _number(props.get(source.lot_sf_field))
        if n and n > 0:
            return n
    if source.acreage_field:
        n = _number(props.get(source.acreage_field))
        if n and n > 0:
            return n * 43560
    return None


def include_bucket(bucket: str) -> bool:
    return bucket in _INCLUDE_BUCKETS


def classify_candidate(source: ParcelSource, props: dict[str, Any]) -> CandidateDecision:
    if source.county_key == "miami_dade":
        return _classify_miami_dade(source, props)
    if source.county_key == "broward":
        return _classify_broward(source, props)
    if source.county_key == "palm_beach":
        return _classify_palm_beach(source, props)
    return CandidateDecision(False, "excluded_unknown", "no classifier for county", "unknown")


def candidate_where(source: ParcelSource) -> str:
    """ArcGIS where clause for server-side candidate prefiltering.

    These clauses are intentionally conservative and may include review buckets;
    Python classification still runs before upsert to assign the exact bucket.
    """
    if source.county_key == "miami_dade":
        # Core commercial/industrial/mixed-use plus multifamily/redevelopment and
        # vacant/flex safety nets. Zoning rescue from layer 19 is a later join.
        return """
            FOLIO<>'0000000000000' AND (
                DOR_CODE_CUR LIKE '1%' OR
                DOR_CODE_CUR LIKE '2%' OR
                DOR_CODE_CUR LIKE '3%' OR
                DOR_CODE_CUR LIKE '4%' OR
                DOR_CODE_CUR LIKE '08%' OR
                DOR_CODE_CUR IN (
                    '0081','0066','0010','0004','0005','0007','0709',
                    '5003','5009','5011','5013','5029','5032','5037','5069','5079',
                    '7003','7081'
                )
            )
        """
    if source.county_key == "broward":
        return """
            PARCELNO IS NOT NULL
            AND PUBLIC_LND IS NULL
            AND (
                (DOR_UC >= '010' AND DOR_UC <= '049')
                OR DOR_UC IN ('003','020','028','031','032','033','034','035','036','037','038','099')
                OR ((DOR_UC IS NULL OR DOR_UC = '000') AND LND_SQFOOT >= 87120)
            )
        """
    if source.county_key == "palm_beach":
        # ArcGIS SQL LIKE support is adequate here; Python handles exact bucket.
        return """
            PCN IS NOT NULL AND (
                UPPER(PROPERTY_USE) LIKE '%COMM%' OR
                UPPER(PROPERTY_USE) LIKE '%IND ZONING%' OR
                UPPER(PROPERTY_USE) LIKE 'STORE%' OR
                UPPER(PROPERTY_USE) LIKE 'OFFICE%' OR
                UPPER(PROPERTY_USE) LIKE 'MEDIC%' OR
                UPPER(PROPERTY_USE) LIKE 'SHOPPING%' OR
                UPPER(PROPERTY_USE) LIKE 'SERVICE%' OR
                UPPER(PROPERTY_USE) LIKE 'RESTAURANT%' OR
                UPPER(PROPERTY_USE) LIKE 'FINANCIAL%' OR
                UPPER(PROPERTY_USE) LIKE 'INSURANCE%' OR
                UPPER(PROPERTY_USE) LIKE 'AUTO SALES%' OR
                UPPER(PROPERTY_USE) LIKE 'WAREH%' OR
                UPPER(PROPERTY_USE) LIKE '%INDUSTRIAL%' OR
                UPPER(PROPERTY_USE) LIKE 'LIGHT MFG%' OR
                UPPER(PROPERTY_USE) LIKE 'OPEN STORAGE%' OR
                UPPER(PROPERTY_USE) LIKE 'SELF STORAGE%' OR
                UPPER(PROPERTY_USE) LIKE 'HOTEL%' OR
                UPPER(PROPERTY_USE) LIKE 'MOTEL%' OR
                UPPER(PROPERTY_USE) IN ('VACANT','VACANT INSTIT','RELIGIOUS','PRV SCHL/COLL')
            )
        """
    return source.where


def _classify_miami_dade(source: ParcelSource, props: dict[str, Any]) -> CandidateDecision:
    code = _text(props.get("DOR_CODE_CUR")).zfill(4)
    desc = _text(props.get("DOR_DESC"))
    zone = _text(props.get("PRIMARY_ZONE"))
    lot_sf = _lot_sf(props, source) or 0

    if code.startswith(("1", "2", "3", "4")):
        return CandidateDecision(True, _md_core_bucket(code, desc), f"DOR {code} core candidate", _md_core_bucket(code, desc))
    if code.startswith("08") or code in {"0303", "0317"}:
        return CandidateDecision(True, "multifamily_redevelopment_review", f"DOR {code} multifamily/redevelopment review", "multifamily")
    if code in {"1081", "1066", "4081", "4066"}:
        return CandidateDecision(True, "vacant_candidate", f"DOR {code} vacant commercial/industrial", "vacant_candidate")
    if code in {"1209", "1211", "1217", "1229", "1829", "1929", "2729", "3929", "4729", "4739"}:
        return CandidateDecision(True, "core_mixed_use", f"DOR {code} mixed-use pattern", "mixed_use")
    if code in {"0081", "0066", "0010", "0004", "0005", "0007", "0709"} and lot_sf >= 43560:
        return CandidateDecision(True, "vacant_candidate", f"DOR {code} uncertain/vacant >=1 acre", "vacant_candidate")
    if code in {"5003", "5009", "5011", "5013", "5029", "5032", "5037", "5069", "5079"}:
        return CandidateDecision(True, "pud_flex_candidate", f"DOR {code} flex/ag-commercial safety net", "pud_flex")
    if code in {"7003", "7081"}:
        return CandidateDecision(True, "faith_owned_yigby_review", f"DOR {code} institutional/religious review", "faith_owned_yigby")
    if code.startswith(("01", "02", "04", "5", "6", "7", "8", "9")) or code in {"0000", "0951", "9751"}:
        return CandidateDecision(False, "excluded_residential_or_public", f"DOR {code} excluded; zone={zone}", "excluded")
    return CandidateDecision(False, "excluded_unknown", f"DOR {code or 'missing'} not candidate", "unknown")


def _md_core_bucket(code: str, desc: str) -> str:
    if "IND" in desc or "WARE" in desc or code.startswith("4"):
        return "core_industrial"
    if "MIX" in desc or "RES" in desc and ("STORE" in desc or "OFFICE" in desc):
        return "core_mixed_use"
    return "core_commercial"


def _classify_broward(source: ParcelSource, props: dict[str, Any]) -> CandidateDecision:
    code = _text(props.get("DOR_UC")).zfill(3)
    lot_sf = _lot_sf(props, source) or 0
    public_land = props.get("PUBLIC_LND")

    if public_land not in (None, ""):
        return CandidateDecision(False, "excluded_public_or_recreation", "PUBLIC_LND is set", "excluded")
    if "010" <= code <= "049":
        if code in {"041", "042", "043", "044", "045", "046", "047", "048", "049"}:
            return CandidateDecision(True, "core_industrial", f"DOR_UC {code} industrial/commercial range", "industrial")
        if code in {"028", "020", "031", "032", "033", "034", "035", "036", "037", "038"}:
            return CandidateDecision(True, "underused_commercial_candidate", f"DOR_UC {code} review bucket", "underused_commercial")
        return CandidateDecision(True, "core_commercial", f"DOR_UC {code} commercial range", "commercial")
    if code == "003":
        return CandidateDecision(True, "multifamily_redevelopment_review", "DOR_UC 003 multifamily review", "multifamily")
    if code == "099":
        return CandidateDecision(True, "pud_flex_candidate", "DOR_UC 099 acreage not agricultural", "pud_flex")
    if code in {"000", ""} and lot_sf >= 87120:
        return CandidateDecision(True, "vacant_candidate", "blank/000 use with lot >=2 acres", "vacant_candidate")
    if code in {"001", "002", "004", "005", "006", "007", "008", "009"}:
        return CandidateDecision(False, "excluded_residential", f"DOR_UC {code} residential/noise", "excluded")
    if "050" <= code <= "069" or code == "097":
        return CandidateDecision(False, "excluded_agricultural", f"DOR_UC {code} agricultural", "excluded")
    if "070" <= code <= "089" or code == "098":
        return CandidateDecision(False, "excluded_public_or_recreation", f"DOR_UC {code} public/institutional", "excluded")
    if "091" <= code <= "096":
        return CandidateDecision(False, "excluded_misc_noise", f"DOR_UC {code} misc/noise", "excluded")
    return CandidateDecision(False, "excluded_unknown", f"DOR_UC {code or 'missing'} not candidate", "unknown")


def _classify_palm_beach(source: ParcelSource, props: dict[str, Any]) -> CandidateDecision:
    use = _text(props.get("PROPERTY_USE"))
    lot_sf = _lot_sf(props, source) or 0

    if "COMM ZONING" in use:
        return CandidateDecision(True, "zoning_rescue_commercial", f"PROPERTY_USE {use}", "commercial")
    if "IND ZONING" in use or "INDUSTRIAL" in use:
        return CandidateDecision(True, "zoning_rescue_industrial", f"PROPERTY_USE {use}", "industrial")
    if use.startswith(("STORE", "OFFICE", "MEDIC", "SHOPPING", "SERVICE", "RESTAURANT", "FINANCIAL", "INSURANCE", "AUTO SALES")):
        return CandidateDecision(True, "core_commercial", f"PROPERTY_USE {use}", "commercial")
    if use.startswith(("WAREH", "LIGHT MFG", "OPEN STORAGE", "SELF STORAGE")) or use in {"MFR-IMP NON CONTRIBUTING", "MIN PROCESSING"}:
        return CandidateDecision(True, "core_industrial", f"PROPERTY_USE {use}", "industrial")
    if "STORE/OFFICE/RESIDENTIAL" in use:
        return CandidateDecision(True, "core_mixed_use", f"PROPERTY_USE {use}", "mixed_use")
    if use.startswith(("HOTEL", "MOTEL", "TOURIST ATTRAC", "NIGHT CLUB", "THTR/AUD/CLBHS", "CLB/LDG/UN HALL")):
        return CandidateDecision(True, "core_commercial", f"PROPERTY_USE {use}", "commercial")
    if use in {"VACANT COMMERCIAL LAND", "VACANT INDUSTRIAL"}:
        return CandidateDecision(True, "vacant_candidate", f"PROPERTY_USE {use}", "vacant_candidate")
    if use == "VACANT" and lot_sf >= 43560:
        return CandidateDecision(True, "vacant_candidate", "generic VACANT >=1 acre", "vacant_candidate")
    if use in {"VACANT INSTIT", "LEASEHOLD INT", "CENTRALLY ASSESSED", "NON AG", ""} and lot_sf >= 43560:
        return CandidateDecision(True, "pud_flex_candidate", f"uncertain PROPERTY_USE {use or 'missing'} >=1 acre", "pud_flex")
    if use in {"RELIGIOUS", "PRV SCHL/COLL", "ORPHNG/NON-PROF", "RETIREMENT", "LIFE CARE HX", "SANI/ REST HOME"} and lot_sf >= 43560:
        return CandidateDecision(True, "faith_owned_yigby_review", f"PROPERTY_USE {use} >=1 acre", "faith_owned_yigby")
    if use.startswith(("SINGLE FAMILY", "TOWNHOUSE", "CONDOMINIUM", "MULTIFAMILY", "MFR ", "MOBILE HOME", "MHT COOP", "ZERO LOT LINE")):
        return CandidateDecision(False, "excluded_residential", f"PROPERTY_USE {use}", "excluded")
    if use.startswith("AG CLASSIFICATION") or use in {"AGR-PUD PRESERVE", "PACKING"}:
        return CandidateDecision(False, "excluded_agricultural", f"PROPERTY_USE {use}", "excluded")
    if use in {"MUNICIPAL", "STATE", "CITY INC NONMUNI", "DISTRICTS", "PUB CTY SCHOOL", "UTILITY"}:
        return CandidateDecision(False, "excluded_public_or_recreation", f"PROPERTY_USE {use}", "excluded")
    if use in {"R/W - BUFFER", "FOREST/PK/REC", "OUTDR REC/PARK LAND", "GOLF COURSE", "RIVER/LAKES", "AIRPORT/MARINA", "MORT/CEMETERY", "CAMPS", "CULTURAL"}:
        return CandidateDecision(False, "excluded_public_or_recreation", f"PROPERTY_USE {use}", "excluded")
    return CandidateDecision(False, "excluded_unknown", f"PROPERTY_USE {use or 'missing'} not candidate", "unknown")

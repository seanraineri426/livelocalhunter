"""Live Local Act statutory massing rollups and parcel-level envelope calculations."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from psycopg2.extras import Json, execute_values
from sqlalchemy import text
from sqlalchemy.engine import Connection


PARAMS_VERSION = "live-local-4.0-2025"

DEFAULT_DENSITY_DU_AC = Decimal("15")
DEFAULT_MAX_FAR = Decimal("1")
DEFAULT_HEIGHT_FT = Decimal("35")
DEFAULT_HEIGHT_STORIES = Decimal("3")
DEFAULT_PARKING_PER_UNIT = Decimal("1.5")
STORY_HEIGHT_FT = Decimal("10")
FAR_STATUTORY_MULTIPLIER = Decimal("1.5")
MAX_PLAUSIBLE_LOCAL_FAR = Decimal("10")
TRANSIT_STOP_PARKING_MULTIPLIER = Decimal("0.85")
SINGLE_FAMILY_ADJACENCY_HEIGHT_CAP_STORIES = Decimal("10")

# --- Building-envelope reconciliation constants -------------------------------
# These translate a buildable floor-area envelope into an approximate residential
# unit count. They are screening defaults, documented in docs/live_local_massing.md,
# and recorded per parcel in entitlement.massing_inputs for auditability.
#
# AVG_UNIT_NET_SF        average net (rentable) area of one dwelling unit.
# UNIT_GROSS_EFFICIENCY  net rentable area divided by gross building area; the
#                        remainder is corridors, cores, walls, amenity, mechanical.
# DEFAULT_LOT_COVERAGE   conservative max building footprint fraction used when a
#                        zoning district (with explicit coverage/setbacks) is not
#                        matched to the parcel.
# SURFACE_PARKING_SF_PER_STALL  land consumed by one surface stall incl. drive aisle.
# OVERSIZED_PARCEL_ACRES parcels above this size are almost always aggregate tracts
#                        (sections, golf courses, government land) rather than a
#                        single development site, so we flag and degrade confidence.
AVG_UNIT_NET_SF = Decimal("900")
UNIT_GROSS_EFFICIENCY = Decimal("0.82")
DEFAULT_LOT_COVERAGE = Decimal("0.40")
SURFACE_PARKING_SF_PER_STALL = Decimal("350")
OVERSIZED_PARCEL_ACRES = Decimal("50")


CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class JurisdictionRollup:
    jurisdiction_id: str
    county_fips: str
    jurisdiction_name: str
    district_count: int
    max_density_du_ac: Decimal
    max_far: Decimal
    far_2023_snapshot: Decimal | None
    max_height_ft: Decimal
    max_height_stories: Decimal
    base_parking_per_unit: Decimal
    zoning_crosswalk_ref: str
    missing_fields: tuple[str, ...]


@dataclass(frozen=True)
class EnvelopeReconciliation:
    """Result of reconciling density, FAR, and footprint x height constraints."""

    max_units: int
    binding_constraint: str
    buildable_sf: Decimal
    footprint_sf: Decimal
    density_limited_units: int
    far_limited_units: int
    envelope_limited_units: int
    far_buildable_sf: Decimal
    envelope_buildable_sf: Decimal
    lot_coverage_fraction: Decimal
    setback_footprint_sf: Decimal | None
    surface_parking_sf: Decimal
    flags: tuple[str, ...]


@dataclass(frozen=True)
class MassingResult:
    parcel_id: str
    county_fips: str
    max_units: int
    max_far: Decimal
    buildable_sf: Decimal
    max_height_ft: Decimal
    max_height_stories: Decimal
    parking_ratio: Decimal
    required_parking: int
    confidence: str
    missing_params: bool
    massing_flags: tuple[str, ...]
    massing_inputs: dict[str, Any]


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    if number <= 0:
        return None
    return number


def _coverage_fraction(value: Any) -> Decimal | None:
    """Normalize a stored max-lot-coverage value to a 0-1 fraction.

    The zoning_districts table stores coverage inconsistently: some rows hold a
    percent (e.g. ``40`` or ``50``) and others a fraction (e.g. ``0.8``). Values
    greater than 1 are treated as percents; values in (0, 1] are already fractions.
    """

    number = _decimal(value)
    if number is None:
        return None
    if number > 1:
        number = number / Decimal("100")
    if number <= 0:
        return None
    return min(number, Decimal("1"))


def _max_decimal(values: Iterable[Any], *, max_value: Decimal | None = None) -> Decimal | None:
    numbers = [_decimal(value) for value in values]
    numbers = [number for number in numbers if number is not None]
    if max_value is not None:
        numbers = [number for number in numbers if number <= max_value]
    return max(numbers) if numbers else None


def _is_residential_density_source(row: dict[str, Any]) -> bool:
    category = str(row.get("category") or "").lower()
    return (
        row.get("allows_multifamily") is True
        or row.get("allows_residential") is True
        or category in {"residential", "mixed_use"}
    )


def _is_development_far_source(row: dict[str, Any]) -> bool:
    category = str(row.get("category") or "").lower()
    return category not in {"agricultural", "civic", "other"}


def _lowest_confidence(*values: str | None) -> str:
    usable = [value for value in values if value]
    if not usable:
        return "low"
    return min(usable, key=lambda value: CONFIDENCE_ORDER.get(value, 0))


def fetch_zoning_districts(conn: Connection, county_fips: str | None = None) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT
                z.jurisdiction_id::text AS jurisdiction_id,
                j.county_fips,
                j.name AS jurisdiction_name,
                z.category,
                z.allows_residential,
                z.allows_multifamily,
                z.max_density_du_ac,
                z.max_height_ft,
                z.max_height_stories,
                z.max_far,
                z.max_lot_coverage,
                z.parking_per_unit
            FROM lla.zoning_districts z
            JOIN lla.jurisdictions j ON j.jurisdiction_id = z.jurisdiction_id
            WHERE (:county_fips IS NULL OR j.county_fips = :county_fips)
            ORDER BY j.county_fips, j.name, z.district_code
            """
        ),
        {"county_fips": county_fips},
    ).mappings()
    return [dict(row) for row in rows]


def rollup_jurisdiction_params(rows: list[dict[str, Any]]) -> list[JurisdictionRollup]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["jurisdiction_id"])].append(row)

    rollups: list[JurisdictionRollup] = []
    for jurisdiction_id, district_rows in sorted(
        grouped.items(), key=lambda item: (item[1][0]["county_fips"], item[1][0]["jurisdiction_name"])
    ):
        density_source = [row for row in district_rows if _is_residential_density_source(row)]
        density = _max_decimal(row.get("max_density_du_ac") for row in density_source)
        if density is None:
            density = _max_decimal(row.get("max_density_du_ac") for row in district_rows)

        far_source = [row for row in district_rows if _is_development_far_source(row)]
        local_far = _max_decimal((row.get("max_far") for row in far_source), max_value=MAX_PLAUSIBLE_LOCAL_FAR)
        if local_far is None:
            local_far = _max_decimal((row.get("max_far") for row in district_rows), max_value=MAX_PLAUSIBLE_LOCAL_FAR)
        parking = _max_decimal(row.get("parking_per_unit") for row in district_rows)
        height_source = [
            row
            for row in district_rows
            if str(row.get("category") or "").lower() in {"commercial", "mixed_use", "residential"}
            or row.get("allows_residential") is True
            or row.get("allows_multifamily") is True
        ]
        if not height_source:
            height_source = district_rows
        explicit_height_ft = _max_decimal(row.get("max_height_ft") for row in height_source)
        explicit_height_stories = _max_decimal(row.get("max_height_stories") for row in height_source)

        missing: list[str] = []
        if density is None:
            density = DEFAULT_DENSITY_DU_AC
            missing.append("density")
        if local_far is None:
            local_far = DEFAULT_MAX_FAR
            missing.append("far")
        if parking is None:
            parking = DEFAULT_PARKING_PER_UNIT
            missing.append("parking")

        height_from_stories = explicit_height_stories * STORY_HEIGHT_FT if explicit_height_stories else None
        height_ft = max(
            [value for value in (explicit_height_ft, height_from_stories, DEFAULT_HEIGHT_FT) if value is not None]
        )
        height_stories = explicit_height_stories or (height_ft / STORY_HEIGHT_FT)
        height_stories = max(height_stories, DEFAULT_HEIGHT_STORIES)
        if explicit_height_ft is None and explicit_height_stories is None:
            missing.append("height")

        source_notes = ",".join(f"{field}={'default' if field in missing else 'extracted'}" for field in (
            "density",
            "far",
            "parking",
            "height",
        ))
        rollups.append(
            JurisdictionRollup(
                jurisdiction_id=jurisdiction_id,
                county_fips=district_rows[0]["county_fips"],
                jurisdiction_name=district_rows[0]["jurisdiction_name"],
                district_count=len(district_rows),
                max_density_du_ac=density,
                max_far=local_far * FAR_STATUTORY_MULTIPLIER,
                far_2023_snapshot=local_far,
                max_height_ft=height_ft,
                max_height_stories=height_stories,
                base_parking_per_unit=parking,
                zoning_crosswalk_ref=(
                    f"zoning_districts:{PARAMS_VERSION}:districts={len(district_rows)}:"
                    f"far_multiplier={FAR_STATUTORY_MULTIPLIER}:{source_notes}"
                ),
                missing_fields=tuple(missing),
            )
        )
    return rollups


def upsert_jurisdiction_params(conn: Connection, rollups: list[JurisdictionRollup]) -> int:
    if not rollups:
        return 0

    values = [
        (
            rollup.jurisdiction_id,
            rollup.max_density_du_ac,
            rollup.max_far,
            rollup.far_2023_snapshot,
            rollup.zoning_crosswalk_ref,
            rollup.base_parking_per_unit,
        )
        for rollup in rollups
    ]
    dbapi_conn = conn.connection.driver_connection
    with dbapi_conn.cursor() as cursor:
        execute_values(
            cursor,
            """
            INSERT INTO lla.jurisdiction_params (
                jurisdiction_id,
                max_density_du_ac,
                max_far,
                far_2023_snapshot,
                zoning_crosswalk_ref,
                base_parking_per_unit,
                params_version,
                updated_at
            )
            VALUES %s
            ON CONFLICT (jurisdiction_id) DO UPDATE SET
                max_density_du_ac = EXCLUDED.max_density_du_ac,
                max_far = EXCLUDED.max_far,
                far_2023_snapshot = EXCLUDED.far_2023_snapshot,
                zoning_crosswalk_ref = EXCLUDED.zoning_crosswalk_ref,
                base_parking_per_unit = EXCLUDED.base_parking_per_unit,
                params_version = EXCLUDED.params_version,
                updated_at = now()
            """,
            values,
            template=f"(%s::uuid, %s, %s, %s, %s, %s, '{PARAMS_VERSION}', now())",
            page_size=len(values),
        )
    return len(rollups)


def _setback_footprint_sf(
    lot_sf: Decimal,
    front_setback_ft: Decimal | None,
    side_setback_ft: Decimal | None,
    rear_setback_ft: Decimal | None,
) -> Decimal | None:
    """Approximate the per-floor buildable footprint after setbacks.

    We do not store lot dimensions, only lot area, so we model the parcel as a
    square (side = sqrt(lot_sf)) and subtract front+rear from the depth and twice
    the side setback from the width. This is a conservative screening proxy.
    """

    if lot_sf <= 0:
        return None
    if front_setback_ft is None and side_setback_ft is None and rear_setback_ft is None:
        return None
    side_len = Decimal(str(math.sqrt(float(lot_sf))))
    front = front_setback_ft or Decimal("0")
    side = side_setback_ft or Decimal("0")
    rear = rear_setback_ft or Decimal("0")
    buildable_depth = max(side_len - front - rear, Decimal("0"))
    buildable_width = max(side_len - (side * 2), Decimal("0"))
    return buildable_depth * buildable_width


def reconcile_max_units(
    *,
    acreage: Decimal,
    lot_sf: Decimal,
    density_du_ac: Decimal,
    statutory_far: Decimal,
    height_stories: Decimal,
    parking_ratio: Decimal,
    max_lot_coverage: Any = None,
    front_setback_ft: Any = None,
    side_setback_ft: Any = None,
    rear_setback_ft: Any = None,
    zoning_matched: bool = False,
) -> EnvelopeReconciliation:
    """Reconcile the binding (minimum) of density, FAR, and footprint x height.

    Returns the reconciled max units and the constraint that produced it, plus the
    intermediate candidates for auditability. ``buildable_sf`` is capped to the
    lesser of the FAR cap and the footprint x height envelope.
    """

    flags: list[str] = []

    # 1. Density-limited units (gross acreage; a net/ROW deduction is not available).
    density_limited_units = int(math.floor(density_du_ac * acreage)) if acreage > 0 else 0

    # 2. FAR-limited buildable area -> units via efficiency + avg unit size.
    far_buildable_sf = statutory_far * lot_sf
    far_limited_units = int(
        math.floor(far_buildable_sf * UNIT_GROSS_EFFICIENCY / AVG_UNIT_NET_SF)
    )

    # 3. Footprint (lot coverage / setbacks) x floors -> envelope-limited units.
    coverage = _coverage_fraction(max_lot_coverage)
    if coverage is None:
        coverage = DEFAULT_LOT_COVERAGE
        flags.append("lot_coverage_defaulted")
    coverage_footprint = coverage * lot_sf

    setback_footprint = _setback_footprint_sf(
        lot_sf,
        _decimal(front_setback_ft),
        _decimal(side_setback_ft),
        _decimal(rear_setback_ft),
    )
    if setback_footprint is None:
        flags.append("setbacks_defaulted")
        footprint_sf = coverage_footprint
    else:
        footprint_sf = min(coverage_footprint, setback_footprint)

    envelope_buildable_sf = footprint_sf * height_stories
    envelope_limited_units = int(
        math.floor(envelope_buildable_sf * UNIT_GROSS_EFFICIENCY / AVG_UNIT_NET_SF)
    )

    if not zoning_matched:
        flags.append("envelope_uses_default_lot_coverage")

    # Binding constraint = the most restrictive of the three.
    candidates = (
        ("density", density_limited_units),
        ("far", far_limited_units),
        ("footprint_height", envelope_limited_units),
    )
    binding_constraint, max_units = min(candidates, key=lambda item: item[1])
    max_units = max(max_units, 0)

    buildable_sf = min(far_buildable_sf, envelope_buildable_sf)
    required_parking = int(math.ceil(Decimal(max_units) * parking_ratio))
    surface_parking_sf = Decimal(required_parking) * SURFACE_PARKING_SF_PER_STALL

    open_area_sf = max(lot_sf - footprint_sf, Decimal("0"))
    if surface_parking_sf > open_area_sf:
        flags.append("surface_parking_may_not_fit_structured_parking_likely")

    return EnvelopeReconciliation(
        max_units=max_units,
        binding_constraint=binding_constraint,
        buildable_sf=buildable_sf,
        footprint_sf=footprint_sf,
        density_limited_units=density_limited_units,
        far_limited_units=far_limited_units,
        envelope_limited_units=envelope_limited_units,
        far_buildable_sf=far_buildable_sf,
        envelope_buildable_sf=envelope_buildable_sf,
        lot_coverage_fraction=coverage,
        setback_footprint_sf=setback_footprint,
        surface_parking_sf=surface_parking_sf,
        flags=tuple(flags),
    )


def compute_massing(row: dict[str, Any]) -> MassingResult:
    acreage = _decimal(row.get("acreage")) or Decimal("0")
    lot_sf = _decimal(row.get("lot_sf")) or Decimal("0")
    # acreage and lot_sf are both derived from the same geometry upstream; if one is
    # missing reconstruct it so the envelope and density checks stay consistent.
    if lot_sf <= 0 and acreage > 0:
        lot_sf = acreage * Decimal("43560")
    if acreage <= 0 and lot_sf > 0:
        acreage = lot_sf / Decimal("43560")
    density = _decimal(row.get("max_density_du_ac")) or DEFAULT_DENSITY_DU_AC
    far = _decimal(row.get("max_far")) or DEFAULT_MAX_FAR
    parking_ratio = _decimal(row.get("base_parking_per_unit")) or DEFAULT_PARKING_PER_UNIT

    flags: list[str] = []
    if row.get("jurisdiction_params_missing"):
        flags.append("jurisdiction_params_missing")
    if row.get("max_density_du_ac") is None:
        flags.append("density_defaulted")
    if row.get("max_far") is None:
        flags.append("far_defaulted")
    if row.get("base_parking_per_unit") is None:
        flags.append("parking_defaulted")
    if row.get("zoning_geometry_available") is not True:
        flags.append("height_within_1mi_uses_jurisdiction_rollup")
    if row.get("subject_zoning_matched") is not True:
        flags.append("subject_zoning_height_not_matched")
    if row.get("historic_height_data_available") is not True:
        flags.append("historic_height_screen_missing")
    flags.extend(("major_transportation_hub_input_missing", "available_parking_input_missing", "tod_area_input_missing"))

    one_mile_height_ft = _decimal(row.get("height_1mi_ft"))
    one_mile_height_stories = _decimal(row.get("height_1mi_stories"))
    subject_height_ft = _decimal(row.get("subject_height_ft"))
    subject_height_stories = _decimal(row.get("subject_height_stories"))

    height_ft_candidates = [
        value
        for value in (
            one_mile_height_ft,
            one_mile_height_stories * STORY_HEIGHT_FT if one_mile_height_stories else None,
            DEFAULT_HEIGHT_FT,
        )
        if value is not None
    ]
    height_ft = max(height_ft_candidates)
    height_stories = max(one_mile_height_stories or (height_ft / STORY_HEIGHT_FT), DEFAULT_HEIGHT_STORIES)

    if row.get("single_family_adjacency_possible"):
        flags.append("single_family_adjacency_possible")
        if height_stories > SINGLE_FAMILY_ADJACENCY_HEIGHT_CAP_STORIES:
            height_stories = SINGLE_FAMILY_ADJACENCY_HEIGHT_CAP_STORIES
            height_ft = min(height_ft, SINGLE_FAMILY_ADJACENCY_HEIGHT_CAP_STORIES * STORY_HEIGHT_FT)
            flags.append("single_family_10_story_cap_applied")
        if subject_height_ft is None and subject_height_stories is None:
            flags.append("adjacent_tallest_building_height_missing")

    if row.get("has_transit_stops") is not True:
        flags.append("transit_stop_input_missing")
    elif row.get("within_quarter_mile_transit"):
        parking_ratio *= TRANSIT_STOP_PARKING_MULTIPLIER
        flags.append("parking_15pct_transit_reduction_applied")
        flags.append("transit_accessibility_unverified")

    reconciliation = reconcile_max_units(
        acreage=acreage,
        lot_sf=lot_sf,
        density_du_ac=density,
        statutory_far=far,
        height_stories=height_stories,
        parking_ratio=parking_ratio,
        max_lot_coverage=row.get("subject_max_lot_coverage"),
        front_setback_ft=row.get("subject_front_setback_ft"),
        side_setback_ft=row.get("subject_side_setback_ft"),
        rear_setback_ft=row.get("subject_rear_setback_ft"),
        zoning_matched=bool(row.get("subject_zoning_matched")),
    )
    max_units = reconciliation.max_units
    buildable_sf = reconciliation.buildable_sf
    required_parking = int(math.ceil(Decimal(max_units) * parking_ratio))
    flags.extend(reconciliation.flags)

    if acreage > OVERSIZED_PARCEL_ACRES:
        flags.append("oversized_parcel_review_required")

    missing_params = (
        row.get("jurisdiction_params_missing")
        or row.get("max_density_du_ac") is None
        or row.get("max_far") is None
        or row.get("base_parking_per_unit") is None
    )
    confidence = str(row.get("confidence") or "low")
    if missing_params or "oversized_parcel_review_required" in flags:
        confidence = _lowest_confidence(confidence, "low")
    elif any(
        flag in flags
        for flag in (
            "height_within_1mi_uses_jurisdiction_rollup",
            "historic_height_screen_missing",
            "subject_zoning_height_not_matched",
            "lot_coverage_defaulted",
            "setbacks_defaulted",
            "envelope_uses_default_lot_coverage",
        )
    ):
        confidence = _lowest_confidence(confidence, "medium")

    massing_inputs = {
        "statute_version": PARAMS_VERSION,
        "acreage": str(acreage),
        "lot_sf": str(lot_sf),
        "density_du_ac": str(density),
        "far": str(far),
        "parking_ratio": str(parking_ratio),
        "height_1mi_ft": str(one_mile_height_ft) if one_mile_height_ft else None,
        "height_1mi_stories": str(one_mile_height_stories) if one_mile_height_stories else None,
        "subject_height_ft": str(subject_height_ft) if subject_height_ft else None,
        "subject_height_stories": str(subject_height_stories) if subject_height_stories else None,
        "adjacent_single_family_parcels": int(row.get("adjacent_single_family_parcels") or 0),
        "within_quarter_mile_transit": bool(row.get("within_quarter_mile_transit")),
        "zoning_geometry_available": bool(row.get("zoning_geometry_available")),
        "historic_height_data_available": bool(row.get("historic_height_data_available")),
        # --- Building-envelope reconciliation audit trail ---
        "binding_constraint": reconciliation.binding_constraint,
        "max_height_stories": str(height_stories),
        "density_limited_units": reconciliation.density_limited_units,
        "far_limited_units": reconciliation.far_limited_units,
        "envelope_limited_units": reconciliation.envelope_limited_units,
        "far_buildable_sf": str(reconciliation.far_buildable_sf),
        "envelope_buildable_sf": str(reconciliation.envelope_buildable_sf),
        "buildable_sf": str(reconciliation.buildable_sf),
        "footprint_sf": str(reconciliation.footprint_sf),
        "lot_coverage_fraction": str(reconciliation.lot_coverage_fraction),
        "setback_footprint_sf": (
            str(reconciliation.setback_footprint_sf)
            if reconciliation.setback_footprint_sf is not None
            else None
        ),
        "subject_max_lot_coverage": (
            str(_decimal(row.get("subject_max_lot_coverage")))
            if _decimal(row.get("subject_max_lot_coverage")) is not None
            else None
        ),
        "subject_front_setback_ft": (
            str(_decimal(row.get("subject_front_setback_ft")))
            if _decimal(row.get("subject_front_setback_ft")) is not None
            else None
        ),
        "subject_side_setback_ft": (
            str(_decimal(row.get("subject_side_setback_ft")))
            if _decimal(row.get("subject_side_setback_ft")) is not None
            else None
        ),
        "subject_rear_setback_ft": (
            str(_decimal(row.get("subject_rear_setback_ft")))
            if _decimal(row.get("subject_rear_setback_ft")) is not None
            else None
        ),
        "subject_min_lot_sf": (
            str(_decimal(row.get("subject_min_lot_sf")))
            if _decimal(row.get("subject_min_lot_sf")) is not None
            else None
        ),
        "avg_unit_net_sf": str(AVG_UNIT_NET_SF),
        "unit_gross_efficiency": str(UNIT_GROSS_EFFICIENCY),
        "surface_parking_sf_estimate": str(reconciliation.surface_parking_sf),
    }

    return MassingResult(
        parcel_id=str(row["parcel_id"]),
        county_fips=str(row["county_fips"]),
        max_units=max_units,
        max_far=far,
        buildable_sf=buildable_sf,
        max_height_ft=height_ft,
        max_height_stories=height_stories,
        parking_ratio=parking_ratio,
        required_parking=required_parking,
        confidence=confidence,
        missing_params=bool(missing_params),
        massing_flags=tuple(dict.fromkeys(flags)),
        massing_inputs=massing_inputs,
    )


def ensure_entitlement_upsert_target(conn: Connection) -> None:
    conn.execute(text("SET LOCAL lock_timeout = '15s'"))
    conn.execute(text("ALTER TABLE lla.entitlement ADD COLUMN IF NOT EXISTS massing_flags TEXT[] NOT NULL DEFAULT '{}'"))
    conn.execute(text("ALTER TABLE lla.entitlement ADD COLUMN IF NOT EXISTS massing_inputs JSONB NOT NULL DEFAULT '{}'::jsonb"))
    conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_lla_entitlement_unique_parcel
                ON lla.entitlement (parcel_id)
                WHERE parcel_id IS NOT NULL
            """
        )
    )


def fetch_eligible_massing_batch(
    conn: Connection,
    *,
    county_fips: str | None,
    last_parcel_id: str | None,
    batch_size: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            WITH height_rollup AS (
                SELECT
                    jurisdiction_id,
                    max(max_height_ft) AS max_height_ft,
                    max(max_height_stories) AS max_height_stories
                FROM lla.zoning_districts
                WHERE lower(coalesce(category, '')) IN ('commercial', 'mixed_use', 'residential')
                   OR allows_residential IS TRUE
                   OR allows_multifamily IS TRUE
                GROUP BY jurisdiction_id
            ),
            transit_stop_count AS (
                SELECT count(*) AS stop_count FROM lla.transit_stops
            ),
            batch AS (
                SELECT
                    p.parcel_id::text AS parcel_id,
                    p.parcel_id AS parcel_uuid,
                    p.county_fips,
                    COALESCE(p.acreage, (ST_Area(p.geom::geography) * 10.76391041671) / 43560.0) AS acreage,
                    COALESCE(p.lot_sf, ST_Area(p.geom::geography) * 10.76391041671) AS lot_sf,
                    p.jurisdiction_id,
                    p.zoning_code,
                    p.zoning_map_zone,
                    p.geom,
                    e.confidence,
                    jp.max_density_du_ac,
                    jp.max_far,
                    jp.base_parking_per_unit,
                    hr.max_height_ft AS height_1mi_ft,
                    hr.max_height_stories AS height_1mi_stories,
                    (jp.jurisdiction_id IS NULL) AS jurisdiction_params_missing
                FROM lla.entitlement e
                JOIN lla.parcels p ON p.parcel_id = e.parcel_id
                LEFT JOIN lla.jurisdiction_params jp ON jp.jurisdiction_id = p.jurisdiction_id
                LEFT JOIN height_rollup hr ON hr.jurisdiction_id = p.jurisdiction_id
                WHERE e.eligible
                  AND (:county_fips IS NULL OR p.county_fips = :county_fips)
                  AND (:last_parcel_id IS NULL OR p.parcel_id > CAST(:last_parcel_id AS uuid))
                ORDER BY p.parcel_id
                LIMIT :batch_size
            )
            SELECT
                b.parcel_id,
                b.county_fips,
                b.acreage,
                b.lot_sf,
                b.jurisdiction_id::text AS jurisdiction_id,
                b.confidence,
                b.max_density_du_ac,
                b.max_far,
                b.base_parking_per_unit,
                b.height_1mi_ft,
                b.height_1mi_stories,
                sz.max_height_ft AS subject_height_ft,
                sz.max_height_stories AS subject_height_stories,
                sz.max_lot_coverage AS subject_max_lot_coverage,
                sz.min_lot_sf AS subject_min_lot_sf,
                sz.front_setback_ft AS subject_front_setback_ft,
                sz.side_setback_ft AS subject_side_setback_ft,
                sz.rear_setback_ft AS subject_rear_setback_ft,
                (sz.district_id IS NOT NULL) AS subject_zoning_matched,
                false AS zoning_geometry_available,
                false AS historic_height_data_available,
                (sf.adjacent_single_family_parcels >= 2) AS single_family_adjacency_possible,
                sf.adjacent_single_family_parcels,
                (ts.stop_count > 0) AS has_transit_stops,
                COALESCE(transit.within_quarter_mile_transit, false) AS within_quarter_mile_transit,
                b.jurisdiction_params_missing
            FROM batch b
            LEFT JOIN LATERAL (
                SELECT z.district_id, z.max_height_ft, z.max_height_stories,
                       z.max_lot_coverage, z.min_lot_sf,
                       z.front_setback_ft, z.side_setback_ft, z.rear_setback_ft
                FROM lla.zoning_districts z
                WHERE z.jurisdiction_id = b.jurisdiction_id
                  AND lower(trim(z.district_code)) IN (
                      lower(trim(coalesce(b.zoning_code, ''))),
                      lower(trim(coalesce(b.zoning_map_zone, '')))
                  )
                ORDER BY
                    CASE z.confidence
                        WHEN 'high' THEN 3
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 1
                        ELSE 0
                    END DESC
                LIMIT 1
            ) sz ON true
            LEFT JOIN LATERAL (
                SELECT count(*) AS adjacent_single_family_parcels
                FROM (
                    SELECT 1
                    FROM lla.parcels p2
                    WHERE p2.county_fips = b.county_fips
                      AND p2.parcel_id <> b.parcel_uuid
                      AND p2.geom && ST_Expand(b.geom, 0.00002)
                      AND ST_DWithin(p2.geom::geography, b.geom::geography, 5)
                      AND (
                          p2.normalized_use ILIKE '%single%family%'
                          OR p2.use_class ILIKE '%single%family%'
                          OR p2.zoning_general_use ILIKE '%single%family%'
                          OR p2.zoning_code ILIKE '%single%family%'
                          OR p2.zoning_code ILIKE 'RS%'
                          OR p2.zoning_code ILIKE 'R-1%'
                      )
                    LIMIT 2
                ) adjacent
            ) sf ON true
            CROSS JOIN transit_stop_count ts
            LEFT JOIN LATERAL (
                SELECT true AS within_quarter_mile_transit
                FROM lla.transit_stops t
                WHERE t.geom && ST_Expand(b.geom, 0.004)
                  AND ST_DWithin(t.geom::geography, b.geom::geography, 402.336)
                LIMIT 1
            ) transit ON ts.stop_count > 0
            """
        ),
        {
            "county_fips": county_fips,
            "last_parcel_id": last_parcel_id,
            "batch_size": batch_size,
        },
    ).mappings()
    return [dict(row) for row in rows]


def update_entitlement_massing(conn: Connection, results: list[MassingResult]) -> int:
    if not results:
        return 0

    values = [
        (
            result.parcel_id,
            result.max_units,
            result.max_height_stories,
            result.buildable_sf,
            result.required_parking,
            result.confidence,
            list(result.massing_flags),
            Json(result.massing_inputs),
        )
        for result in results
    ]
    dbapi_conn = conn.connection.driver_connection
    with dbapi_conn.cursor() as cursor:
        execute_values(
            cursor,
            """
            UPDATE lla.entitlement AS e
            SET
                max_units = data.max_units,
                max_height_stories = data.max_height_stories,
                buildable_sf = data.buildable_sf,
                required_parking = data.required_parking,
                params_version = data.params_version,
                confidence = data.confidence,
                massing_flags = data.massing_flags,
                massing_inputs = data.massing_inputs,
                computed_at = now()
            FROM (VALUES %s) AS data (
                parcel_id,
                max_units,
                max_height_stories,
                buildable_sf,
                required_parking,
                confidence,
                massing_flags,
                massing_inputs,
                params_version
            )
            WHERE e.parcel_id = data.parcel_id::uuid
            """,
            values,
            template=f"(%s::uuid, %s, %s, %s, %s, %s, %s::text[], %s::jsonb, '{PARAMS_VERSION}')",
            page_size=len(values),
        )
    return len(results)

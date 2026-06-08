"""v1 Live Local massing rollups and parcel-level envelope calculations."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from psycopg2.extras import execute_values
from sqlalchemy import text
from sqlalchemy.engine import Connection


PARAMS_VERSION = "v1-massing"

DEFAULT_DENSITY_DU_AC = Decimal("15")
DEFAULT_MAX_FAR = Decimal("1")
DEFAULT_HEIGHT_FT = Decimal("35")
DEFAULT_HEIGHT_STORIES = Decimal("3")
DEFAULT_PARKING_PER_UNIT = Decimal("1.5")
STORY_HEIGHT_FT = Decimal("10")
FAR_STATUTORY_MULTIPLIER = Decimal("1.5")
MAX_PLAUSIBLE_LOCAL_FAR = Decimal("10")


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

        local_far = _max_decimal(
            (row.get("max_far") for row in district_rows),
            max_value=MAX_PLAUSIBLE_LOCAL_FAR,
        )
        parking = _max_decimal(row.get("parking_per_unit") for row in district_rows)
        explicit_height_ft = _max_decimal(row.get("max_height_ft") for row in district_rows)
        explicit_height_stories = _max_decimal(row.get("max_height_stories") for row in district_rows)

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
                zoning_crosswalk_ref=f"zoning_districts:{PARAMS_VERSION}:districts={len(district_rows)}:{source_notes}",
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
            template="(%s::uuid, %s, %s, %s, %s, %s, 'v1-massing', now())",
            page_size=len(values),
        )
    return len(rollups)


def compute_massing(row: dict[str, Any]) -> MassingResult:
    acreage = _decimal(row.get("acreage")) or Decimal("0")
    lot_sf = _decimal(row.get("lot_sf")) or Decimal("0")
    density = _decimal(row.get("max_density_du_ac")) or DEFAULT_DENSITY_DU_AC
    far = _decimal(row.get("max_far")) or DEFAULT_MAX_FAR
    parking_ratio = _decimal(row.get("base_parking_per_unit")) or DEFAULT_PARKING_PER_UNIT

    explicit_height_ft = _decimal(row.get("max_height_ft"))
    explicit_height_stories = _decimal(row.get("max_height_stories"))
    height_from_stories = explicit_height_stories * STORY_HEIGHT_FT if explicit_height_stories else None
    height_ft = max([value for value in (explicit_height_ft, height_from_stories, DEFAULT_HEIGHT_FT) if value])
    height_stories = max(explicit_height_stories or (height_ft / STORY_HEIGHT_FT), DEFAULT_HEIGHT_STORIES)

    max_units = int(math.floor(density * acreage))
    buildable_sf = far * lot_sf
    required_parking = int(math.ceil(Decimal(max_units) * parking_ratio))

    missing_params = (
        row.get("jurisdiction_params_missing")
        or row.get("max_density_du_ac") is None
        or row.get("max_far") is None
        or row.get("base_parking_per_unit") is None
    )
    confidence = str(row.get("confidence") or "low")
    if missing_params or explicit_height_ft is None and explicit_height_stories is None:
        confidence = _lowest_confidence(confidence, "low")

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
    )


def ensure_entitlement_upsert_target(conn: Connection) -> None:
    conn.execute(text("SET LOCAL lock_timeout = '15s'"))
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
                GROUP BY jurisdiction_id
            )
            SELECT
                p.parcel_id::text AS parcel_id,
                p.county_fips,
                COALESCE(p.acreage, (ST_Area(p.geom::geography) * 10.76391041671) / 43560.0) AS acreage,
                COALESCE(p.lot_sf, ST_Area(p.geom::geography) * 10.76391041671) AS lot_sf,
                p.jurisdiction_id::text AS jurisdiction_id,
                e.confidence,
                jp.max_density_du_ac,
                jp.max_far,
                jp.base_parking_per_unit,
                hr.max_height_ft,
                hr.max_height_stories,
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
                params_version = 'v1-massing',
                confidence = data.confidence,
                computed_at = now()
            FROM (VALUES %s) AS data (
                parcel_id,
                max_units,
                max_height_stories,
                buildable_sf,
                required_parking,
                confidence
            )
            WHERE e.parcel_id = data.parcel_id::uuid
            """,
            values,
            template="(%s::uuid, %s, %s, %s, %s, %s)",
            page_size=len(values),
        )
    return len(results)

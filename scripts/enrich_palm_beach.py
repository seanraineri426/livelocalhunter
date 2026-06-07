#!/usr/bin/env python3
"""Enrich Palm Beach candidate parcels with jurisdiction and GIS QA.

This script is intentionally county-specific because the source layers and
audit fields are county-specific. It stages public Palm Beach ArcGIS polygons
in temporary PostGIS tables, assigns lla.parcels.jurisdiction_id by parcel
point-on-surface, backfills missing area from parcel geometry, and reports
candidate quality plus rescue-bucket FLU/zoning joins.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import bindparam, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.db import get_engine  # noqa: E402
from lla.municipalities import all_municipalities  # noqa: E402


COUNTY_FIPS = "12099"
PBC_MUNICIPALITIES_URL = (
    "https://maps.co.palm-beach.fl.us/arcgis/rest/services/"
    "PZB/Municipalities/FeatureServer/0"
)
PBC_FLU_URL = "https://maps.co.palm-beach.fl.us/arcgis/rest/services/Ags/3/MapServer/26"
PBC_ZONING_URL = "https://maps.co.palm-beach.fl.us/arcgis/rest/services/Ags/3/MapServer/42"

RESCUE_BUCKETS = ("zoning_rescue_commercial", "zoning_rescue_industrial")


def _request_json(url: str, params: dict[str, Any], *, timeout: int = 60) -> dict[str, Any]:
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"{url}: {payload['error']}")
    return payload


def fetch_geojson_features(
    layer_url: str,
    *,
    out_fields: str,
    where: str = "1=1",
    order_by: str = "OBJECTID ASC",
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    """Fetch an ArcGIS layer as GeoJSON using resultOffset pagination."""
    query_url = f"{layer_url}/query"
    features: list[dict[str, Any]] = []
    offset = 0
    while True:
        payload = _request_json(
            query_url,
            {
                "f": "geojson",
                "where": where,
                "outFields": out_fields,
                "returnGeometry": "true",
                "outSR": "4326",
                "orderByFields": order_by,
                "resultOffset": offset,
                "resultRecordCount": page_size,
            },
        )
        page = payload.get("features") or []
        features.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)
    return features


def _feature_row(feature: dict[str, Any], field_names: Iterable[str]) -> dict[str, Any] | None:
    geometry = feature.get("geometry")
    if not geometry:
        return None
    props = feature.get("properties") or {}
    attrs = {name: props.get(name) for name in field_names}
    attrs["geometry"] = json.dumps(geometry)
    return attrs


def seed_palm_beach_jurisdictions(conn) -> None:
    upsert_jurisdiction = text(
        """
        INSERT INTO lla.jurisdictions (name, county_fips, jurisdiction_type, gis_name, is_unincorporated)
        VALUES (:name, :county_fips, 'municipality', :gis_name, :is_unincorporated)
        ON CONFLICT (name, county_fips) DO UPDATE
            SET gis_name = EXCLUDED.gis_name,
                is_unincorporated = EXCLUDED.is_unincorporated
        RETURNING jurisdiction_id
        """
    )
    upsert_source = text(
        """
        INSERT INTO lla.jurisdiction_sources (jurisdiction_id, source_type, crawl_status)
        VALUES (:jid, 'zoning_code', 'pending')
        ON CONFLICT (jurisdiction_id, source_type) DO NOTHING
        """
    )
    for municipality in all_municipalities():
        if municipality.county_fips != COUNTY_FIPS:
            continue
        jid = conn.execute(
            upsert_jurisdiction,
            {
                "name": municipality.name,
                "county_fips": municipality.county_fips,
                "gis_name": municipality.gis_name,
                "is_unincorporated": municipality.is_unincorporated,
            },
        ).scalar_one()
        conn.execute(upsert_source, {"jid": jid})


def stage_municipalities(conn, features: list[dict[str, Any]]) -> None:
    conn.execute(
        text(
            """
            CREATE TEMP TABLE pb_municipalities (
                source_name text,
                geom geometry(MultiPolygon, 4326)
            ) ON COMMIT DROP
            """
        )
    )
    rows = [
        row
        for feature in features
        if (row := _feature_row(feature, ("FNAME",))) is not None
    ]
    conn.execute(
        text(
            """
            INSERT INTO pb_municipalities (source_name, geom)
            VALUES (
                upper(trim(:FNAME)),
                ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326))
            )
            """
        ),
        rows,
    )
    conn.execute(text("CREATE INDEX ON pb_municipalities USING gist (geom)"))


def stage_planning_layer(
    conn,
    *,
    table_name: str,
    features: list[dict[str, Any]],
    field_names: tuple[str, ...],
) -> None:
    conn.execute(
        text(
            f"""
            CREATE TEMP TABLE {table_name} (
                attrs jsonb,
                geom geometry(MultiPolygon, 4326)
            ) ON COMMIT DROP
            """
        )
    )
    rows = [
        row
        for feature in features
        if (row := _feature_row(feature, field_names)) is not None
    ]
    if not rows:
        return
    conn.execute(
        text(
            f"""
            INSERT INTO {table_name} (attrs, geom)
            VALUES (
                CAST(:attrs AS jsonb),
                ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326))
            )
            """
        ),
        [{"attrs": json.dumps({name: row.get(name) for name in field_names}), "geometry": row["geometry"]} for row in rows],
    )
    conn.execute(text(f"CREATE INDEX ON {table_name} USING gist (geom)"))


def assign_jurisdictions(conn) -> int:
    result = conn.execute(
        text(
            """
            WITH matches AS (
                SELECT p.parcel_id,
                       j.jurisdiction_id,
                       row_number() OVER (
                           PARTITION BY p.parcel_id
                           ORDER BY ST_Area(ST_Intersection(p.geom, m.geom)::geography) DESC
                       ) AS rn
                FROM lla.parcels p
                JOIN pb_municipalities m
                  ON ST_Intersects(ST_PointOnSurface(p.geom), m.geom)
                JOIN lla.jurisdictions j
                  ON j.county_fips = :county_fips
                 AND upper(trim(j.gis_name)) = m.source_name
                WHERE p.county_fips = :county_fips
                  AND p.is_candidate
            )
            UPDATE lla.parcels p
               SET jurisdiction_id = matches.jurisdiction_id,
                   updated_at = now()
            FROM matches
            WHERE matches.rn = 1
              AND p.parcel_id = matches.parcel_id
              AND p.jurisdiction_id IS DISTINCT FROM matches.jurisdiction_id
            """
        ),
        {"county_fips": COUNTY_FIPS},
    )
    return int(result.rowcount or 0)


def backfill_missing_area(conn) -> int:
    result = conn.execute(
        text(
            """
            UPDATE lla.parcels
               SET lot_sf = COALESCE(lot_sf, ST_Area(geom::geography) * 10.76391041671),
                   acreage = COALESCE(acreage, (ST_Area(geom::geography) * 10.76391041671) / 43560.0),
                   updated_at = now()
             WHERE county_fips = :county_fips
               AND is_candidate
               AND (lot_sf IS NULL OR acreage IS NULL)
            """
        ),
        {"county_fips": COUNTY_FIPS},
    )
    return int(result.rowcount or 0)


def fetch_scalar(conn, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one()


def print_rows(title: str, rows: Iterable[Any]) -> None:
    print(f"\n{title}")
    for row in rows:
        print(dict(row._mapping))


def qa_report(conn, *, jurisdiction_updates: int, area_updates: int, layer_counts: dict[str, int]) -> None:
    print("Palm Beach candidate enrichment QA")
    print("=" * 38)
    print(f"county_fips: {COUNTY_FIPS}")
    print(f"municipality polygons fetched: {layer_counts['municipalities']}")
    print(f"FLU polygons fetched: {layer_counts['flu']}")
    print(f"zoning polygons fetched: {layer_counts['zoning']}")
    print(f"jurisdiction_id updates: {jurisdiction_updates}")
    print(f"area backfill updates: {area_updates}")

    totals = conn.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE is_candidate) AS candidates,
                   count(*) FILTER (WHERE is_candidate AND jurisdiction_id IS NOT NULL) AS assigned,
                   count(*) FILTER (WHERE is_candidate AND jurisdiction_id IS NULL) AS unassigned,
                   count(*) FILTER (WHERE is_candidate AND lot_sf IS NULL) AS missing_lot_sf,
                   count(*) FILTER (WHERE is_candidate AND acreage IS NULL) AS missing_acreage
            FROM lla.parcels
            WHERE county_fips = :county_fips
            """
        ),
        {"county_fips": COUNTY_FIPS},
    ).first()
    print("\nTotals")
    print(dict(totals._mapping))
    if totals and totals.candidates < 5000:
        print(
            "QA note: candidate total is low for a full Palm Beach PA roll; "
            "the current CollectionDays_Parcels property-joined source may be incomplete."
        )

    print_rows(
        "Candidate bucket distribution",
        conn.execute(
            text(
                """
                SELECT candidate_bucket, count(*) AS n
                FROM lla.parcels
                WHERE county_fips = :county_fips AND is_candidate
                GROUP BY candidate_bucket
                ORDER BY n DESC, candidate_bucket
                """
            ),
            {"county_fips": COUNTY_FIPS},
        ),
    )

    print_rows(
        "COMM/IND zoning rescue counts",
        conn.execute(
            text(
                """
                SELECT candidate_bucket,
                       count(*) AS n,
                       count(*) FILTER (WHERE use_class ILIKE '%COMM ZONING%') AS comm_zoning_text,
                       count(*) FILTER (WHERE use_class ILIKE '%IND ZONING%') AS ind_zoning_text
                FROM lla.parcels
                WHERE county_fips = :county_fips
                  AND candidate_bucket IN :buckets
                GROUP BY candidate_bucket
                ORDER BY candidate_bucket
                """
            ).bindparams(bindparam("buckets", expanding=True)),
            {"county_fips": COUNTY_FIPS, "buckets": list(RESCUE_BUCKETS)},
        ),
    )

    print_rows(
        "Rescue zoning spatial join",
        conn.execute(
            text(
                """
                WITH joined AS (
                    SELECT p.parcel_id,
                           z.attrs->>'FCODE' AS zoning_code,
                           z.attrs->>'ZONING_DESC' AS zoning_desc
                    FROM lla.parcels p
                    LEFT JOIN LATERAL (
                        SELECT attrs
                        FROM pb_zoning z
                        WHERE ST_Intersects(ST_PointOnSurface(p.geom), z.geom)
                        ORDER BY ST_Area(ST_Intersection(p.geom, z.geom)::geography) DESC
                        LIMIT 1
                    ) z ON true
                    WHERE p.county_fips = :county_fips
                      AND p.candidate_bucket IN :buckets
                )
                SELECT zoning_code, zoning_desc, count(*) AS n
                FROM joined
                GROUP BY zoning_code, zoning_desc
                ORDER BY n DESC, zoning_code NULLS LAST
                LIMIT 20
                """
            ).bindparams(bindparam("buckets", expanding=True)),
            {"county_fips": COUNTY_FIPS, "buckets": list(RESCUE_BUCKETS)},
        ),
    )

    print_rows(
        "Rescue FLU spatial join",
        conn.execute(
            text(
                """
                WITH joined AS (
                    SELECT p.parcel_id,
                           f.attrs->>'FLU_CODE' AS flu_code,
                           f.attrs->>'FLU_DESC' AS flu_desc
                    FROM lla.parcels p
                    LEFT JOIN LATERAL (
                        SELECT attrs
                        FROM pb_flu f
                        WHERE ST_Intersects(ST_PointOnSurface(p.geom), f.geom)
                        ORDER BY ST_Area(ST_Intersection(p.geom, f.geom)::geography) DESC
                        LIMIT 1
                    ) f ON true
                    WHERE p.county_fips = :county_fips
                      AND p.candidate_bucket IN :buckets
                )
                SELECT flu_code, flu_desc, count(*) AS n
                FROM joined
                GROUP BY flu_code, flu_desc
                ORDER BY n DESC, flu_code NULLS LAST
                LIMIT 20
                """
            ).bindparams(bindparam("buckets", expanding=True)),
            {"county_fips": COUNTY_FIPS, "buckets": list(RESCUE_BUCKETS)},
        ),
    )

    print_rows(
        "Jurisdiction distribution",
        conn.execute(
            text(
                """
                SELECT COALESCE(j.name, '<unassigned>') AS jurisdiction, count(*) AS n
                FROM lla.parcels p
                LEFT JOIN lla.jurisdictions j ON j.jurisdiction_id = p.jurisdiction_id
                WHERE p.county_fips = :county_fips AND p.is_candidate
                GROUP BY COALESCE(j.name, '<unassigned>')
                ORDER BY n DESC, jurisdiction
                LIMIT 50
                """
            ),
            {"county_fips": COUNTY_FIPS},
        ),
    )

    print_rows(
        "Sample unassigned candidate parcels",
        conn.execute(
            text(
                """
                SELECT source_parcel_id,
                       candidate_bucket,
                       use_class,
                       round(lot_sf::numeric, 1) AS lot_sf,
                       round(acreage::numeric, 3) AS acreage,
                       ST_AsText(ST_PointOnSurface(geom)) AS point_wkt
                FROM lla.parcels
                WHERE county_fips = :county_fips
                  AND is_candidate
                  AND jurisdiction_id IS NULL
                ORDER BY source_parcel_id
                LIMIT 20
                """
            ),
            {"county_fips": COUNTY_FIPS},
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-size", type=int, default=1000)
    args = parser.parse_args()

    municipality_features = fetch_geojson_features(
        PBC_MUNICIPALITIES_URL,
        out_fields="FNAME",
        page_size=args.page_size,
    )
    flu_features = fetch_geojson_features(
        PBC_FLU_URL,
        out_fields="FLU_CODE,FLU_DESC",
        page_size=args.page_size,
    )
    zoning_features = fetch_geojson_features(
        PBC_ZONING_URL,
        out_fields="FCODE,ZONING_DESC,FNAME",
        page_size=args.page_size,
    )

    engine = get_engine()
    with engine.begin() as conn:
        seed_palm_beach_jurisdictions(conn)
        stage_municipalities(conn, municipality_features)
        stage_planning_layer(
            conn,
            table_name="pb_flu",
            features=flu_features,
            field_names=("FLU_CODE", "FLU_DESC"),
        )
        stage_planning_layer(
            conn,
            table_name="pb_zoning",
            features=zoning_features,
            field_names=("FCODE", "ZONING_DESC", "FNAME"),
        )
        jurisdiction_updates = assign_jurisdictions(conn)
        area_updates = backfill_missing_area(conn)
        qa_report(
            conn,
            jurisdiction_updates=jurisdiction_updates,
            area_updates=area_updates,
            layer_counts={
                "municipalities": len(municipality_features),
                "flu": len(flu_features),
                "zoning": len(zoning_features),
            },
        )


if __name__ == "__main__":
    main()

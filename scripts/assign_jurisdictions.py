#!/usr/bin/env python3
"""Assign Broward candidate parcel jurisdictions and FLU rescue fields."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import requests
from arcgis2geojson import arcgis2geojson
from sqlalchemy import text
from sqlalchemy.engine import Connection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS
from lla.db import get_engine, verify_postgis
from lla.municipalities import all_municipalities

BROWARD_FIPS = COUNTY_FIPS["broward"]
BROWARD_CITY_BOUNDARIES_URL = (
    "https://services.arcgis.com/JMAJrTsHNLrSsWf5/arcgis/rest/services/"
    "City_Boundaries_Outline/FeatureServer/2"
)
BROWARD_FLU_URL = (
    "https://services.arcgis.com/JMAJrTsHNLrSsWf5/ArcGIS/rest/services/"
    "FutureLandUse/FeatureServer/0"
)

FLU_CLASS_BY_CODE = {
    0: "Tribal Lands",
    1: "Water",
    5: "Conservation - Natural Reservations",
    10: "Recreation and Open Space",
    15: "Commercial Recreation",
    20: "Agricultural",
    28: "Rural Ranches",
    29: "Rural Estates",
    30: "Estate (1) Residential",
    31: "Low (2) Residential",
    32: "Low (3) Residential",
    33: "Low (5) Residential",
    34: "Palm Beach County Rural Residential-10",
    36: "Low-Medium (10) Residential",
    37: "Medium (16) Residential",
    38: "Medium-High (25) Residential",
    39: "High (50) Residential",
    40: "Community",
    60: "Commerce",
    70: "Transportation",
    100: "Activity Center",
    110: "Electrical Generation Facility",
    222: "Irregular Residential",
    444: "Mining",
    555: "Conservation - Reserve Water Supply Areas",
}
FLU_RESCUE_CODES = {15, 60, 100}

UPSERT_JURISDICTION = text(
    """
    INSERT INTO lla.jurisdictions (name, county_fips, jurisdiction_type, gis_name, is_unincorporated)
    VALUES (:name, :county_fips, 'municipality', :gis_name, :is_unincorporated)
    ON CONFLICT (name, county_fips) DO UPDATE
        SET gis_name = EXCLUDED.gis_name,
            is_unincorporated = EXCLUDED.is_unincorporated
    """
)


def arcgis_request(url: str, params: dict[str, Any], *, retries: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=90)
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(f"{url}: {payload['error']}")
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"ArcGIS request failed: {url}") from last_error


def fetch_arcgis_features(
    layer_url: str,
    *,
    out_fields: str,
    order_by: str,
    page_size: int,
) -> Iterator[dict[str, Any]]:
    offset = 0
    while True:
        payload = arcgis_request(
            f"{layer_url}/query",
            {
                "f": "json",
                "where": "1=1",
                "outFields": out_fields,
                "returnGeometry": "true",
                "outSR": "4326",
                "orderByFields": order_by,
                "resultOffset": offset,
                "resultRecordCount": page_size,
            },
        )
        features = payload.get("features") or []
        if not features:
            return
        for feature in features:
            if feature.get("geometry"):
                yield arcgis2geojson(feature)
        offset += len(features)
        if not payload.get("exceededTransferLimit") and len(features) < page_size:
            return


def seed_broward_jurisdictions(conn: Connection) -> int:
    count = 0
    for muni in all_municipalities():
        if muni.county_fips != BROWARD_FIPS:
            continue
        conn.execute(
            UPSERT_JURISDICTION,
            {
                "name": muni.name,
                "county_fips": muni.county_fips,
                "gis_name": muni.gis_name,
                "is_unincorporated": muni.is_unincorporated,
            },
        )
        count += 1
    return count


def load_boundaries(conn: Connection, *, page_size: int) -> int:
    conn.execute(
        text(
            """
            CREATE TEMP TABLE tmp_broward_boundaries (
                gis_name TEXT NOT NULL,
                geom GEOMETRY(MultiPolygon, 4326) NOT NULL
            ) ON COMMIT DROP
            """
        )
    )
    rows = []
    for feature in fetch_arcgis_features(
        BROWARD_CITY_BOUNDARIES_URL,
        out_fields="CITYNAME",
        order_by="OBJECTID ASC",
        page_size=page_size,
    ):
        props = feature.get("properties") or {}
        geom = feature.get("geometry")
        city = str(props.get("CITYNAME") or "").strip().upper()
        if city and geom:
            rows.append({"gis_name": city, "geometry": json.dumps(geom)})
    if rows:
        conn.execute(
            text(
                """
                INSERT INTO tmp_broward_boundaries (gis_name, geom)
                VALUES (:gis_name, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)))
                """
            ),
            rows,
        )
    conn.execute(text("CREATE INDEX tmp_broward_boundaries_geom_idx ON tmp_broward_boundaries USING GIST (geom)"))
    return len(rows)


def assign_jurisdictions(conn: Connection) -> int:
    result = conn.execute(
        text(
            """
            WITH matched AS (
                SELECT DISTINCT ON (p.parcel_id)
                       p.parcel_id,
                       j.jurisdiction_id
                FROM lla.parcels p
                JOIN tmp_broward_boundaries b
                  ON p.geom && b.geom
                 AND ST_Intersects(p.geom, b.geom)
                JOIN lla.jurisdictions j
                  ON j.county_fips = :fips
                 AND (
                    upper(j.gis_name) = b.gis_name
                    OR (
                        j.is_unincorporated
                        AND b.gis_name IN (
                            'BROWARD MUNICIPAL SERVICES DISTRICT',
                            'COUNTY REGIONAL FACILITY',
                            'UNINCORPORATED'
                        )
                    )
                 )
                WHERE p.county_fips = :fips
                  AND p.is_candidate
                ORDER BY
                    p.parcel_id,
                    ST_Area(ST_Intersection(ST_MakeValid(p.geom), ST_MakeValid(b.geom))) DESC
            )
            UPDATE lla.parcels p
               SET jurisdiction_id = matched.jurisdiction_id,
                   updated_at = now()
              FROM matched
             WHERE p.parcel_id = matched.parcel_id
               AND p.jurisdiction_id IS DISTINCT FROM matched.jurisdiction_id
            """
        ),
        {"fips": BROWARD_FIPS},
    )
    return int(result.rowcount or 0)


def load_flu(conn: Connection, *, page_size: int) -> int:
    conn.execute(
        text(
            """
            CREATE TEMP TABLE tmp_broward_flu (
                flu_code TEXT NOT NULL,
                flu_class TEXT NOT NULL,
                rescue BOOLEAN NOT NULL,
                geom GEOMETRY(MultiPolygon, 4326) NOT NULL
            ) ON COMMIT DROP
            """
        )
    )
    rows = []
    for feature in fetch_arcgis_features(
        BROWARD_FLU_URL,
        out_fields="OBJECTID_1,SLUC1,DENSITY",
        order_by="OBJECTID_1 ASC",
        page_size=page_size,
    ):
        props = feature.get("properties") or {}
        geom = feature.get("geometry")
        if props.get("SLUC1") is None or not geom:
            continue
        code = int(props["SLUC1"])
        rows.append(
            {
                "flu_code": str(code),
                "flu_class": FLU_CLASS_BY_CODE.get(code, f"Unknown SLUC1 {code}"),
                "rescue": code in FLU_RESCUE_CODES,
                "geometry": json.dumps(geom),
            }
        )
    if rows:
        conn.execute(
            text(
                """
                INSERT INTO tmp_broward_flu (flu_code, flu_class, rescue, geom)
                VALUES (
                    :flu_code,
                    :flu_class,
                    :rescue,
                    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326))
                )
                """
            ),
            rows,
        )
    conn.execute(text("CREATE INDEX tmp_broward_flu_geom_idx ON tmp_broward_flu USING GIST (geom)"))
    return len(rows)


def enrich_flu(conn: Connection) -> int:
    result = conn.execute(
        text(
            """
            WITH matched AS (
                SELECT DISTINCT ON (p.parcel_id)
                       p.parcel_id,
                       f.flu_code,
                       f.flu_class,
                       f.rescue
                FROM lla.parcels p
                JOIN tmp_broward_flu f
                  ON p.geom && f.geom
                 AND ST_Intersects(p.geom, f.geom)
                WHERE p.county_fips = :fips
                  AND p.is_candidate
                ORDER BY
                    p.parcel_id,
                    ST_Area(ST_Intersection(ST_MakeValid(p.geom), ST_MakeValid(f.geom))) DESC
            )
            UPDATE lla.parcels p
               SET flu_code = matched.flu_code,
                   flu_class = matched.flu_class,
                   zoning_rescue = matched.rescue,
                   zoning_rescue_source = :source_url,
                   zoning_rescue_updated_at = now(),
                   updated_at = now()
              FROM matched
             WHERE p.parcel_id = matched.parcel_id
               AND (
                    p.flu_code IS DISTINCT FROM matched.flu_code
                    OR p.flu_class IS DISTINCT FROM matched.flu_class
                    OR p.zoning_rescue IS DISTINCT FROM matched.rescue
                    OR p.zoning_rescue_source IS DISTINCT FROM :source_url
               )
            """
        ),
        {"fips": BROWARD_FIPS, "source_url": BROWARD_FLU_URL},
    )
    return int(result.rowcount or 0)


def qa_counts(conn: Connection) -> dict[str, Any]:
    summary = conn.execute(
        text(
            """
            SELECT
                count(*) FILTER (WHERE is_candidate) AS candidates,
                count(*) FILTER (WHERE is_candidate AND jurisdiction_id IS NOT NULL) AS assigned,
                count(*) FILTER (WHERE is_candidate AND jurisdiction_id IS NULL) AS unassigned,
                count(*) FILTER (WHERE is_candidate AND flu_code IS NOT NULL) AS flu_enriched,
                count(*) FILTER (WHERE is_candidate AND zoning_rescue) AS zoning_rescue
            FROM lla.parcels
            WHERE county_fips = :fips
            """
        ),
        {"fips": BROWARD_FIPS},
    ).mappings().one()
    flu = conn.execute(
        text(
            """
            SELECT flu_class, zoning_rescue, count(*) AS parcels
            FROM lla.parcels
            WHERE county_fips = :fips
              AND is_candidate
              AND flu_class IS NOT NULL
            GROUP BY flu_class, zoning_rescue
            ORDER BY parcels DESC, flu_class
            """
        ),
        {"fips": BROWARD_FIPS},
    ).mappings().all()
    buckets = conn.execute(
        text(
            """
            SELECT candidate_bucket, count(*) AS parcels
            FROM lla.parcels
            WHERE county_fips = :fips
              AND is_candidate
            GROUP BY candidate_bucket
            ORDER BY parcels DESC, candidate_bucket
            """
        ),
        {"fips": BROWARD_FIPS},
    ).mappings().all()
    return {
        **dict(summary),
        "flu_classes": [dict(row) for row in flu],
        "candidate_buckets": [dict(row) for row in buckets],
    }


def print_qa(counts: dict[str, Any]) -> None:
    print("\nQA counts")
    print(f"  candidates:             {counts['candidates']}")
    print(f"  assigned jurisdictions: {counts['assigned']}")
    print(f"  unassigned candidates:  {counts['unassigned']}")
    print(f"  FLU enriched:           {counts['flu_enriched']}")
    print(f"  zoning rescue flagged:  {counts['zoning_rescue']}")
    print("\nCandidate buckets")
    for row in counts["candidate_buckets"]:
        print(f"  {row['candidate_bucket'] or '(null)':36s} {row['parcels']}")
    print("\nFLU classes")
    for row in counts["flu_classes"]:
        label = "rescue" if row["zoning_rescue"] else "review"
        print(f"  {row['flu_class'] or '(null)':44s} {label:6s} {row['parcels']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--county", required=True, choices=["broward"])
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--skip-flu", action="store_true")
    args = parser.parse_args()

    engine = get_engine()
    print(f"PostGIS: {verify_postgis(engine)}")
    print(f"Broward city boundary source: {BROWARD_CITY_BOUNDARIES_URL}")
    if not args.skip_flu:
        print(f"Broward FLU source: {BROWARD_FLU_URL}")

    with engine.begin() as conn:
        print(f"Seeded/verified Broward jurisdictions: {seed_broward_jurisdictions(conn)}")
        print(f"Loaded Broward boundary polygons: {load_boundaries(conn, page_size=args.page_size)}")
        print(f"Jurisdiction rows changed: {assign_jurisdictions(conn)}")
        if not args.skip_flu:
            print(f"Loaded Broward FLU polygons: {load_flu(conn, page_size=args.page_size)}")
            print(f"FLU rows changed: {enrich_flu(conn)}")
        counts = qa_counts(conn)

    print_qa(counts)


if __name__ == "__main__":
    main()

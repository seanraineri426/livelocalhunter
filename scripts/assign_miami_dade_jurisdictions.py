#!/usr/bin/env python3
"""Assign Miami-Dade candidate parcels to jurisdictions and zoning layer 19.

The source is Miami-Dade MD_LandInformation/MapServer/19 (Municipal Zoning).
It carries both municipality boundaries via MUNICNAME and the audit-recommended
zoning attributes GENRLLUTYPE/ZONE, so the script stages that layer in a temp
PostGIS table and updates candidate parcels by dominant intersection area.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.arcgis import fetch_features
from lla.db import get_engine
from lla.gis_sources import get_zoning_source
from lla.municipalities import all_municipalities

COUNTY_KEY = "miami_dade"
COUNTY_FIPS = "12086"
DEFAULT_LOG = Path("/tmp/lla_miami_dade_enrich.log")

# General-use families that can rescue or confirm Live Local commercial,
# industrial, office, mixed-use, and urban-center candidates. This is an audit
# signal, not a final eligibility determination.
ZONING_RESCUE_USES = ("C", "CR", "I", "IC", "IR", "O", "RC", "RCI", "RO", "UC")


UPSERT_JURISDICTION = text(
    """
    INSERT INTO lla.jurisdictions (name, county_fips, jurisdiction_type, gis_name, is_unincorporated)
    VALUES (:name, :county_fips, 'municipality', :gis_name, :is_unincorporated)
    ON CONFLICT (name, county_fips) DO UPDATE
        SET gis_name = EXCLUDED.gis_name,
            is_unincorporated = EXCLUDED.is_unincorporated
    RETURNING jurisdiction_id
    """
)

UPSERT_SOURCE = text(
    """
    INSERT INTO lla.jurisdiction_sources (jurisdiction_id, source_type, provider, url, crawl_status, notes)
    VALUES (:jid, 'zoning_map', 'arcgis', :url, 'found', :notes)
    ON CONFLICT (jurisdiction_id, source_type) DO UPDATE
        SET provider = EXCLUDED.provider,
            url = EXCLUDED.url,
            crawl_status = EXCLUDED.crawl_status,
            notes = EXCLUDED.notes,
            updated_at = now()
    """
)

INSERT_TEMP_ZONING = text(
    """
    INSERT INTO temp_miami_dade_zoning (
        objectid,
        municname,
        zone,
        genrllutype,
        zonedesc,
        geom
    )
    SELECT
        :objectid,
        NULLIF(BTRIM(UPPER(:municname)), ''),
        NULLIF(BTRIM(:zone), ''),
        NULLIF(BTRIM(UPPER(:genrllutype)), ''),
        NULLIF(BTRIM(:zonedesc), ''),
        g.geom
    FROM (
        SELECT ST_Multi(
            ST_CollectionExtract(
                ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)),
                3
            )
        ) AS geom
    ) AS g
    WHERE NOT ST_IsEmpty(g.geom)
    """
)

ASSIGN_SQL = text(
    """
    WITH ranked AS (
        SELECT
            p.parcel_id,
            j.jurisdiction_id,
            z.municname,
            z.zone,
            z.genrllutype,
            z.zonedesc,
            row_number() OVER (
                PARTITION BY p.parcel_id
                ORDER BY ST_Area(ST_Intersection(p.geom, z.geom)::geography) DESC, z.objectid
            ) AS rn
        FROM lla.parcels p
        JOIN temp_miami_dade_zoning z
          ON p.geom && z.geom
         AND ST_Intersects(p.geom, z.geom)
        JOIN lla.jurisdictions j
          ON j.county_fips = :county_fips
         AND UPPER(j.gis_name) = z.municname
        WHERE p.county_fips = :county_fips
          AND p.is_candidate
    ),
    updated AS (
        UPDATE lla.parcels p
        SET jurisdiction_id = r.jurisdiction_id,
            zoning_general_use = r.genrllutype,
            zoning_map_zone = r.zone,
            zoning_map_description = r.zonedesc,
            zoning_map_municipality = r.municname,
            zoning_rescue = COALESCE(r.genrllutype = ANY(:rescue_uses), false),
            zoning_enriched_at = now(),
            updated_at = now()
        FROM ranked r
        WHERE p.parcel_id = r.parcel_id
          AND r.rn = 1
        RETURNING p.parcel_id
    )
    SELECT count(*) FROM updated
    """
)


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def ensure_miami_dade_jurisdictions(conn: Connection, zoning_url: str) -> int:
    count = 0
    notes = "Municipal zoning polygons from Miami-Dade MD_LandInformation/MapServer/19."
    for muni in all_municipalities():
        if muni.county_fips != COUNTY_FIPS:
            continue
        jurisdiction_id = conn.execute(
            UPSERT_JURISDICTION,
            {
                "name": muni.name,
                "county_fips": muni.county_fips,
                "gis_name": muni.gis_name,
                "is_unincorporated": muni.is_unincorporated,
            },
        ).scalar_one()
        conn.execute(
            UPSERT_SOURCE,
            {"jid": jurisdiction_id, "url": zoning_url, "notes": notes},
        )
        count += 1
    return count


def create_temp_zoning_table(conn: Connection) -> None:
    conn.execute(
        text(
            """
            CREATE TEMP TABLE temp_miami_dade_zoning (
                objectid INTEGER PRIMARY KEY,
                municname TEXT,
                zone TEXT,
                genrllutype TEXT,
                zonedesc TEXT,
                geom GEOMETRY(MultiPolygon, 4326) NOT NULL
            ) ON COMMIT DROP
            """
        )
    )


def feature_to_row(feature: dict[str, Any], source: Any) -> dict[str, Any] | None:
    props = feature.get("properties") or {}
    geometry = feature.get("geometry")
    objectid = props.get(source.id_field)
    if objectid is None or not geometry:
        return None
    return {
        "objectid": int(objectid),
        "municname": props.get(source.municipality_field),
        "zone": props.get(source.zone_field),
        "genrllutype": props.get(source.general_use_field),
        "zonedesc": props.get(source.description_field),
        "geometry": json.dumps(geometry),
    }


def stage_zoning(conn: Connection, *, page_size: int, limit: int) -> int:
    source = get_zoning_source(COUNTY_KEY)
    rows: list[dict[str, Any]] = []
    staged = 0
    fetch_limit = limit or 10_000_000

    for feature in fetch_features(source, limit=fetch_limit, page_size=page_size):
        row = feature_to_row(feature, source)
        if row is None:
            continue
        rows.append(row)
        if len(rows) >= 500:
            conn.execute(INSERT_TEMP_ZONING, rows)
            staged += len(rows)
            logging.info("staged %s zoning polygons", staged)
            rows = []

    if rows:
        conn.execute(INSERT_TEMP_ZONING, rows)
        staged += len(rows)

    conn.execute(text("CREATE INDEX temp_miami_dade_zoning_geom_idx ON temp_miami_dade_zoning USING GIST (geom)"))
    conn.execute(text("ANALYZE temp_miami_dade_zoning"))
    return staged


def assign_candidate_parcels(conn: Connection) -> int:
    return conn.execute(
        ASSIGN_SQL,
        {
            "county_fips": COUNTY_FIPS,
            "rescue_uses": list(ZONING_RESCUE_USES),
        },
    ).scalar_one()


def fetch_qa(conn: Connection) -> dict[str, Any]:
    candidate_count = conn.execute(
        text(
            """
            SELECT count(*)
            FROM lla.parcels
            WHERE county_fips = :county_fips
              AND is_candidate
            """
        ),
        {"county_fips": COUNTY_FIPS},
    ).scalar_one()

    assigned_count, general_use_count, zone_count, complete_zoning_count, rescue_count = conn.execute(
        text(
            """
            SELECT
                count(*) FILTER (WHERE jurisdiction_id IS NOT NULL) AS assigned_count,
                count(*) FILTER (WHERE zoning_general_use IS NOT NULL) AS general_use_count,
                count(*) FILTER (WHERE zoning_map_zone IS NOT NULL) AS zone_count,
                count(*) FILTER (WHERE zoning_general_use IS NOT NULL AND zoning_map_zone IS NOT NULL) AS complete_zoning_count,
                count(*) FILTER (WHERE zoning_rescue) AS rescue_count
            FROM lla.parcels
            WHERE county_fips = :county_fips
              AND is_candidate
            """
        ),
        {"county_fips": COUNTY_FIPS},
    ).one()

    top_buckets = conn.execute(
        text(
            """
            SELECT candidate_bucket, count(*) AS parcel_count
            FROM lla.parcels
            WHERE county_fips = :county_fips
              AND is_candidate
            GROUP BY candidate_bucket
            ORDER BY parcel_count DESC, candidate_bucket
            LIMIT 12
            """
        ),
        {"county_fips": COUNTY_FIPS},
    ).mappings().all()

    zoning_uses = conn.execute(
        text(
            """
            SELECT zoning_general_use, count(*) AS parcel_count
            FROM lla.parcels
            WHERE county_fips = :county_fips
              AND is_candidate
              AND zoning_general_use IS NOT NULL
            GROUP BY zoning_general_use
            ORDER BY parcel_count DESC, zoning_general_use
            LIMIT 12
            """
        ),
        {"county_fips": COUNTY_FIPS},
    ).mappings().all()

    unassigned = conn.execute(
        text(
            """
            SELECT
                source_parcel_id,
                candidate_bucket,
                zoning_code,
                round(lot_sf::numeric, 1) AS lot_sf,
                ST_AsText(ST_PointOnSurface(geom)) AS sample_point
            FROM lla.parcels
            WHERE county_fips = :county_fips
              AND is_candidate
              AND jurisdiction_id IS NULL
            ORDER BY source_parcel_id
            LIMIT 10
            """
        ),
        {"county_fips": COUNTY_FIPS},
    ).mappings().all()

    return {
        "candidate_count": candidate_count,
        "assigned_count": assigned_count,
        "general_use_count": general_use_count,
        "zone_count": zone_count,
        "complete_zoning_count": complete_zoning_count,
        "rescue_count": rescue_count,
        "assigned_rate": (assigned_count / candidate_count) if candidate_count else 0,
        "general_use_rate": (general_use_count / candidate_count) if candidate_count else 0,
        "zone_rate": (zone_count / candidate_count) if candidate_count else 0,
        "complete_zoning_rate": (complete_zoning_count / candidate_count) if candidate_count else 0,
        "top_buckets": [dict(row) for row in top_buckets],
        "zoning_uses": [dict(row) for row in zoning_uses],
        "unassigned_sample": [dict(row) for row in unassigned],
    }


def log_qa(qa: dict[str, Any]) -> None:
    logging.info("QA candidate_count=%s", qa["candidate_count"])
    logging.info(
        "QA jurisdiction_assigned=%s (%.2f%%)",
        qa["assigned_count"],
        qa["assigned_rate"] * 100,
    )
    logging.info(
        "QA zoning_general_use_fill=%s (%.2f%%)",
        qa["general_use_count"],
        qa["general_use_rate"] * 100,
    )
    logging.info(
        "QA zoning_zone_fill=%s (%.2f%%), complete_zoning_fill=%s (%.2f%%), zoning_rescue=%s",
        qa["zone_count"],
        qa["zone_rate"] * 100,
        qa["complete_zoning_count"],
        qa["complete_zoning_rate"] * 100,
        qa["rescue_count"],
    )
    logging.info("QA top candidate buckets:")
    for row in qa["top_buckets"]:
        logging.info("  %-36s %s", row["candidate_bucket"], row["parcel_count"])
    logging.info("QA top zoning general-use values:")
    for row in qa["zoning_uses"]:
        logging.info("  %-12s %s", row["zoning_general_use"], row["parcel_count"])
    logging.info("QA unassigned sample:")
    for row in qa["unassigned_sample"]:
        logging.info("  %s", row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-size", type=int, default=1000, help="ArcGIS page size")
    parser.add_argument("--limit-zoning", type=int, default=0, help="Limit zoning polygons for testing; 0 means all")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args()

    setup_logging(args.log_file)
    source = get_zoning_source(COUNTY_KEY)
    logging.info("Starting Miami-Dade candidate jurisdiction/zoning enrichment")
    logging.info("Zoning source: %s", source.url)
    logging.info("Log file: %s", args.log_file)

    engine = get_engine()
    with engine.begin() as conn:
        seeded = ensure_miami_dade_jurisdictions(conn, source.url)
        logging.info("Ensured %s Miami-Dade municipality jurisdiction rows", seeded)
        create_temp_zoning_table(conn)
        staged = stage_zoning(conn, page_size=args.page_size, limit=args.limit_zoning)
        logging.info("Staged %s zoning polygons", staged)
        updated = assign_candidate_parcels(conn)
        logging.info("Updated %s Miami-Dade candidate parcels", updated)
        qa = fetch_qa(conn)

    log_qa(qa)
    logging.info("Done")


if __name__ == "__main__":
    main()

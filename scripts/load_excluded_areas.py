#!/usr/bin/env python3
"""Load conservative Live Local excluded-area overlays from public GIS.

Loaded sources:
- Miami-Dade aviation RPZ/property/noise/safety-zone layers.
- Palm Beach County airport noise zones.
- FNAI Everglades / Water Conservation Area managed-area polygons in the
  South Florida pilot counties.

Not loaded in this pass:
- Broward airport noise: official Part 150 materials are public, but the
  quickly available sources are PDF/static rather than queryable GIS polygons.
- Coastal high-hazard / waterfront: FEMA/GeoPlan flood data is queryable, but
  broad SFHA/VE polygons need a cleaner statutory mapping before they should
  make parcels ineligible.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from arcgis2geojson import arcgis2geojson
from psycopg2.extras import execute_values
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS  # noqa: E402
from lla.db import get_engine  # noqa: E402


LOG_PATH = Path("/tmp/lla_excluded_areas.log")
COUNTY_NAME_TO_FIPS = {
    "DADE": COUNTY_FIPS["miami_dade"],
    "MIAMI-DADE": COUNTY_FIPS["miami_dade"],
    "MIAMI DADE": COUNTY_FIPS["miami_dade"],
    "BROWARD": COUNTY_FIPS["broward"],
    "PALM BEACH": COUNTY_FIPS["palm_beach"],
}
COUNTY_LABELS = {value: key for key, value in COUNTY_FIPS.items()}


@dataclass(frozen=True)
class ExcludedAreaSource:
    key: str
    url: str
    area_type: str
    source_label: str
    county_fips: str | None
    where: str = "1=1"
    out_fields: tuple[str, ...] = ("*",)
    order_by: str = "OBJECTID ASC"
    name_fields: tuple[str, ...] = ("NAME", "FNAME", "MANAME", "MAJORMA")
    source_id_field: str = "OBJECTID"
    county_field: str | None = None
    notes: str = ""


MIAMI_DADE_AVIATION_URL = "https://gisweb.miamidade.gov/arcgis/rest/services/LandManagement/MD_AviationLandUse/MapServer"
SOURCES: tuple[ExcludedAreaSource, ...] = (
    ExcludedAreaSource(
        key="miami_dade_aviation_rpz",
        url=f"{MIAMI_DADE_AVIATION_URL}/0",
        area_type="airport",
        source_label="Miami-Dade Aviation Land Use - Runway Protection Zone",
        county_fips=COUNTY_FIPS["miami_dade"],
        out_fields=("OBJECTID", "NAME", "APCODE"),
        notes="Runway Protection Zone (RPZ) polygons from Miami-Dade Aviation Department.",
    ),
    ExcludedAreaSource(
        key="miami_dade_aviation_property",
        url=f"{MIAMI_DADE_AVIATION_URL}/2",
        area_type="airport",
        source_label="Miami-Dade Aviation Land Use - Airport Property Line",
        county_fips=COUNTY_FIPS["miami_dade"],
        out_fields=("OBJECTID", "NAME", "APCODE"),
        notes="Airport property-line polygons from Miami-Dade Aviation Department.",
    ),
    ExcludedAreaSource(
        key="miami_dade_aviation_noise_65",
        url=f"{MIAMI_DADE_AVIATION_URL}/3",
        area_type="airport",
        source_label="Miami-Dade Aviation Land Use - 65 dB Noise",
        county_fips=COUNTY_FIPS["miami_dade"],
        out_fields=("OBJECTID", "NAME", "APCODE"),
        notes="65 dB airport noise polygons from Miami-Dade Aviation Department.",
    ),
    ExcludedAreaSource(
        key="miami_dade_aviation_noise_75",
        url=f"{MIAMI_DADE_AVIATION_URL}/4",
        area_type="airport",
        source_label="Miami-Dade Aviation Land Use - 75 dB Noise",
        county_fips=COUNTY_FIPS["miami_dade"],
        out_fields=("OBJECTID", "NAME", "APCODE"),
        notes="75 dB airport noise polygons from Miami-Dade Aviation Department.",
    ),
    ExcludedAreaSource(
        key="miami_dade_aviation_outer_safety",
        url=f"{MIAMI_DADE_AVIATION_URL}/6",
        area_type="airport",
        source_label="Miami-Dade Aviation Land Use - Outer Safety Zone",
        county_fips=COUNTY_FIPS["miami_dade"],
        out_fields=("OBJECTID", "NAME", "APCODE"),
        notes="Outer Safety Zone polygons from Miami-Dade Aviation Department.",
    ),
    ExcludedAreaSource(
        key="miami_dade_aviation_critical_approach",
        url=f"{MIAMI_DADE_AVIATION_URL}/7",
        area_type="airport",
        source_label="Miami-Dade Aviation Land Use - Critical Approach Zone",
        county_fips=COUNTY_FIPS["miami_dade"],
        out_fields=("OBJECTID", "NAME", "APCODE"),
        notes="Critical Approach Zone polygons from Miami-Dade Aviation Department.",
    ),
    ExcludedAreaSource(
        key="palm_beach_airport_noise",
        url="https://maps.co.palm-beach.fl.us/arcgis/rest/services/Ags/3/MapServer/54",
        area_type="airport",
        source_label="Palm Beach County Airport Noise Zones",
        county_fips=COUNTY_FIPS["palm_beach"],
        out_fields=("OBJECTID", "FNAME", "FCODE"),
        notes="Airport Noise Zones layer from Palm Beach County GIS.",
    ),
    ExcludedAreaSource(
        key="fnai_everglades_wca",
        url="https://geoweb.sfwmd.gov/agsext2/rest/services/FloridaNaturalAreasInventory/FNAI_Conservation_Lands/FeatureServer/5",
        area_type="everglades",
        source_label="FNAI Everglades and Water Conservation Areas",
        county_fips=None,
        where=(
            "UPPER(COUNTY) IN ('DADE','BROWARD','PALM BEACH') AND "
            "(UPPER(MANAME) LIKE '%EVERGLADES%' OR "
            "UPPER(MAJORMA) LIKE '%EVERGLADES%' OR "
            "UPPER(MANAME) LIKE '%WATER CONSERVATION AREA%' OR "
            "UPPER(MAJORMA) LIKE '%WATER CONSERVATION AREA%' OR "
            "UPPER(MANAME) LIKE '%LOXAHATCHEE NATIONAL WILDLIFE REFUGE%' OR "
            "UPPER(MAJORMA) LIKE '%LOXAHATCHEE NATIONAL WILDLIFE REFUGE%')"
        ),
        out_fields=("OBJECTID", "MA_ID", "MANAME", "MAJORMA", "COUNTY", "MANAGING_A", "OWNER"),
        name_fields=("MANAME", "MAJORMA"),
        county_field="COUNTY",
        notes="FNAI managed conservation lands narrowed to Everglades/WCA-style names in pilot counties.",
    ),
)


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def request_json(url: str, params: dict[str, Any], *, timeout: int, retries: int) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(f"{url}: {payload['error']}")
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Request failed after {retries} attempts: {url}") from last_exc


def fetch_features(source: ExcludedAreaSource, *, page_size: int, timeout: int) -> Iterable[dict[str, Any]]:
    query_url = f"{source.url}/query"
    offset = 0
    while True:
        params = {
            "f": "json",
            "where": source.where,
            "outFields": ",".join(source.out_fields),
            "returnGeometry": "true",
            "outSR": "4326",
            "orderByFields": source.order_by,
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }
        payload = request_json(query_url, params, timeout=timeout, retries=3)
        features = payload.get("features") or []
        if not features:
            return
        for feature in features:
            if feature.get("geometry"):
                yield arcgis2geojson(feature)
        offset += len(features)
        if not payload.get("exceededTransferLimit") and len(features) < page_size:
            return


def feature_name(source: ExcludedAreaSource, properties: dict[str, Any]) -> str | None:
    for field in source.name_fields:
        value = properties.get(field)
        if value not in (None, ""):
            return str(value)
    return None


def county_fips_values(source: ExcludedAreaSource, properties: dict[str, Any]) -> list[str | None]:
    if source.county_fips:
        return [source.county_fips]
    raw = str(properties.get(source.county_field or "") or "").upper()
    found = [fips for label, fips in COUNTY_NAME_TO_FIPS.items() if label in raw]
    return sorted(set(found)) or [None]


def source_feature_id(source: ExcludedAreaSource, properties: dict[str, Any], county_fips: str | None) -> str:
    value = properties.get(source.source_id_field)
    if value is None:
        value = properties.get("MA_ID") or properties.get("GlobalID") or feature_name(source, properties)
    base = str(value)
    if source.county_fips is None and county_fips:
        return f"{base}:{county_fips}"
    return base


def normalize_rows(source: ExcludedAreaSource, feature: dict[str, Any]) -> list[dict[str, Any]]:
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry")
    if not geometry:
        return []
    rows = []
    for county_fips in county_fips_values(source, properties):
        rows.append(
            {
                "geom": json.dumps(geometry),
                "area_type": source.area_type,
                "source": f"arcgis:{source.key}",
                "county_fips": county_fips,
                "name": feature_name(source, properties),
                "source_id": source_feature_id(source, properties, county_fips),
                "source_url": source.url,
                "notes": source.notes,
            }
        )
    return rows


def fetch_all_rows(*, page_size: int, timeout: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in SOURCES:
        source_rows: list[dict[str, Any]] = []
        for feature in fetch_features(source, page_size=page_size, timeout=timeout):
            source_rows.extend(normalize_rows(source, feature))
        logging.info("fetched %s rows from %s", len(source_rows), source.source_label)
        rows.extend(source_rows)
    return rows


def ensure_metadata_columns(conn: Any) -> None:
    conn.execute(text((Path(__file__).resolve().parents[1] / "db" / "migrations" / "007_excluded_areas_metadata.sql").read_text()))


def replace_loaded_sources(rows: list[dict[str, Any]], *, dry_run: bool) -> None:
    if dry_run:
        return
    engine = get_engine()
    source_keys = [f"arcgis:{source.key}" for source in SOURCES]
    with engine.begin() as conn:
        ensure_metadata_columns(conn)
        conn.execute(text("DELETE FROM lla.excluded_areas WHERE source = ANY(:sources)"), {"sources": source_keys})
        if not rows:
            return
        dbapi_conn = conn.connection.driver_connection
        with dbapi_conn.cursor() as cursor:
            execute_values(
                cursor,
                """
                INSERT INTO lla.excluded_areas (
                    geom,
                    area_type,
                    source,
                    county_fips,
                    name,
                    source_id,
                    source_url,
                    notes,
                    updated_at
                )
                VALUES %s
                """,
                [
                    (
                        row["geom"],
                        row["area_type"],
                        row["source"],
                        row["county_fips"],
                        row["name"],
                        row["source_id"],
                        row["source_url"],
                        row["notes"],
                    )
                    for row in rows
                ],
                template=(
                    "(ST_Multi(ST_CollectionExtract(ST_MakeValid("
                    "ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3)), "
                    "%s, %s, %s, %s, %s, %s, %s, now())"
                ),
                page_size=500,
            )


def summarize(rows: list[dict[str, Any]]) -> None:
    by_type_county: dict[tuple[str, str], int] = Counter()
    by_source: dict[str, int] = Counter(row["source"] for row in rows)
    for row in rows:
        county = COUNTY_LABELS.get(row["county_fips"], row["county_fips"] or "unknown")
        by_type_county[(row["area_type"], county)] += 1
    logging.info("rows prepared=%s", len(rows))
    logging.info("counts by type/county:")
    for (area_type, county), count in sorted(by_type_county.items()):
        logging.info("  %s %s %s", area_type, county, count)
    logging.info("counts by source:")
    for source, count in sorted(by_source.items()):
        logging.info("  %s %s", source, count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true", help="Fetch and summarize without writing to the database")
    args = parser.parse_args()

    setup_logging()
    logging.info("loading excluded areas")
    rows = fetch_all_rows(page_size=args.page_size, timeout=args.timeout)
    summarize(rows)
    replace_loaded_sources(rows, dry_run=args.dry_run)
    logging.info("database write dry_run=%s", args.dry_run)
    logging.info("Log written to %s", LOG_PATH)


if __name__ == "__main__":
    main()

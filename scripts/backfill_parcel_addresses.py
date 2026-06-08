#!/usr/bin/env python3
"""Backfill site addresses for existing parcels from county ArcGIS sources.

This does NOT re-import geometry. It looks up the physical site address for parcels
already stored in lla.parcels (matched by source_parcel_id) and updates the additive
address columns added in migration 014. Coverage is partial: vacant land and aggregate
tracts frequently have no site address on the appraiser roll.

Usage:
    python scripts/backfill_parcel_addresses.py --county miami_dade
    python scripts/backfill_parcel_addresses.py            # all counties
    python scripts/backfill_parcel_addresses.py --refresh  # also re-check rows already populated
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests
import psycopg2.errors
from psycopg2.extras import execute_values
from sqlalchemy import text

from lla.arcgis import ArcGISError, extract_site_address
from lla.db import get_engine
from lla.gis_sources import PARCEL_SOURCES, get_source

LOG_PATH = Path("/tmp/lla_address_backfill.log")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, mode="a"), logging.StreamHandler(sys.stdout)],
    )


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _address_out_fields(source) -> list[str]:
    fields = [source.id_field]
    for field in (source.site_address_field, source.site_city_field, source.site_zip_field):
        if field:
            fields.append(field)
    return fields


def fetch_addresses(source, parcel_ids: list[str], *, timeout: int = 60, retries: int = 3) -> dict[str, dict]:
    """Query the ArcGIS service for address attributes keyed by source parcel id.

    Uses POST so the long ``IN (...)`` predicate is not truncated by GET URL limits.
    """

    quoted = ",".join("'" + pid.replace("'", "''") + "'" for pid in parcel_ids)
    data = {
        "f": "json",
        "where": f"{source.id_field} IN ({quoted})",
        "outFields": ",".join(_address_out_fields(source)),
        "returnGeometry": "false",
    }
    last_exc: Exception | None = None
    payload: dict = {}
    for attempt in range(retries):
        try:
            response = requests.post(f"{source.url}/query", data=data, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and payload.get("error"):
                raise ArcGISError(f"{source.url}: {payload['error']}")
            break
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    else:
        raise ArcGISError(f"Request failed after {retries} attempts: {source.url}") from last_exc
    out: dict[str, dict] = {}
    for feature in payload.get("features", []):
        attrs = feature.get("attributes") or {}
        pid = attrs.get(source.id_field)
        if pid is None:
            continue
        address = extract_site_address(source, attrs)
        if address["site_address"] or address["site_city"] or address["site_zip"]:
            out[str(pid)] = address
    return out


def _bulk_update_addresses(engine, rows: list[dict], *, retries: int = 3) -> int:
    """Apply all address updates in a batch with one round-trip via UPDATE ... FROM."""

    if not rows:
        return 0
    values = [
        (
            r["county_fips"],
            r["source_parcel_id"],
            r.get("site_address"),
            r.get("site_city"),
            r.get("site_zip"),
            r["address_source"],
        )
        for r in rows
    ]
    for attempt in range(retries):
        try:
            with engine.begin() as conn:
                dbapi_conn = conn.connection.driver_connection
                with dbapi_conn.cursor() as cursor:
                    execute_values(
                        cursor,
                        """
                        UPDATE lla.parcels AS p
                        SET site_address = data.site_address,
                            site_city = data.site_city,
                            site_zip = data.site_zip,
                            address_source = data.address_source,
                            address_updated_at = now(),
                            updated_at = now()
                        FROM (VALUES %s) AS data (
                            county_fips, source_parcel_id, site_address, site_city, site_zip, address_source
                        )
                        WHERE p.county_fips = data.county_fips
                          AND p.source_parcel_id = data.source_parcel_id
                        """,
                        values,
                        page_size=len(values),
                    )
            break
        except psycopg2.errors.DeadlockDetected:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return len(rows)


def backfill_county(engine, county_key: str, *, batch_size: int, refresh: bool, limit: int | None) -> dict:
    source = get_source(county_key)
    if not source.site_address_field:
        logging.info("%s has no site address field configured; skipping", county_key)
        return {"county": county_key, "candidates": 0, "matched": 0, "updated": 0}

    where_missing = "" if refresh else "AND site_address IS NULL AND site_city IS NULL AND site_zip IS NULL"
    with engine.connect() as conn:
        ids = conn.execute(
            text(
                f"""
                SELECT source_parcel_id
                FROM lla.parcels
                WHERE county_fips = :fips AND is_candidate {where_missing}
                ORDER BY source_parcel_id
                """
            ),
            {"fips": source.county_fips},
        ).scalars().all()

    if limit:
        ids = ids[:limit]
    logging.info("%s: %s candidate parcels to look up", county_key, len(ids))

    matched = 0
    updated = 0
    processed = 0
    for chunk in _chunks(ids, batch_size):
        try:
            addresses = fetch_addresses(source, chunk)
        except Exception as exc:  # noqa: BLE001 - log and continue on transient ArcGIS errors
            logging.warning("%s: batch failed (%s); retrying once after pause", county_key, exc)
            time.sleep(3)
            try:
                addresses = fetch_addresses(source, chunk)
            except Exception as exc2:  # noqa: BLE001
                logging.error("%s: batch failed again (%s); skipping %s ids", county_key, exc2, len(chunk))
                continue
        matched += len(addresses)
        rows = [
            {
                "county_fips": source.county_fips,
                "source_parcel_id": pid,
                "address_source": f"arcgis:{county_key}",
                **addr,
            }
            for pid, addr in addresses.items()
        ]
        if rows:
            updated += _bulk_update_addresses(engine, rows)
        processed += len(chunk)
        if processed % (batch_size * 10) == 0:
            logging.info("%s: processed %s/%s (updated %s)", county_key, processed, len(ids), updated)

    coverage = (updated / len(ids) * 100) if ids else 0.0
    logging.info(
        "%s DONE: candidates=%s matched=%s updated=%s coverage=%.1f%%",
        county_key,
        len(ids),
        matched,
        updated,
        coverage,
    )
    return {"county": county_key, "candidates": len(ids), "matched": matched, "updated": updated, "coverage_pct": coverage}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--county", choices=sorted(PARCEL_SOURCES), help="County key; default all")
    parser.add_argument("--batch-size", type=int, default=200, help="Parcel ids per ArcGIS request")
    parser.add_argument("--refresh", action="store_true", help="Re-check rows that already have an address")
    parser.add_argument("--limit", type=int, default=0, help="Max candidate parcels per county; 0 means all")
    args = parser.parse_args()

    setup_logging()
    engine = get_engine()
    counties = [args.county] if args.county else sorted(PARCEL_SOURCES)
    limit = args.limit or None
    summary = []
    for county_key in counties:
        summary.append(
            backfill_county(engine, county_key, batch_size=args.batch_size, refresh=args.refresh, limit=limit)
        )
    logging.info("Backfill summary: %s", summary)
    logging.info("Log written to %s", LOG_PATH)


if __name__ == "__main__":
    main()

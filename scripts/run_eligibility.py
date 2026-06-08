#!/usr/bin/env python3
"""Run v0 Live Local eligibility for candidate parcels."""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from psycopg2.extras import execute_values
from sqlalchemy import text
from sqlalchemy.engine import Connection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS  # noqa: E402
from lla.db import get_engine  # noqa: E402
from lla.eligibility import eligibility  # noqa: E402


COUNTY_LABELS = {value: key for key, value in COUNTY_FIPS.items()}
LOG_PATH = Path("/tmp/lla_eligibility.log")

SELECT_BATCH = text(
    """
    SELECT
        p.parcel_id,
        p.county_fips,
        p.source_parcel_id,
        p.use_class,
        p.zoning_code,
        p.candidate_bucket,
        p.candidate_reason,
        p.normalized_use,
        p.zoning_general_use,
        p.zoning_map_zone,
        p.zoning_map_description,
        p.zoning_map_municipality,
        p.zoning_rescue,
        p.flu_code,
        p.flu_class,
        p.jurisdiction_id,
        :excluded_area_count AS excluded_area_count,
        CASE
            WHEN :excluded_area_count = 0 THEN false
            ELSE EXISTS (
                SELECT 1
                FROM lla.excluded_areas e
                WHERE p.geom && e.geom
                  AND ST_Intersects(p.geom, e.geom)
            )
        END AS intersects_excluded_area
    FROM lla.parcels p
    WHERE p.is_candidate
      AND (:county_fips IS NULL OR p.county_fips = :county_fips)
      AND (:last_parcel_id IS NULL OR p.parcel_id > CAST(:last_parcel_id AS uuid))
    ORDER BY p.parcel_id
    LIMIT :batch_size
    """
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


def fetch_batch(
    conn: Connection,
    *,
    county_fips: str | None,
    last_parcel_id: str | None,
    batch_size: int,
    excluded_area_count: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        SELECT_BATCH,
        {
            "county_fips": county_fips,
            "last_parcel_id": last_parcel_id,
            "batch_size": batch_size,
            "excluded_area_count": excluded_area_count,
        },
    ).mappings()
    return [dict(row) for row in rows]


def upsert_results(conn: Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    values = [
        (
            str(row["parcel_id"]),
            row["eligible"],
            row["failed_reasons"],
            row["confidence"],
        )
        for row in rows
    ]
    dbapi_conn = conn.connection.driver_connection
    with dbapi_conn.cursor() as cursor:
        execute_values(
            cursor,
            """
            INSERT INTO lla.entitlement (
                parcel_id,
                eligible,
                failed_reasons,
                statute_version,
                params_version,
                confidence,
                computed_at
            )
            VALUES %s
            ON CONFLICT (parcel_id) WHERE parcel_id IS NOT NULL
            DO UPDATE SET
                eligible = EXCLUDED.eligible,
                failed_reasons = EXCLUDED.failed_reasons,
                statute_version = EXCLUDED.statute_version,
                params_version = EXCLUDED.params_version,
                confidence = EXCLUDED.confidence,
                computed_at = now()
            """,
            values,
            template="(%s::uuid, %s, %s::text[], '2025', 'v1', %s, now())",
            page_size=len(values),
        )
    return len(rows)


def log_samples(title: str, samples: list[dict[str, Any]]) -> None:
    logging.info("%s:", title)
    for sample in samples:
        logging.info(
            "  %s %s bucket=%s confidence=%s failures=%s",
            COUNTY_LABELS.get(sample["county_fips"], sample["county_fips"]),
            sample["source_parcel_id"],
            sample.get("candidate_bucket"),
            sample["confidence"],
            sample["failed_reasons"],
        )


def run(*, county_fips: str | None, batch_size: int, limit: int | None) -> dict[str, Any]:
    engine = get_engine()
    totals: Counter[str] = Counter()
    by_county: dict[str, Counter[str]] = defaultdict(Counter)
    failures: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    eligible_samples: list[dict[str, Any]] = []
    ineligible_samples: list[dict[str, Any]] = []

    with engine.begin() as conn:
        logging.info("preparing entitlement upsert target")
        ensure_entitlement_upsert_target(conn)
        excluded_area_count = conn.execute(text("SELECT count(*) FROM lla.excluded_areas")).scalar_one()
    if excluded_area_count == 0:
        logging.warning("lla.excluded_areas is empty; exclusion gate passes with low confidence.")

    last_parcel_id: str | None = None
    processed = 0
    while True:
        remaining = None if limit is None else max(limit - processed, 0)
        if remaining == 0:
            break
        current_batch_size = batch_size if remaining is None else min(batch_size, remaining)
        with engine.begin() as conn:
            batch = fetch_batch(
                conn,
                county_fips=county_fips,
                last_parcel_id=last_parcel_id,
                batch_size=current_batch_size,
                excluded_area_count=excluded_area_count,
            )
            if not batch:
                break

            upsert_rows: list[dict[str, Any]] = []
            for parcel in batch:
                result = eligibility(parcel)
                row = {
                    "parcel_id": parcel["parcel_id"],
                    "eligible": result["eligible"],
                    "failed_reasons": result["failed_reasons"],
                    "confidence": result["confidence"],
                }
                upsert_rows.append(row)

                county = parcel["county_fips"]
                totals["total"] += 1
                totals["eligible" if result["eligible"] else "ineligible"] += 1
                by_county[county]["total"] += 1
                by_county[county]["eligible" if result["eligible"] else "ineligible"] += 1
                confidence_counts[result["confidence"]] += 1
                failures.update(result["failed_reasons"])

                sample = {
                    **parcel,
                    "eligible": result["eligible"],
                    "failed_reasons": result["failed_reasons"],
                    "confidence": result["confidence"],
                }
                if result["eligible"] and len(eligible_samples) < 5:
                    eligible_samples.append(sample)
                if not result["eligible"] and len(ineligible_samples) < 5:
                    ineligible_samples.append(sample)

            upsert_results(conn, upsert_rows)
        processed += len(batch)
        last_parcel_id = str(batch[-1]["parcel_id"])
        logging.info("processed %s parcels", processed)

    logging.info("QA total=%s eligible=%s ineligible=%s", totals["total"], totals["eligible"], totals["ineligible"])
    logging.info("QA by county:")
    for county, counts in sorted(by_county.items()):
        logging.info(
            "  %s total=%s eligible=%s ineligible=%s",
            COUNTY_LABELS.get(county, county),
            counts["total"],
            counts["eligible"],
            counts["ineligible"],
        )
    logging.info("QA top failed_reasons:")
    for reason, count in failures.most_common(10):
        logging.info("  %s %s", reason, count)
    logging.info("QA confidence: %s", dict(sorted(confidence_counts.items())))
    log_samples("QA sample eligible", eligible_samples)
    log_samples("QA sample ineligible", ineligible_samples)
    logging.info("Log written to %s", LOG_PATH)

    return {
        "totals": totals,
        "by_county": by_county,
        "failures": failures,
        "confidence": confidence_counts,
        "excluded_area_count": excluded_area_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--county", choices=sorted(COUNTY_FIPS), help="Optional county key to process")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--limit", type=int, default=0, help="Optional max candidates to process; 0 means all")
    args = parser.parse_args()

    setup_logging()
    county_fips = COUNTY_FIPS[args.county] if args.county else None
    limit = args.limit or None
    run(county_fips=county_fips, batch_size=args.batch_size, limit=limit)


if __name__ == "__main__":
    main()

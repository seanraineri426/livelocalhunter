#!/usr/bin/env python3
"""Run v1 Live Local massing for eligible parcels."""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS  # noqa: E402
from lla.db import get_engine  # noqa: E402
from lla.massing import (  # noqa: E402
    compute_massing,
    ensure_entitlement_upsert_target,
    fetch_eligible_massing_batch,
    update_entitlement_massing,
)


COUNTY_LABELS = {value: key for key, value in COUNTY_FIPS.items()}
LOG_PATH = Path("/tmp/lla_massing.log")


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


def _avg(values: list[int | Decimal]) -> float:
    if not values:
        return 0.0
    return float(mean(values))


def log_samples(samples: list[dict[str, Any]]) -> None:
    logging.info("QA sample massing:")
    for sample in samples:
        logging.info(
            "  %s parcel=%s units=%s far=%s buildable_sf=%.0f height_ft=%.1f parking_ratio=%s required_parking=%s missing_params=%s",
            COUNTY_LABELS.get(sample["county_fips"], sample["county_fips"]),
            sample["parcel_id"],
            sample["max_units"],
            sample["max_far"],
            float(sample["buildable_sf"]),
            float(sample["max_height_ft"]),
            sample["parking_ratio"],
            sample["required_parking"],
            sample["missing_params"],
        )


def run(
    *,
    county_fips: str | None,
    batch_size: int,
    limit: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    engine = get_engine()
    totals: Counter[str] = Counter()
    by_county: dict[str, Counter[str]] = defaultdict(Counter)
    units_by_county: dict[str, list[int]] = defaultdict(list)
    far_by_county: dict[str, list[Decimal]] = defaultdict(list)
    height_by_county: dict[str, list[Decimal]] = defaultdict(list)
    missing_params_by_county: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    if not dry_run:
        with engine.begin() as conn:
            ensure_entitlement_upsert_target(conn)

    last_parcel_id: str | None = None
    processed = 0
    while True:
        remaining = None if limit is None else max(limit - processed, 0)
        if remaining == 0:
            break
        current_batch_size = batch_size if remaining is None else min(batch_size, remaining)

        with engine.begin() as conn:
            batch = fetch_eligible_massing_batch(
                conn,
                county_fips=county_fips,
                last_parcel_id=last_parcel_id,
                batch_size=current_batch_size,
            )
            if not batch:
                break

            results = [compute_massing(row) for row in batch]
            if not dry_run:
                update_entitlement_massing(conn, results)

        for result in results:
            totals["eligible_processed"] += 1
            by_county[result.county_fips]["eligible_processed"] += 1
            units_by_county[result.county_fips].append(result.max_units)
            far_by_county[result.county_fips].append(result.max_far)
            height_by_county[result.county_fips].append(result.max_height_ft)
            if result.missing_params:
                totals["missing_params"] += 1
                missing_params_by_county[result.county_fips] += 1
            if len(samples) < 10:
                samples.append(
                    {
                        "county_fips": result.county_fips,
                        "parcel_id": result.parcel_id,
                        "max_units": result.max_units,
                        "max_far": result.max_far,
                        "buildable_sf": result.buildable_sf,
                        "max_height_ft": result.max_height_ft,
                        "parking_ratio": result.parking_ratio,
                        "required_parking": result.required_parking,
                        "missing_params": result.missing_params,
                    }
                )

        processed += len(batch)
        last_parcel_id = batch[-1]["parcel_id"]
        logging.info("processed %s eligible parcels", processed)

    logging.info(
        "QA total eligible_processed=%s missing_params=%s missing_params_rate=%.2f%% dry_run=%s",
        totals["eligible_processed"],
        totals["missing_params"],
        (totals["missing_params"] / totals["eligible_processed"] * 100) if totals["eligible_processed"] else 0,
        dry_run,
    )
    logging.info("QA by county:")
    for county, counts in sorted(by_county.items()):
        processed_count = counts["eligible_processed"]
        missing_count = missing_params_by_county[county]
        logging.info(
            "  %s eligible_processed=%s avg_max_units=%.1f avg_max_far=%.2f avg_height_ft=%.1f missing_params_rate=%.2f%%",
            COUNTY_LABELS.get(county, county),
            processed_count,
            _avg(units_by_county[county]),
            _avg(far_by_county[county]),
            _avg(height_by_county[county]),
            (missing_count / processed_count * 100) if processed_count else 0,
        )
    log_samples(samples)
    logging.info("Log written to %s", LOG_PATH)

    return {
        "totals": totals,
        "by_county": by_county,
        "units_by_county": units_by_county,
        "far_by_county": far_by_county,
        "height_by_county": height_by_county,
        "missing_params_by_county": missing_params_by_county,
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--county", choices=sorted(COUNTY_FIPS), help="Optional county key to process")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--limit", type=int, default=0, help="Optional max eligible parcels; 0 means all")
    parser.add_argument("--dry-run", action="store_true", help="Compute massing without writing entitlement")
    args = parser.parse_args()

    setup_logging()
    county_fips = COUNTY_FIPS[args.county] if args.county else None
    limit = args.limit or None
    run(county_fips=county_fips, batch_size=args.batch_size, limit=limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

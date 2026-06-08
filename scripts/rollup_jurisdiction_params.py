#!/usr/bin/env python3
"""Roll lla.zoning_districts into lla.jurisdiction_params for v1 massing."""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS  # noqa: E402
from lla.db import get_engine  # noqa: E402
from lla.massing import (  # noqa: E402
    fetch_zoning_districts,
    rollup_jurisdiction_params,
    upsert_jurisdiction_params,
)


COUNTY_LABELS = {value: key for key, value in COUNTY_FIPS.items()}


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def run(*, county_fips: str | None, dry_run: bool) -> dict[str, object]:
    engine = get_engine()
    with engine.begin() as conn:
        district_rows = fetch_zoning_districts(conn, county_fips=county_fips)
        rollups = rollup_jurisdiction_params(district_rows)
        written = 0 if dry_run else upsert_jurisdiction_params(conn, rollups)

    missing = Counter(field for rollup in rollups for field in rollup.missing_fields)
    by_county = Counter(rollup.county_fips for rollup in rollups)

    logging.info(
        "jurisdiction_params rollup districts=%s jurisdictions=%s written=%s dry_run=%s",
        len(district_rows),
        len(rollups),
        written,
        dry_run,
    )
    for county, count in sorted(by_county.items()):
        logging.info("  %s jurisdictions=%s", COUNTY_LABELS.get(county, county), count)
    logging.info("missing field fallbacks: %s", dict(sorted(missing.items())))

    for rollup in rollups[:10]:
        logging.info(
            "sample %s %s density=%s far=%s height_ft=%s parking=%s missing=%s",
            COUNTY_LABELS.get(rollup.county_fips, rollup.county_fips),
            rollup.jurisdiction_name,
            rollup.max_density_du_ac,
            rollup.max_far,
            rollup.max_height_ft,
            rollup.base_parking_per_unit,
            list(rollup.missing_fields),
        )

    return {
        "district_rows": len(district_rows),
        "jurisdiction_params": len(rollups),
        "written": written,
        "missing": missing,
        "by_county": by_county,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--county", choices=sorted(COUNTY_FIPS), help="Optional county key to roll up")
    parser.add_argument("--dry-run", action="store_true", help="Compute rollups without writing")
    args = parser.parse_args()

    setup_logging()
    county_fips = COUNTY_FIPS[args.county] if args.county else None
    run(county_fips=county_fips, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

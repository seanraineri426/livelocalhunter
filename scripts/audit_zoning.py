#!/usr/bin/env python3
"""Audit zoning extraction quality across all jurisdictions."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.db import get_engine


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        status = conn.execute(
            text(
                """
                SELECT crawl_status, count(*)
                FROM lla.jurisdiction_sources
                GROUP BY crawl_status ORDER BY crawl_status
                """
            )
        ).all()
        totals = conn.execute(
            text(
                """
                SELECT
                    count(DISTINCT z.jurisdiction_id) AS jurisdictions,
                    count(*) AS districts,
                    count(*) FILTER (WHERE z.max_density_du_ac IS NOT NULL) AS with_density,
                    count(*) FILTER (WHERE z.max_height_ft IS NOT NULL) AS with_height,
                    count(*) FILTER (WHERE z.max_far IS NOT NULL) AS with_far
                FROM lla.zoning_districts z
                """
            )
        ).one()
        by_county = conn.execute(
            text(
                """
                SELECT j.county_fips,
                       count(DISTINCT z.jurisdiction_id) AS cities,
                       count(z.district_id) AS districts
                FROM lla.jurisdictions j
                LEFT JOIN lla.zoning_districts z ON z.jurisdiction_id = j.jurisdiction_id
                WHERE j.jurisdiction_type = 'municipality'
                GROUP BY j.county_fips ORDER BY j.county_fips
                """
            )
        ).all()
        zero = conn.execute(
            text(
                """
                SELECT j.county_fips, j.name, s.provider, s.crawl_status, s.notes
                FROM lla.jurisdiction_sources s
                JOIN lla.jurisdictions j ON j.jurisdiction_id = s.jurisdiction_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM lla.zoning_districts z
                    WHERE z.jurisdiction_id = j.jurisdiction_id
                )
                ORDER BY j.county_fips, j.name
                """
            )
        ).all()
        thin = conn.execute(
            text(
                """
                SELECT j.name, count(z.district_id) AS n
                FROM lla.jurisdiction_sources s
                JOIN lla.jurisdictions j ON j.jurisdiction_id = s.jurisdiction_id
                LEFT JOIN lla.zoning_districts z ON z.jurisdiction_id = j.jurisdiction_id
                WHERE s.crawl_status = 'extracted'
                GROUP BY j.name
                HAVING count(z.district_id) BETWEEN 1 AND 3
                ORDER BY n, j.name
                """
            )
        ).all()

    print("=== Source crawl status ===")
    for st, n in status:
        print(f"  {st:14s} {n}")

    print("\n=== District totals ===")
    print(f"  jurisdictions with districts: {totals.jurisdictions}")
    print(f"  total district rows:          {totals.districts}")
    print(f"  with density:                 {totals.with_density}")
    print(f"  with height:                  {totals.with_height}")
    print(f"  with FAR:                     {totals.with_far}")

    print("\n=== By county ===")
    for r in by_county:
        print(f"  {r.county_fips}: {r.cities} cities, {r.districts} district rows")

    print(f"\n=== Zero districts ({len(zero)}) ===")
    for r in zero:
        note = (r.notes or "")[:50]
        print(f"  {r.county_fips} {r.name:28s} {r.provider or '-':10s} {r.crawl_status:12s} {note}")

    if thin:
        print(f"\n=== Thin extraction (1-3 districts, {len(thin)}) ===")
        for r in thin:
            print(f"  {r.name:28s} {r.n} districts")


if __name__ == "__main__":
    main()

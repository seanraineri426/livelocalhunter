#!/usr/bin/env python3
"""Seed the municipality checklist into lla.jurisdictions and create a pending
zoning_code source row for each one. This is the no-gaps coverage list:
every incorporated city + the unincorporated county in all three pilot counties.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.db import get_engine
from lla.municipalities import all_municipalities, summary

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
    INSERT INTO lla.jurisdiction_sources (jurisdiction_id, source_type, crawl_status)
    VALUES (:jid, 'zoning_code', 'pending')
    ON CONFLICT (jurisdiction_id, source_type) DO NOTHING
    """
)


def main() -> None:
    munis = all_municipalities()
    engine = get_engine()
    with engine.begin() as conn:
        for m in munis:
            jid = conn.execute(
                UPSERT_JURISDICTION,
                {
                    "name": m.name,
                    "county_fips": m.county_fips,
                    "gis_name": m.gis_name,
                    "is_unincorporated": m.is_unincorporated,
                },
            ).scalar_one()
            conn.execute(UPSERT_SOURCE, {"jid": jid})

    print(f"Seeded {len(munis)} jurisdictions (incl. unincorporated):")
    for fips, n in sorted(summary().items()):
        print(f"  {fips}: {n}")

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT j.county_fips,
                       count(*) FILTER (WHERE j.jurisdiction_type='municipality') AS municipalities,
                       count(*) FILTER (WHERE s.crawl_status='pending') AS pending_crawl
                FROM lla.jurisdictions j
                LEFT JOIN lla.jurisdiction_sources s ON s.jurisdiction_id = j.jurisdiction_id
                GROUP BY j.county_fips ORDER BY j.county_fips
                """
            )
        ).all()
    print("\nIn database:")
    for r in rows:
        print(f"  {r.county_fips}: {r.municipalities} municipalities, {r.pending_crawl} pending crawl")


if __name__ == "__main__":
    main()

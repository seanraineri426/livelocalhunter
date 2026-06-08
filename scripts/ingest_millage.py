#!/usr/bin/env python3
"""Ingest source-backed millage and opt-out context for pilot counties."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS  # noqa: E402
from lla.db import get_engine  # noqa: E402
from lla.millage_ingest import (  # noqa: E402
    COUNTY_CANNOT_OPT_OUT,
    COUNTY_NAMES,
    COUNTY_SOURCES,
    MillageAuthorityRow,
    fetch_county_millage_text,
    jurisdiction_lookup_keys,
    load_pilot_county_millage_rows,
    parse_county_millage_rows,
)

LOG_PATH = Path("/tmp/lla_millage_ingest.log")


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, mode="w"), logging.StreamHandler(sys.stdout)],
    )


def _load_jurisdiction_map(conn) -> dict[tuple[str, str], dict[str, str]]:
    rows = conn.execute(
        text(
            """
            SELECT jurisdiction_id::text, county_fips, name, jurisdiction_type
            FROM lla.jurisdictions
            WHERE county_fips IN :counties
            """
        ),
        {"counties": tuple(COUNTY_FIPS.values())},
    ).mappings()
    lookup: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        for key in jurisdiction_lookup_keys(row["name"]):
            lookup[(row["county_fips"], key)] = dict(row)
    return lookup


def _resolve_jurisdiction_id(lookup: dict[tuple[str, str], dict[str, str]], row: MillageAuthorityRow) -> str | None:
    for key in jurisdiction_lookup_keys(row.jurisdiction_name):
        match = lookup.get((row.county_fips, key))
        if match:
            return match["jurisdiction_id"]
    return None


def upsert_rows(rows: list[MillageAuthorityRow], *, dry_run: bool) -> tuple[int, list[str]]:
    unmatched: list[str] = []
    if dry_run:
        return len(rows), unmatched

    engine = get_engine()
    with engine.begin() as conn:
        tax_years = sorted({row.tax_year for row in rows})
        conn.execute(
            text(
                """
                DELETE FROM lla.millage m
                USING lla.jurisdictions j
                WHERE m.jurisdiction_id = j.jurisdiction_id
                  AND j.county_fips IN :counties
                  AND m.tax_year IN :tax_years
                """
            ),
            {"counties": tuple(COUNTY_FIPS.values()), "tax_years": tuple(tax_years)},
        )
        lookup = _load_jurisdiction_map(conn)
        written = 0
        for row in rows:
            jurisdiction_id = _resolve_jurisdiction_id(lookup, row)
            if not jurisdiction_id:
                unmatched.append(f"{row.county_fips}:{row.jurisdiction_name}")
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO lla.millage (
                        jurisdiction_id,
                        authority_name,
                        authority_type,
                        millage,
                        opted_out_middle,
                        county_has_adequate_supply,
                        tax_year,
                        jurisdiction_name,
                        effective_date,
                        opt_out_source_url,
                        millage_source_url,
                        raw,
                        updated_at
                    )
                    VALUES (
                        CAST(:jurisdiction_id AS uuid),
                        :authority_name,
                        :authority_type,
                        :millage,
                        :opted_out_middle,
                        :county_has_adequate_supply,
                        :tax_year,
                        :jurisdiction_name,
                        :effective_date,
                        :opt_out_source_url,
                        :millage_source_url,
                        CAST(:raw AS jsonb),
                        now()
                    )
                    ON CONFLICT (jurisdiction_id, authority_name, tax_year)
                    DO UPDATE SET
                        authority_type = EXCLUDED.authority_type,
                        millage = EXCLUDED.millage,
                        opted_out_middle = EXCLUDED.opted_out_middle,
                        county_has_adequate_supply = EXCLUDED.county_has_adequate_supply,
                        jurisdiction_name = EXCLUDED.jurisdiction_name,
                        effective_date = EXCLUDED.effective_date,
                        opt_out_source_url = EXCLUDED.opt_out_source_url,
                        millage_source_url = EXCLUDED.millage_source_url,
                        raw = EXCLUDED.raw,
                        updated_at = now()
                    """
                ),
                {
                    "jurisdiction_id": jurisdiction_id,
                    "authority_name": row.authority_name,
                    "authority_type": row.authority_type,
                    "millage": row.millage,
                    "opted_out_middle": row.opted_out_middle,
                    "county_has_adequate_supply": row.county_has_adequate_supply,
                    "tax_year": row.tax_year,
                    "jurisdiction_name": row.jurisdiction_name,
                    "effective_date": row.effective_date,
                    "opt_out_source_url": row.opt_out_source_url,
                    "millage_source_url": row.millage_source_url,
                    "raw": json.dumps(
                        {
                            **row.raw,
                            "county_fips": row.county_fips,
                            "source_method": row.source_method,
                            "county_name": COUNTY_NAMES[row.county_fips],
                        }
                    ),
                },
            )
            written += 1
    return written, unmatched


def summarize(rows: list[MillageAuthorityRow]) -> None:
    by_county_tax_type: Counter[tuple[str, int, str]] = Counter()
    by_county_jurisdiction: Counter[tuple[str, str]] = Counter()
    for row in rows:
        by_county_tax_type[(row.county_fips, row.tax_year, row.authority_type)] += 1
        by_county_jurisdiction[(row.county_fips, row.jurisdiction_name)] += 1
    logging.info("Rows by county/tax_year/authority_type: %s", dict(by_county_tax_type))
    logging.info("Jurisdictions with rows: %s", len(by_county_jurisdiction))
    for county_fips, config in COUNTY_SOURCES.items():
        methods = {row.source_method for row in rows if row.county_fips == county_fips}
        logging.info(
            "County %s (%s) source=%s methods=%s",
            COUNTY_NAMES[county_fips],
            county_fips,
            config["url"],
            sorted(methods),
        )
    for county_fips, context in COUNTY_CANNOT_OPT_OUT.items():
        logging.info(
            "Adequate-supply context for %s: county_has_adequate_supply=%s source=%s",
            COUNTY_NAMES[county_fips],
            context["county_has_adequate_supply"],
            context["opt_out_source_url"],
        )


def verify_db() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        summary = conn.execute(
            text(
                """
                SELECT j.county_fips, m.tax_year, m.authority_type, count(*) AS rows
                FROM lla.millage m
                JOIN lla.jurisdictions j ON j.jurisdiction_id = m.jurisdiction_id
                WHERE j.county_fips IN ('12086', '12011', '12099')
                GROUP BY j.county_fips, m.tax_year, m.authority_type
                ORDER BY j.county_fips, m.tax_year, m.authority_type
                """
            )
        ).mappings()
        logging.info("DB millage summary: %s", [dict(row) for row in summary])
        opt_counts = conn.execute(
            text(
                """
                SELECT j.county_fips,
                       count(*) FILTER (WHERE m.opted_out_middle IS TRUE) AS opt_true,
                       count(*) FILTER (WHERE m.opted_out_middle IS FALSE) AS opt_false,
                       count(*) FILTER (WHERE m.opted_out_middle IS NULL) AS opt_unknown,
                       count(*) FILTER (WHERE m.county_has_adequate_supply IS TRUE) AS adequate_true,
                       count(*) FILTER (WHERE m.county_has_adequate_supply IS NULL) AS adequate_unknown
                FROM lla.millage m
                JOIN lla.jurisdictions j ON j.jurisdiction_id = m.jurisdiction_id
                WHERE j.county_fips IN ('12086', '12011', '12099')
                GROUP BY j.county_fips
                ORDER BY j.county_fips
                """
            )
        ).mappings()
        logging.info("DB opt-out / adequate-supply counts: %s", [dict(row) for row in opt_counts])
        duplicates = conn.execute(
            text(
                """
                SELECT jurisdiction_id, authority_name, tax_year, count(*) AS n
                FROM lla.millage
                GROUP BY jurisdiction_id, authority_name, tax_year
                HAVING count(*) > 1
                """
            )
        ).mappings()
        logging.info("Duplicate millage keys: %s", [dict(row) for row in duplicates])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-firecrawl", action="store_true", help="Skip Firecrawl and use direct PDF fetch")
    parser.add_argument("--dry-run", action="store_true", help="Parse but do not write rows")
    args = parser.parse_args()

    setup_logging()
    prefer_firecrawl = not args.no_firecrawl
    rows = load_pilot_county_millage_rows(prefer_firecrawl=prefer_firecrawl)
    logging.info("Parsed %s millage authority rows", len(rows))
    summarize(rows)
    written, unmatched = upsert_rows(rows, dry_run=args.dry_run)
    if unmatched:
        by_county: dict[str, list[str]] = defaultdict(list)
        for item in sorted(set(unmatched)):
            county, name = item.split(":", 1)
            by_county[county].append(name)
        logging.warning("Unmatched jurisdictions: %s", dict(by_county))
    logging.info("Upserted %s rows dry_run=%s", written, args.dry_run)
    if not args.dry_run:
        verify_db()
    logging.info("Log written to %s", LOG_PATH)


if __name__ == "__main__":
    main()

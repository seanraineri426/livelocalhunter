#!/usr/bin/env python3
"""Ingest source-backed FHFC/Shimberg rent limits for the pilot counties."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import requests
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS  # noqa: E402
from lla.db import get_engine  # noqa: E402
from lla.firecrawl import FirecrawlClient, FirecrawlError  # noqa: E402


LOG_PATH = Path("/tmp/lla_rent_limits.log")
SHIMBERG_RESULTS_URL = "https://flhousingdata.shimberg.ufl.edu/income-and-rent-limits/results?nid=1"
FHFC_RENT_LIMITS_URL = "https://www.floridahousing.org/owners-and-managers/compliance/rent-limits"
FHFC_INCOME_LIMITS_URL = "https://www.floridahousing.org/owners-and-managers/compliance/income-limits"
HUD_HOME_RENT_LIMITS_URL = "https://www.huduser.gov/portal/datasets/HOME-Rent-limits.html"
HUD_MTSP_LIMITS_URL = "https://www.huduser.gov/portal/datasets/mtsp.html"
HUD_FMR_2026_URL = "https://www.huduser.gov/portal/datasets/fmr/fmr2026/FY2026_FMR_Schedule.pdf"
PROGRAM = "Florida Housing Rent Limits"
COUNTY_NAMES = {
    "12086": "Miami-Dade County",
    "12011": "Broward County",
    "12099": "Palm Beach County",
}


@dataclass(frozen=True)
class RentLimitRow:
    county_fips: str
    county_name: str
    year: int
    ami_band: int
    bedroom_count: int
    max_monthly_rent: Decimal
    source: str
    source_url: str
    effective_date: str | None
    raw: dict


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, mode="w"), logging.StreamHandler(sys.stdout)],
    )


def _clean_money(value: str) -> Decimal:
    return Decimal(value.replace("$", "").replace(",", "").strip())


def _fetch_with_firecrawl() -> str | None:
    try:
        client = FirecrawlClient(timeout=90)
        queries = (
            "Shimberg Florida Housing Rent Limits 2026 county AMI bedroom",
            "Florida Housing Finance Corporation 2026 income rent limits effective May 1 2026",
            "HUD 2026 HOME rent limits Florida Miami-Dade Broward Palm Beach",
            "HUD 2026 MTSP income limits Florida Miami-Dade Broward Palm Beach",
        )
        for query in queries:
            results = client.search(query, limit=5)
            logging.info("Firecrawl search returned %s results for %s", len(results), query)
            for result in results:
                logging.info("Firecrawl result: %s %s", result.get("title"), result.get("url"))
        data = client.scrape(SHIMBERG_RESULTS_URL, formats=["markdown"])
        markdown = data.get("markdown") or ""
        if "Florida Housing Rent Limits" in markdown:
            logging.info("Using Firecrawl scrape for Shimberg results page")
            return markdown
    except (FirecrawlError, RuntimeError) as exc:
        logging.warning("Firecrawl unavailable; falling back to direct Shimberg request: %s", exc)
    return None


def _fetch_direct() -> str:
    response = requests.get(
        SHIMBERG_RESULTS_URL,
        headers={"User-Agent": "LiveLocalHunter/1.0 source-ingestion"},
        timeout=60,
    )
    response.raise_for_status()
    logging.info("Using direct Shimberg results page fallback")
    return response.text


def fetch_source_text(*, prefer_firecrawl: bool) -> tuple[str, str]:
    if prefer_firecrawl:
        text_body = _fetch_with_firecrawl()
        if text_body:
            return text_body, "Firecrawl scrape of Shimberg data page"
    return _fetch_direct(), "Shimberg direct data page"


def parse_rent_limits(source_text: str, *, source_name: str) -> list[RentLimitRow]:
    header_match = re.search(r"Florida Housing Rent Limits,\s*(20\d{2})", source_text)
    if not header_match:
        raise RuntimeError("Could not find Florida Housing Rent Limits table/year in source text")
    year = int(header_match.group(1))
    rows: list[RentLimitRow] = []
    county_to_fips = {name: fips for fips, name in COUNTY_NAMES.items()}
    line_pattern = re.compile(
        r"^\|\s*(?P<county>[^|]+County)\s*\|\s*(?P<ami>\d+)%\s*\|\s*"
        r"(?P<b0>\$[\d,]+)\s*\|\s*(?P<b1>\$[\d,]+)\s*\|\s*(?P<b2>\$[\d,]+)\s*\|\s*"
        r"(?P<b3>\$[\d,]+)\s*\|\s*(?P<b4>\$[\d,]+)\s*\|"
    )
    for line in source_text.splitlines():
        match = line_pattern.match(line.strip())
        if not match:
            continue
        county = match.group("county").strip()
        if county not in county_to_fips:
            continue
        ami = int(match.group("ami"))
        if ami not in {80, 120}:
            continue
        for bedroom in range(5):
            rent = _clean_money(match.group(f"b{bedroom}"))
            rows.append(
                RentLimitRow(
                    county_fips=county_to_fips[county],
                    county_name=county,
                    year=year,
                    ami_band=ami,
                    bedroom_count=bedroom,
                    max_monthly_rent=rent,
                    source=source_name,
                    source_url=SHIMBERG_RESULTS_URL,
                    effective_date="2026-05-01" if year == 2026 else None,
                    raw={
                        "source_line": line.strip(),
                        "fhfc_rent_limits_url": FHFC_RENT_LIMITS_URL,
                        "fhfc_income_limits_url": FHFC_INCOME_LIMITS_URL,
                        "hud_reference_urls": {
                            "home_rent_limits": HUD_HOME_RENT_LIMITS_URL,
                            "mtsp_income_limits": HUD_MTSP_LIMITS_URL,
                            "fy2026_fmr_schedule": HUD_FMR_2026_URL,
                        },
                        "note": "Shimberg page cites Florida Housing Finance Corporation combined income and rent limits.",
                    },
                )
            )
    if rows:
        return rows

    # The direct Shimberg route renders HTML plus a serialized data payload.
    jsonish_pattern = re.compile(
        r'\{"county":"(?P<county>[^"]+County)","inc_ami":"(?P<ami>\d+)%",'
        r'"rent_br_0":"(?P<b0>\d+)","rent_br_1":"(?P<b1>\d+)","rent_br_2":"(?P<b2>\d+)",'
        r'"rent_br_3":"(?P<b3>\d+)","rent_br_4":"(?P<b4>\d+)"\}'
    )
    for match in jsonish_pattern.finditer(source_text):
        county = match.group("county").strip()
        if county not in county_to_fips:
            continue
        ami = int(match.group("ami"))
        if ami not in {80, 120}:
            continue
        for bedroom in range(5):
            rent = Decimal(match.group(f"b{bedroom}"))
            rows.append(
                RentLimitRow(
                    county_fips=county_to_fips[county],
                    county_name=county,
                    year=year,
                    ami_band=ami,
                    bedroom_count=bedroom,
                    max_monthly_rent=rent,
                    source=source_name,
                    source_url=SHIMBERG_RESULTS_URL,
                    effective_date="2026-05-01" if year == 2026 else None,
                    raw={
                        "source_payload": "serialized_shimberg_table",
                        "fhfc_rent_limits_url": FHFC_RENT_LIMITS_URL,
                        "fhfc_income_limits_url": FHFC_INCOME_LIMITS_URL,
                        "hud_reference_urls": {
                            "home_rent_limits": HUD_HOME_RENT_LIMITS_URL,
                            "mtsp_income_limits": HUD_MTSP_LIMITS_URL,
                            "fy2026_fmr_schedule": HUD_FMR_2026_URL,
                        },
                        "note": "Shimberg page cites Florida Housing Finance Corporation combined income and rent limits.",
                    },
                )
            )
    return rows


def upsert_rows(rows: Iterable[RentLimitRow], *, dry_run: bool) -> int:
    rows = list(rows)
    if dry_run:
        return len(rows)
    engine = get_engine()
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO lla.rent_limits (
                        county_fips,
                        county_name,
                        year,
                        program,
                        ami_band,
                        bedroom_count,
                        max_monthly_rent,
                        effective_date,
                        source,
                        source_url,
                        raw,
                        updated_at
                    )
                    VALUES (
                        :county_fips,
                        :county_name,
                        :year,
                        :program,
                        :ami_band,
                        :bedroom_count,
                        :max_monthly_rent,
                        :effective_date,
                        :source,
                        :source_url,
                        CAST(:raw AS jsonb),
                        now()
                    )
                    ON CONFLICT (county_fips, year, program, ami_band, bedroom_count)
                    DO UPDATE SET
                        county_name = EXCLUDED.county_name,
                        max_monthly_rent = EXCLUDED.max_monthly_rent,
                        effective_date = EXCLUDED.effective_date,
                        source = EXCLUDED.source,
                        source_url = EXCLUDED.source_url,
                        raw = EXCLUDED.raw,
                        updated_at = now()
                    """
                ),
                {
                    "county_fips": row.county_fips,
                    "county_name": row.county_name,
                    "year": row.year,
                    "program": PROGRAM,
                    "ami_band": row.ami_band,
                    "bedroom_count": row.bedroom_count,
                    "max_monthly_rent": row.max_monthly_rent,
                    "effective_date": row.effective_date,
                    "source": row.source,
                    "source_url": row.source_url,
                    "raw": __import__("json").dumps(row.raw),
                },
            )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-firecrawl", action="store_true", help="Skip Firecrawl and use direct Shimberg source")
    parser.add_argument("--dry-run", action="store_true", help="Parse but do not write rows")
    args = parser.parse_args()

    setup_logging()
    source_text, source_name = fetch_source_text(prefer_firecrawl=not args.no_firecrawl)
    rows = parse_rent_limits(source_text, source_name=source_name)
    count = upsert_rows(rows, dry_run=args.dry_run)
    logging.info("Parsed/upserted %s rent-limit rows dry_run=%s", count, args.dry_run)
    by_county: dict[str, int] = {}
    for row in rows:
        by_county[row.county_name] = by_county.get(row.county_name, 0) + 1
    logging.info("Rows by county: %s", by_county)
    logging.info("Source URLs: %s ; %s", SHIMBERG_RESULTS_URL, FHFC_RENT_LIMITS_URL)
    logging.info("Log written to %s", LOG_PATH)


if __name__ == "__main__":
    main()

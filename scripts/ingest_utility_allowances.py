#!/usr/bin/env python3
"""Ingest source-backed utility allowance schedules for pilot counties."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.db import get_engine  # noqa: E402
from lla.firecrawl import FirecrawlClient, FirecrawlError  # noqa: E402


LOG_PATH = Path("/tmp/lla_utility_allowances.log")
UTILITY_PROFILE = "all_tenant_paid_electric_apartment_baseline"
PROFILE_NOTE = (
    "Screening profile sums electric heating, electric cooking, other electric, "
    "air conditioning, electric water heating, water, sewer, trash, refrigerator, "
    "and range/microwave where those components are listed in the source schedule."
)


@dataclass(frozen=True)
class SourceDefinition:
    county_fips: str
    county_name: str
    year: int
    pha_name: str
    source_area: str
    unit_type: str
    source_url: str
    effective_date: str
    section_marker: str
    confidence: str
    direct_component_labels: tuple[str, ...]
    electric_contexts: tuple[str, ...]


@dataclass(frozen=True)
class UtilityAllowanceRow:
    county_fips: str
    county_name: str
    year: int
    bedroom_count: int
    allowance_monthly: Decimal
    pha_name: str
    source_area: str
    unit_type: str
    utility_profile: str
    effective_date: str
    source: str
    source_url: str
    raw: dict
    confidence: str


SOURCES = (
    SourceDefinition(
        county_fips="12086",
        county_name="Miami-Dade County",
        year=2025,
        pha_name="Miami-Dade Public Housing and Community Development",
        source_area="Miami-Dade County, FL-All",
        unit_type="Apartment With 5+ Units; High Rise",
        source_url="https://mdvoucher.com/Media/Shared/Documents/2025%20Utility%20Allowance%20Schedules.pdf",
        effective_date="2025-06-01",
        section_marker="Apartment With 5+ Units; High Rise",
        confidence="medium",
        direct_component_labels=(
            "Other Electric",
            "Air Conditioning",
            "Water",
            "Sewer",
            "Trash Collection",
            "Range/Microwave",
            "Refrigerator",
        ),
        electric_contexts=("Heating", "Cooking", "Water Heating"),
    ),
    SourceDefinition(
        county_fips="12011",
        county_name="Broward County",
        year=2026,
        pha_name="Broward County Housing Authority",
        source_area="Broward County Housing Authority jurisdiction",
        unit_type="Garden (Low-Rise-3 floors or less)",
        source_url="https://bchafl.org/wp-content/uploads/2024/06/Utility-Allowance-Schedule-2026.pdf",
        effective_date="2026-01-01",
        section_marker="Unit Type Garden(Low-Rise-3 floors or less)",
        confidence="medium",
        direct_component_labels=(
            "Air Conditioning",
            "Other Electric",
            "Water",
            "Sewer",
            "Trash Collection",
            "Refrigerator",
            "Range/Microwave",
        ),
        electric_contexts=("Heating", "Cooking", "Water Heating"),
    ),
    SourceDefinition(
        county_fips="12099",
        county_name="Palm Beach County",
        year=2026,
        pha_name="West Palm Beach Housing Authority",
        source_area="Palm Beach County",
        unit_type="Flat/Garden/High Rise",
        source_url="https://www.wpbha.org/utility/openPDF/wpbhfl/Utility_Allowance_Schedule__01.01.2026.pdf?alt=media",
        effective_date="2026-01-01",
        section_marker="Unit Type Flat/Garden/High Rise",
        confidence="medium",
        direct_component_labels=(
            "Other Electric",
            "Air Conditioning",
            "Water | b. County",
            "Sewer | b. County",
            "Trash Collection",
            "Range/Microwave",
            "Refrigerator",
        ),
        electric_contexts=("Heating", "Cooking", "Water Heating"),
    ),
)


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, mode="w"), logging.StreamHandler(sys.stdout)],
    )


def _money_values(line: str) -> list[Decimal]:
    values = re.findall(r"\$?\b(\d+(?:\.\d{2})?)\b", line)
    return [Decimal(value) for value in values[:9]]


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _section(markdown: str, marker: str) -> str:
    start = markdown.find(marker)
    if start == -1:
        raise RuntimeError(f"Could not find utility allowance section marker: {marker}")
    end = markdown.find("* * *", start)
    return markdown[start:] if end == -1 else markdown[start:end]


def _row_values(section: str, label: str) -> list[Decimal]:
    normalized_label = label.lower()
    label_parts = [part.strip().lower() for part in label.split("|") if part.strip()]
    if len(label_parts) > 1:
        lines = section.splitlines()
        for index, line in enumerate(lines):
            cells = _cells(line)
            if not cells or cells[0].lower() != label_parts[0]:
                continue
            for candidate in lines[index : index + 5]:
                candidate_cells = [cell.lower() for cell in _cells(candidate)]
                if any(cell == label_parts[1] for cell in candidate_cells[:2]):
                    values = _money_values(candidate)
                    if values:
                        return values
    for line in section.splitlines():
        cells = _cells(line)
        if cells and cells[0].lower() == normalized_label:
            values = _money_values(line)
            if values:
                return values
    raise RuntimeError(f"Could not parse utility allowance row: {label}")


def _electric_values(section: str, context: str) -> list[Decimal]:
    lines = section.splitlines()
    for index, line in enumerate(lines):
        if context.lower() not in line.lower():
            continue
        for candidate in lines[index : index + 8]:
            lowered = candidate.lower()
            if "electric" in lowered and "other electric" not in lowered and "heat pump" not in lowered:
                values = _money_values(candidate)
                if values:
                    return values
    raise RuntimeError(f"Could not parse electric utility allowance row for {context}")


def _fetch_with_firecrawl(source: SourceDefinition) -> str:
    client = FirecrawlClient(timeout=120)
    search_results = client.search(f"{source.pha_name} {source.year} utility allowance schedule", limit=3)
    for result in search_results:
        logging.info("Firecrawl result for %s: %s %s", source.county_name, result.get("title"), result.get("url"))
    data = client.scrape(source.source_url, formats=["markdown"], only_main_content=False)
    markdown = data.get("markdown") or ""
    if source.section_marker not in markdown:
        raise RuntimeError(f"Firecrawl scrape did not include expected section for {source.county_name}")
    return markdown


def parse_rows(source: SourceDefinition, markdown: str, *, source_label: str) -> list[UtilityAllowanceRow]:
    section = _section(markdown, source.section_marker)
    components: dict[str, list[Decimal]] = {}
    for context in source.electric_contexts:
        components[f"{context}: Electric"] = _electric_values(section, context)
    for label in source.direct_component_labels:
        components[label] = _row_values(section, label)

    rows: list[UtilityAllowanceRow] = []
    for bedroom in range(5):
        total = sum((values[bedroom] for values in components.values()), Decimal("0"))
        rows.append(
            UtilityAllowanceRow(
                county_fips=source.county_fips,
                county_name=source.county_name,
                year=source.year,
                bedroom_count=bedroom,
                allowance_monthly=total,
                pha_name=source.pha_name,
                source_area=source.source_area,
                unit_type=source.unit_type,
                utility_profile=UTILITY_PROFILE,
                effective_date=source.effective_date,
                source=source_label,
                source_url=source.source_url,
                raw={
                    "components": {name: [str(value) for value in values[:5]] for name, values in components.items()},
                    "profile_note": PROFILE_NOTE,
                    "section_marker": source.section_marker,
                    "scope_note": "County/PHA schedule, not parcel-specific and not a verified owner/tenant utility split.",
                },
                confidence=source.confidence,
            )
        )
    return rows


def fetch_and_parse_sources(*, dry_run: bool) -> list[UtilityAllowanceRow]:
    rows: list[UtilityAllowanceRow] = []
    for source in SOURCES:
        try:
            markdown = _fetch_with_firecrawl(source)
            source_label = "Firecrawl scrape of official utility allowance PDF"
        except (FirecrawlError, RuntimeError) as exc:
            raise RuntimeError(
                f"Unable to parse {source.county_name} utility allowance schedule from official source: {exc}"
            ) from exc
        parsed = parse_rows(source, markdown, source_label=source_label)
        rows.extend(parsed)
        logging.info("Parsed %s rows for %s dry_run=%s", len(parsed), source.county_name, dry_run)
    return rows


def upsert_rows(rows: Iterable[UtilityAllowanceRow], *, dry_run: bool) -> int:
    rows = list(rows)
    if dry_run:
        return len(rows)
    engine = get_engine()
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO lla.utility_allowances (
                        county_fips,
                        county_name,
                        year,
                        bedroom_count,
                        allowance_monthly,
                        pha_name,
                        source_area,
                        unit_type,
                        utility_profile,
                        effective_date,
                        source,
                        source_url,
                        raw,
                        confidence,
                        updated_at
                    )
                    VALUES (
                        :county_fips,
                        :county_name,
                        :year,
                        :bedroom_count,
                        :allowance_monthly,
                        :pha_name,
                        :source_area,
                        :unit_type,
                        :utility_profile,
                        :effective_date,
                        :source,
                        :source_url,
                        CAST(:raw AS jsonb),
                        :confidence,
                        now()
                    )
                    ON CONFLICT (county_fips, year, bedroom_count, pha_name, source_area, unit_type, utility_profile)
                    DO UPDATE SET
                        county_name = EXCLUDED.county_name,
                        allowance_monthly = EXCLUDED.allowance_monthly,
                        effective_date = EXCLUDED.effective_date,
                        source = EXCLUDED.source,
                        source_url = EXCLUDED.source_url,
                        raw = EXCLUDED.raw,
                        confidence = EXCLUDED.confidence,
                        updated_at = now()
                    """
                ),
                {
                    "county_fips": row.county_fips,
                    "county_name": row.county_name,
                    "year": row.year,
                    "bedroom_count": row.bedroom_count,
                    "allowance_monthly": row.allowance_monthly,
                    "pha_name": row.pha_name,
                    "source_area": row.source_area,
                    "unit_type": row.unit_type,
                    "utility_profile": row.utility_profile,
                    "effective_date": row.effective_date,
                    "source": row.source,
                    "source_url": row.source_url,
                    "raw": json.dumps(row.raw),
                    "confidence": row.confidence,
                },
            )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Parse but do not write rows")
    args = parser.parse_args()

    setup_logging()
    rows = fetch_and_parse_sources(dry_run=args.dry_run)
    count = upsert_rows(rows, dry_run=args.dry_run)
    logging.info("Parsed/upserted %s utility allowance rows dry_run=%s", count, args.dry_run)
    by_county: dict[str, int] = {}
    for row in rows:
        by_county[row.county_name] = by_county.get(row.county_name, 0) + 1
    logging.info("Rows by county: %s", by_county)
    logging.info("Log written to %s", LOG_PATH)


if __name__ == "__main__":
    main()

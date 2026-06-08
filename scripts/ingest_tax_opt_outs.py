#!/usr/bin/env python3
"""Ingest verified Missing Middle opt-out evidence for millage rows.

Only source-backed local actions belong here. Absence of an official opt-out
resolution is not enough to set opted_out_middle=false.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.db import get_engine  # noqa: E402

LOG_PATH = Path("/tmp/lla_opt_out_ingest.log")


@dataclass(frozen=True)
class VerifiedOptOutRecord:
    county_fips: str
    jurisdiction_name: str
    tax_year: int
    opted_out_middle: bool
    source_url: str
    evidence_summary: str
    authority_type: str | None = None
    authority_name: str | None = None
    resolution_number: str | None = None
    resolution_date: str | None = None


# Intentionally empty until official local actions are verified.
VERIFIED_OPT_OUTS: list[VerifiedOptOutRecord] = []


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, mode="w"), logging.StreamHandler(sys.stdout)],
    )


def _load_records(path: str | None) -> list[VerifiedOptOutRecord]:
    records = list(VERIFIED_OPT_OUTS)
    if not path:
        return records

    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, list):
        raise ValueError("Evidence JSON must be a list of records.")
    for item in raw:
        records.append(VerifiedOptOutRecord(**item))
    return records


def _validate(record: VerifiedOptOutRecord) -> None:
    if record.opted_out_middle is None:
        raise ValueError(f"{record.jurisdiction_name}: opted_out_middle must be explicit true/false")
    if not record.source_url:
        raise ValueError(f"{record.jurisdiction_name}: source_url is required")
    if not record.evidence_summary:
        raise ValueError(f"{record.jurisdiction_name}: evidence_summary is required")
    if record.opted_out_middle and not (record.resolution_number or record.resolution_date):
        raise ValueError(
            f"{record.jurisdiction_name}: verified opt-out requires resolution_number or resolution_date"
        )
    if not record.opted_out_middle and not (record.resolution_number or record.resolution_date):
        raise ValueError(
            f"{record.jurisdiction_name}: verified non-opt-out requires official action/date provenance"
        )
    if not record.authority_type and not record.authority_name:
        raise ValueError(f"{record.jurisdiction_name}: authority_type or authority_name is required")


def _raw_patch(record: VerifiedOptOutRecord) -> str:
    return json.dumps(
        {
            "opt_out_verification": {
                "opted_out_middle": record.opted_out_middle,
                "source_url": record.source_url,
                "evidence_summary": record.evidence_summary,
                "resolution_number": record.resolution_number,
                "resolution_date": record.resolution_date,
            }
        }
    )


def ingest(records: list[VerifiedOptOutRecord], *, dry_run: bool) -> int:
    for record in records:
        _validate(record)

    if not records:
        logging.info("No verified opt-out records configured; leaving opted_out_middle unchanged.")
        return 0

    engine = get_engine()
    updated = 0
    with engine.begin() as conn:
        for record in records:
            params: dict[str, Any] = {
                "county_fips": record.county_fips,
                "jurisdiction_name": record.jurisdiction_name,
                "tax_year": record.tax_year,
                "authority_type": record.authority_type,
                "authority_name": record.authority_name,
                "opted_out_middle": record.opted_out_middle,
                "source_url": record.source_url,
                "raw_patch": _raw_patch(record),
            }
            matches = conn.execute(
                text(
                    """
                    SELECT m.millage_id::text, j.name, m.authority_name, m.authority_type
                    FROM lla.millage m
                    JOIN lla.jurisdictions j ON j.jurisdiction_id = m.jurisdiction_id
                    WHERE j.county_fips = :county_fips
                      AND lower(j.name) = lower(:jurisdiction_name)
                      AND m.tax_year = :tax_year
                      AND (:authority_type IS NULL OR m.authority_type = :authority_type)
                      AND (:authority_name IS NULL OR m.authority_name = :authority_name)
                    ORDER BY m.authority_type, m.authority_name
                    """
                ),
                params,
            ).mappings().all()
            if not matches:
                logging.warning("No millage rows matched verified record: %s", record)
                continue

            logging.info(
                "Matched %s rows for %s %s tax_year=%s dry_run=%s source=%s",
                len(matches),
                record.jurisdiction_name,
                record.authority_type or record.authority_name,
                record.tax_year,
                dry_run,
                record.source_url,
            )
            if dry_run:
                updated += len(matches)
                continue

            result = conn.execute(
                text(
                    """
                    UPDATE lla.millage m
                    SET opted_out_middle = :opted_out_middle,
                        opt_out_source_url = :source_url,
                        raw = m.raw || CAST(:raw_patch AS jsonb),
                        updated_at = now()
                    FROM lla.jurisdictions j
                    WHERE j.jurisdiction_id = m.jurisdiction_id
                      AND j.county_fips = :county_fips
                      AND lower(j.name) = lower(:jurisdiction_name)
                      AND m.tax_year = :tax_year
                      AND (:authority_type IS NULL OR m.authority_type = :authority_type)
                      AND (:authority_name IS NULL OR m.authority_name = :authority_name)
                    """
                ),
                params,
            )
            updated += result.rowcount or 0
    logging.info("Updated %s millage rows dry_run=%s", updated, dry_run)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-json", help="Optional JSON list of verified opt-out records")
    parser.add_argument("--dry-run", action="store_true", help="Validate and match records without updating")
    args = parser.parse_args()

    setup_logging()
    records = _load_records(args.evidence_json)
    updated = ingest(records, dry_run=args.dry_run)
    logging.info("Log written to %s", LOG_PATH)
    logging.info("Rows matched/updated: %s", updated)


if __name__ == "__main__":
    main()

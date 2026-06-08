#!/usr/bin/env python3
"""Read-only batch QA for parcel feasibility readiness."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.db import get_engine  # noqa: E402


def _rows(conn, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]


def collect_summary() -> dict[str, Any]:
    engine = get_engine()
    with engine.connect() as conn:
        eligible_large = _rows(
            conn,
            """
            SELECT
                p.parcel_id::text,
                p.county_fips,
                p.source_parcel_id,
                e.max_units,
                e.confidence,
                e.massing_flags
            FROM lla.parcels p
            JOIN lla.entitlement e ON e.parcel_id = p.parcel_id
            WHERE e.eligible IS TRUE
              AND e.max_units >= 71
            ORDER BY e.max_units DESC NULLS LAST
            LIMIT 500
            """,
        )
        low_confidence_massing = _rows(
            conn,
            """
            SELECT
                p.parcel_id::text,
                p.county_fips,
                p.source_parcel_id,
                e.max_units,
                e.confidence,
                e.massing_flags
            FROM lla.parcels p
            JOIN lla.entitlement e ON e.parcel_id = p.parcel_id
            WHERE e.confidence = 'low'
               OR cardinality(e.massing_flags) > 0
            ORDER BY e.computed_at DESC
            LIMIT 500
            """,
        )
        missing_jurisdiction_params = _rows(
            conn,
            """
            SELECT
                p.parcel_id::text,
                p.county_fips,
                p.source_parcel_id,
                p.jurisdiction_id::text,
                j.name AS jurisdiction_name
            FROM lla.parcels p
            LEFT JOIN lla.jurisdictions j ON j.jurisdiction_id = p.jurisdiction_id
            LEFT JOIN lla.jurisdiction_params jp ON jp.jurisdiction_id = p.jurisdiction_id
            WHERE p.jurisdiction_id IS NULL
               OR jp.jurisdiction_id IS NULL
            ORDER BY p.county_fips, p.source_parcel_id
            LIMIT 500
            """,
        )
        warning_counts = _rows(
            conn,
            """
            WITH scenario_warnings AS (
                SELECT
                    p.county_fips,
                    warning
                FROM lla.parcel_scenarios s
                JOIN lla.parcels p ON p.parcel_id = s.parcel_id
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    coalesce(s.feasibility_output_jsonb->'warnings', '[]'::jsonb)
                ) AS warning
            )
            SELECT
                county_fips,
                count(*) FILTER (WHERE warning LIKE 'rent_%' OR warning LIKE '%rent%') AS rent_warning_count,
                count(*) FILTER (WHERE warning LIKE 'utility_%' OR warning LIKE '%utility%') AS utility_warning_count,
                count(*) FILTER (WHERE warning LIKE 'tax_%' OR warning LIKE '%tax%' OR warning LIKE 'opt_out_%') AS tax_warning_count,
                count(*) AS total_warning_count
            FROM scenario_warnings
            GROUP BY county_fips
            ORDER BY county_fips
            """,
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eligible_large_parcels": eligible_large,
        "low_confidence_massing_parcels": low_confidence_massing,
        "missing_jurisdiction_params": missing_jurisdiction_params,
        "warning_counts_by_county": warning_counts,
        "counts": {
            "eligible_large_parcels": len(eligible_large),
            "low_confidence_massing_parcels": len(low_confidence_massing),
            "missing_jurisdiction_params": len(missing_jurisdiction_params),
            "warning_counties": len(warning_counts),
        },
    }


def write_reports(summary: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"batch_feasibility_qa_{stamp}.json"
    csv_path = output_dir / f"batch_feasibility_qa_counts_{stamp}.csv"

    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str))
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["category", "count"])
        writer.writeheader()
        for category, count in summary["counts"].items():
            writer.writerow({"category": category, "count": count})
    return {"json": str(json_path), "csv": str(csv_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="reports", help="Directory for JSON/CSV reports.")
    args = parser.parse_args()

    summary = collect_summary()
    paths = write_reports(summary, Path(args.output_dir))
    print(json.dumps({"counts": summary["counts"], "reports": paths}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

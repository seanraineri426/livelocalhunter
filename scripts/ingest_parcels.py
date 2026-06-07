#!/usr/bin/env python3
"""Ingest county parcels from public ArcGIS services into lla.parcels.

Usage:
    python scripts/ingest_parcels.py --county miami_dade --limit 500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text

from lla.arcgis import fetch_features, normalize_feature, upsert_parcels
from lla.candidates import candidate_where, classify_candidate
from lla.db import get_engine
from lla.gis_sources import get_source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--county", required=True, help="County key (miami_dade, broward, palm_beach)")
    parser.add_argument("--limit", type=int, default=0, help="Max parcels to ingest; 0 means all matching rows")
    parser.add_argument("--page-size", type=int, default=500, help="ArcGIS page size")
    parser.add_argument("--batch-size", type=int, default=500, help="DB upsert batch size")
    parser.add_argument("--candidates-only", action="store_true",
                        help="Ingest only parcels plausibly eligible for Live Local")
    parser.add_argument("--audit-candidates", action="store_true",
                        help="Classify and count candidate buckets without writing parcels")
    parser.add_argument("--allow-geometry-only", action="store_true",
                        help="Permit sources that lack engine attributes")
    args = parser.parse_args()

    source = get_source(args.county)
    if source.geometry_only and not args.allow_geometry_only:
        raise SystemExit(
            f"'{args.county}' is a geometry-only source and will produce parcels with no "
            f"use_class/lot_sf. {source.notes}\nRe-run with --allow-geometry-only to ingest anyway."
        )

    engine = get_engine()
    mode = "candidate audit" if args.audit_candidates else "candidate ingest" if args.candidates_only else "parcel ingest"
    fetch_limit = args.limit or 10_000_000
    limit_label = "all matching" if args.limit == 0 else str(args.limit)
    print(f"{mode}: up to {limit_label} parcels from {source.name}")

    batch: list[dict] = []
    seen: set[tuple[str, str]] = set()
    total_upserted = 0
    total_seen = 0
    included = 0
    excluded = 0
    bucket_counts: dict[str, int] = {}

    def flush() -> None:
        nonlocal batch, total_upserted
        if batch:
            total_upserted += upsert_parcels(engine, batch)
            batch = []

    fetch_source = source
    if args.candidates_only or args.audit_candidates:
        fetch_source = type(source)(
            **{**source.__dict__, "where": candidate_where(source)}
        )

    for feature in fetch_features(fetch_source, limit=fetch_limit, page_size=args.page_size):
        props = feature.get("properties") or {}
        decision = classify_candidate(source, props) if (args.candidates_only or args.audit_candidates) else None
        if decision is not None:
            bucket_counts[decision.bucket] = bucket_counts.get(decision.bucket, 0) + 1
            if decision.include:
                included += 1
            else:
                excluded += 1
            if not decision.include and args.candidates_only:
                continue
            if args.audit_candidates:
                total_seen += 1
                continue

        row = normalize_feature(source, feature)
        if row is None:
            continue
        if decision is not None:
            row["is_candidate"] = decision.include
            row["candidate_bucket"] = decision.bucket
            row["candidate_reason"] = decision.reason
            row["normalized_use"] = decision.normalized_use
        total_seen += 1
        key = (row["county_fips"], row["source_parcel_id"])
        if key in seen:
            continue
        seen.add(key)
        batch.append(row)
        if len(batch) >= args.batch_size:
            flush()
            print(f"  upserted {total_upserted} (seen {total_seen})")

    flush()
    print(f"Done. Features seen: {total_seen}, unique upserted/updated: {total_upserted}")
    if args.candidates_only or args.audit_candidates:
        print(f"Candidate decisions: included={included}, excluded={excluded}")
        for bucket, n in sorted(bucket_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {bucket:36s} {n}")

    if args.audit_candidates:
        return

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM lla.parcels WHERE county_fips = :fips"),
            {"fips": source.county_fips},
        ).scalar_one()
        sample = conn.execute(
            text(
                """
                SELECT source_parcel_id, use_class, zoning_code,
                       round(lot_sf::numeric, 1) AS lot_sf,
                       round(acreage::numeric, 3) AS acreage,
                       round(ST_Area(geom::geography)::numeric, 1) AS geog_area_m2,
                       ST_GeometryType(geom) AS geom_type,
                       ST_IsValid(geom) AS is_valid
                FROM lla.parcels
                WHERE county_fips = :fips
                ORDER BY updated_at DESC
                LIMIT 3
                """
            ),
            {"fips": source.county_fips},
        ).mappings().all()

    print(f"\nlla.parcels rows for {source.county_fips}: {count}")
    for row in sample:
        print(dict(row))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Verify Supabase Postgres connection and PostGIS."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text

from lla.db import get_engine


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        pg_version = conn.execute(text("SELECT version();")).scalar_one()
        postgis_version = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")
        ).scalar_one()
        table_count = conn.execute(
            text(
                """
                SELECT count(*)
                FROM information_schema.tables
                WHERE table_schema = 'lla'
                  AND table_type = 'BASE TABLE'
                """
            )
        ).scalar_one()
        jurisdiction_count = conn.execute(
            text("SELECT count(*) FROM lla.jurisdictions")
        ).scalar_one()

    print("Connected to Supabase Postgres")
    print(f"Postgres: {pg_version.split(',')[0]}")
    print(f"PostGIS: {postgis_version}")
    print(f"LLA tables: {table_count}")
    print(f"Jurisdictions seeded: {jurisdiction_count}")


if __name__ == "__main__":
    main()

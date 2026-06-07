#!/usr/bin/env python3
"""Apply SQL migrations from db/migrations/ to Supabase."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.db import get_engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"


def main() -> None:
    if len(sys.argv) > 1:
        migrations = [MIGRATIONS_DIR / name for name in sys.argv[1:]]
    else:
        migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    engine = get_engine()
    for migration in migrations:
        sql = migration.read_text()
        with engine.begin() as conn:
            conn.execute(text(sql))
        print(f"Applied {migration.name}")


if __name__ == "__main__":
    main()

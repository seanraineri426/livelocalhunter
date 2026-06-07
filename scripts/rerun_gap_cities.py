#!/usr/bin/env python3
"""Rerun ONLY the three remaining gap cities, by source-specific handler.

  * Pompano Beach, Weston  -> American Legal stealth API walker
  * West Palm Beach        -> enCodePlus (repointed from Municode)

Does not touch any other jurisdiction. Failures are persisted with the
machine-readable error code from lla.errors.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from lla.db import get_engine  # noqa: E402
from lla.errors import ZoningCrawlError  # noqa: E402
from lla.extract import DEFAULT_MODEL, extract_districts_chunked  # noqa: E402
from lla.firecrawl import FirecrawlClient  # noqa: E402
from lla.zoning_crawler import fetch_zoning_text, set_source, upsert_districts  # noqa: E402

WPB_ENCODEPLUS_URL = (
    "https://online.encodeplus.com/regs/westpalmbeach-fl/doc-viewer.aspx?secid=201"
)
TARGETS = ["Pompano Beach", "Weston", "West Palm Beach"]


def _repoint_wpb(engine) -> None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT jurisdiction_id FROM lla.jurisdictions WHERE name='West Palm Beach'")
        ).first()
        if not row:
            print("  West Palm Beach jurisdiction not found"); return
        set_source(
            conn, str(row[0]), "encodeplus", WPB_ENCODEPLUS_URL, "found",
            "SOURCE_MAPPING_ERROR fixed: zoning is enCodePlus Ch.94 ZLDR, not Municode",
        )
    print(f"  West Palm Beach repointed -> encodeplus {WPB_ENCODEPLUS_URL}")


def main() -> None:
    client = FirecrawlClient()
    engine = get_engine()
    print("Repointing West Palm Beach source...", flush=True)
    _repoint_wpb(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT j.jurisdiction_id, j.name, s.provider, s.url
                FROM lla.jurisdiction_sources s
                JOIN lla.jurisdictions j ON j.jurisdiction_id = s.jurisdiction_id
                WHERE j.name = ANY(:names)
                ORDER BY j.name
                """
            ),
            {"names": TARGETS},
        ).all()

    print(f"\nRerunning {len(rows)} gap cities\n", flush=True)
    for r in rows:
        jid = str(r.jurisdiction_id)
        t0 = time.time()
        try:
            body = fetch_zoning_text(client, r.provider, r.url, name=r.name)
            print(f"  {r.name:16s} [{r.provider}] {len(body):>8d} chars in {time.time()-t0:.0f}s", flush=True)
            if len(body) < 2000:
                raise ZoningCrawlError(f"thin body ({len(body)} chars)")
            districts, chunks = extract_districts_chunked(body, jurisdiction=r.name, model=DEFAULT_MODEL)
            with engine.begin() as conn:
                cnt = upsert_districts(conn, jid, districts, r.url, DEFAULT_MODEL)
                set_source(conn, jid, r.provider, r.url, "extracted",
                           f"{r.provider}: {cnt} districts from {len(chunks)} chunks")
            print(f"  {r.name:16s} -> {cnt} districts ({len(chunks)} chunks), {time.time()-t0:.0f}s", flush=True)
        except ZoningCrawlError as e:
            with engine.begin() as conn:
                set_source(conn, jid, r.provider, r.url, "failed", str(e)[:200])
            print(f"  {r.name:16s} -> {e}", flush=True)
        except Exception as e:  # noqa: BLE001
            with engine.begin() as conn:
                set_source(conn, jid, r.provider, r.url, "failed", f"UNCLASSIFIED: {str(e)[:160]}")
            print(f"  {r.name:16s} -> UNCLASSIFIED {type(e).__name__}: {str(e)[:90]}", flush=True)


if __name__ == "__main__":
    main()

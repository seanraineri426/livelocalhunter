#!/usr/bin/env python3
"""One-off backfill for the four American Legal (Cloudflare-gated) cities.

Fetches each zoning code through the Firecrawl stealth proxy + American Legal
JSON API, runs chunked extraction, and stores districts. Run from repo root:
    python scripts/backfill_amlegal.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from lla.db import get_engine  # noqa: E402
from lla.extract import DEFAULT_MODEL, extract_districts_chunked  # noqa: E402
from lla.firecrawl import FirecrawlClient  # noqa: E402
from lla.zoning_crawler import fetch_zoning_text, set_source, upsert_districts  # noqa: E402


def main() -> None:
    client = FirecrawlClient()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT j.jurisdiction_id, j.name, j.county_fips, s.provider, s.url
                FROM lla.jurisdiction_sources s
                JOIN lla.jurisdictions j ON j.jurisdiction_id = s.jurisdiction_id
                WHERE s.provider = 'amlegal'
                ORDER BY j.name
                """
            )
        ).all()
    print(f"American Legal backfill: {len(rows)} cities\n", flush=True)
    for r in rows:
        jid = str(r.jurisdiction_id)
        t0 = time.time()
        try:
            body = fetch_zoning_text(client, r.provider, r.url, name=r.name)
            ft = time.time() - t0
            print(f"  {r.name:16s} fetched {len(body):>7d} chars in {ft:.0f}s", flush=True)
            if len(body) < 2000:
                with engine.begin() as conn:
                    set_source(conn, jid, r.provider, r.url, "not_available",
                               f"FLU fallback: thin amlegal code ({len(body)} chars) ({r.name})")
                print(f"  {r.name:16s} -> FLU fallback (thin)", flush=True)
                continue
            districts, chunks = extract_districts_chunked(body, jurisdiction=r.name, model=DEFAULT_MODEL)
            with engine.begin() as conn:
                cnt = upsert_districts(conn, jid, districts, r.url, DEFAULT_MODEL)
                set_source(conn, jid, r.provider, r.url, "extracted",
                           f"amlegal stealth: {cnt} districts from {len(chunks)} chunks")
            print(f"  {r.name:16s} -> {cnt} districts ({len(chunks)} chunks), {time.time()-t0:.0f}s total", flush=True)
        except Exception as e:  # noqa: BLE001
            with engine.begin() as conn:
                set_source(conn, jid, r.provider, r.url, "failed", str(e)[:200])
            print(f"  {r.name:16s} -> ERR {type(e).__name__}: {str(e)[:90]}", flush=True)


if __name__ == "__main__":
    main()

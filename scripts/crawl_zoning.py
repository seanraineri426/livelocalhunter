#!/usr/bin/env python3
"""Zoning crawler CLI.

Modes:
  --detect [--county FIPS] [--limit N]
      For each municipality, find its code host (Municode/eCode/etc.) and record
      provider + url + status in lla.jurisdiction_sources. This fills the
      no-gaps checklist with *where* each town's rules live.

  --crawl --name "Fort Lauderdale" --county 12011
      Full pipeline for one jurisdiction: detect host -> fetch -> extract ->
      store districts. Works today for scrapable hosts (eCode360); JS hosts
      are marked 'found' with a note that they need the browser fetch path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.db import get_engine
from lla.extract import DEFAULT_MODEL, extract_districts, extract_districts_chunked
from lla.firecrawl import FirecrawlClient, classify_host, detect_code_host
from lla.zoning_crawler import (
    FETCHABLE_PROVIDERS,
    JS_PROVIDERS,
    fetch_zoning_text,
    set_source,
    upsert_districts,
)


def _status_for(provider: str | None) -> tuple[str, str | None]:
    if provider in FETCHABLE_PROVIDERS:
        return "found", None
    if provider in JS_PROVIDERS:
        return "found", f"{provider}: JavaScript host, needs browser/API fetch"
    return "not_available", "no recognized code host found"


# Jurisdictions known to publish zoning outside the online code host, or whose
# online code lacks a parseable district schedule (use county FLU instead).
_FLU_FALLBACK_NAMES = frozenset({
    "Pinecrest",
    "West Palm Beach",
    "West Miami",
    "Cloud Lake",
    "Royal Palm Beach",
})


def _flu_fallback_note(name: str, reason: str) -> str:
    return f"FLU fallback: {reason} ({name})"


def retry_zero_districts(county: str | None, limit: int | None, model: str) -> None:
    """Re-extract jurisdictions marked extracted but with 0 zoning_district rows."""
    client = FirecrawlClient()
    engine = get_engine()
    where = """
        s.crawl_status IN ('extracted', 'failed')
        AND NOT EXISTS (
            SELECT 1 FROM lla.zoning_districts z
            WHERE z.jurisdiction_id = j.jurisdiction_id
        )
    """
    params: dict = {}
    if county:
        where += " AND j.county_fips = :c"
        params["c"] = county
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT j.jurisdiction_id, j.name, j.county_fips, s.provider, s.url
                FROM lla.jurisdiction_sources s
                JOIN lla.jurisdictions j ON j.jurisdiction_id = s.jurisdiction_id
                WHERE {where}
                ORDER BY j.county_fips, j.name
                """
            ),
            params,
        ).all()
    if limit:
        rows = rows[:limit]
    print(f"Retry zero-district (chunked): {len(rows)} jurisdictions\n")
    ok = flu = fail = 0
    for r in rows:
        jid = str(r.jurisdiction_id)
        if r.name in _FLU_FALLBACK_NAMES:
            with engine.begin() as conn:
                set_source(
                    conn,
                    jid,
                    r.provider,
                    r.url,
                    "not_available",
                    _flu_fallback_note(r.name, "zoning published outside Municode host"),
                )
            print(f"  FLU {r.county_fips} {r.name:28s} marked for county FLU fallback")
            flu += 1
            continue
        try:
            body = fetch_zoning_text(client, r.provider, r.url, name=r.name)
            if len(body) < 2000:
                with engine.begin() as conn:
                    set_source(
                        conn,
                        jid,
                        r.provider,
                        r.url,
                        "not_available",
                        _flu_fallback_note(r.name, f"thin online code ({len(body)} chars)"),
                    )
                print(f"  FLU {r.county_fips} {r.name:28s} thin content ({len(body)} chars)")
                flu += 1
                continue
            districts, chunks = extract_districts_chunked(body, jurisdiction=r.name, model=model)
            if not districts:
                raise RuntimeError(f"chunked extraction returned 0 districts ({len(chunks)} chunks)")
            with engine.begin() as conn:
                cnt = upsert_districts(conn, jid, districts, r.url, model)
                note = f"chunked: {cnt} districts from {len(chunks)} chunks"
                set_source(conn, jid, r.provider, r.url, "extracted", note)
            print(f"  OK  {r.county_fips} {r.name:28s} {cnt:>3d} districts ({len(chunks)} chunks)")
            ok += 1
        except Exception as e:  # noqa: BLE001
            with engine.begin() as conn:
                set_source(conn, jid, r.provider, r.url, "failed", str(e)[:200])
            print(f"  ERR {r.county_fips} {r.name:28s} {type(e).__name__}: {str(e)[:70]}")
            fail += 1
    print(f"\nDone. ok={ok} flu_fallback={flu} fail={fail}")


def audit() -> None:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from audit_zoning import main as audit_main

    audit_main()


def detect(county: str | None, limit: int | None) -> None:
    client = FirecrawlClient()
    engine = get_engine()
    where = "j.jurisdiction_type = 'municipality'"
    params: dict = {}
    if county:
        where += " AND j.county_fips = :c"
        params["c"] = county
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT j.jurisdiction_id, j.name, j.county_fips
                FROM lla.jurisdictions j
                JOIN lla.jurisdiction_sources s ON s.jurisdiction_id = j.jurisdiction_id
                WHERE {where} AND s.crawl_status = 'pending'
                ORDER BY j.county_fips, j.name
                """
            ),
            params,
        ).all()
    if limit:
        rows = rows[:limit]
    print(f"Detecting code hosts for {len(rows)} jurisdictions...")
    for r in rows:
        try:
            found = detect_code_host(client, r.name)
            status, note = _status_for(found["provider"])
            with engine.begin() as conn:
                set_source(conn, str(r.jurisdiction_id), found["provider"], found["url"], status, note)
            print(f"  {r.county_fips} {r.name:28s} {found['provider']:10s} {status:13s} {found['url'] or ''}")
        except Exception as e:  # noqa: BLE001
            print(f"  {r.county_fips} {r.name:28s} ERROR {e}")


def crawl(name: str, county: str, model: str) -> None:
    client = FirecrawlClient()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT jurisdiction_id FROM lla.jurisdictions WHERE name=:n AND county_fips=:c"),
            {"n": name, "c": county},
        ).first()
    if not row:
        print(f"No jurisdiction '{name}' in county {county}")
        return
    jid = str(row[0])

    found = detect_code_host(client, name)
    provider, url = found["provider"], found["url"]
    print(f"Host: {provider} -> {url}")

    if provider not in FETCHABLE_PROVIDERS:
        status, note = _status_for(provider)
        with engine.begin() as conn:
            set_source(conn, jid, provider, url, status, note)
        print(f"Marked '{status}'. {note or ''}")
        return

    print("Fetching code text...")
    body = fetch_zoning_text(client, provider, url, name=name)
    print(f"  fetched {len(body)} chars; extracting with {model}...")
    districts = extract_districts(body, jurisdiction=name, model=model)
    print(f"  extracted {len(districts)} districts")
    with engine.begin() as conn:
        n = upsert_districts(conn, jid, districts, url, model)
        set_source(conn, jid, provider, url, "extracted", None)
    print(f"Stored {n} districts for {name}.")
    for d in districts[:12]:
        print(
            f"  {d['district_code']:10s} dens={d.get('max_density_du_ac')} "
            f"ht={d.get('max_height_ft')} far={d.get('max_far')} cat={d.get('category')}"
        )


def crawl_all(
    county: str | None,
    limit: int | None,
    model: str,
    dry_run: bool,
    refetch: bool,
) -> None:
    """Batch the whole fetchable checklist. Resumable: skips rows already
    'extracted' (or 'crawled' in dry-run) unless --refetch. With --dry-run it
    fetches text only and records char counts, leaving extraction for later."""
    client = FirecrawlClient()
    engine = get_engine()
    placeholders = ", ".join(f"'{p}'" for p in sorted(FETCHABLE_PROVIDERS))
    done_states = "('extracted')" if not dry_run else "('crawled', 'extracted')"
    where = f"s.provider IN ({placeholders})"
    params: dict = {}
    if county:
        where += " AND j.county_fips = :c"
        params["c"] = county
    if not refetch:
        where += f" AND s.crawl_status NOT IN {done_states}"
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT j.jurisdiction_id, j.name, j.county_fips, s.provider, s.url
                FROM lla.jurisdiction_sources s
                JOIN lla.jurisdictions j ON j.jurisdiction_id = s.jurisdiction_id
                WHERE {where}
                ORDER BY j.county_fips, j.name
                """
            ),
            params,
        ).all()
    if limit:
        rows = rows[:limit]
    mode = "DRY-RUN (fetch only)" if dry_run else "FULL (fetch + extract)"
    print(f"{mode}: {len(rows)} jurisdictions\n")
    ok = fail = 0
    for r in rows:
        jid = str(r.jurisdiction_id)
        try:
            body = fetch_zoning_text(client, r.provider, r.url, name=r.name)
            n = len(body)
            if n < 1000:
                raise RuntimeError(f"thin content ({n} chars)")
            if dry_run:
                with engine.begin() as conn:
                    set_source(conn, jid, r.provider, r.url, "crawled", f"fetched {n} chars")
                print(f"  OK  {r.county_fips} {r.name:26s} {r.provider:9s} {n:>7d} chars")
            else:
                if len(body) > 120_000:
                    districts, chunks = extract_districts_chunked(
                        body, jurisdiction=r.name, model=model
                    )
                    note = f"{len(districts)} districts (chunked, {len(chunks)} chunks)"
                else:
                    districts = extract_districts(body, jurisdiction=r.name, model=model)
                    note = f"{len(districts)} districts"
                with engine.begin() as conn:
                    cnt = upsert_districts(conn, jid, districts, r.url, model)
                    set_source(conn, jid, r.provider, r.url, "extracted", note)
                print(f"  OK  {r.county_fips} {r.name:26s} {r.provider:9s} {len(districts):>3d} districts")
            ok += 1
        except Exception as e:  # noqa: BLE001
            with engine.begin() as conn:
                set_source(conn, jid, r.provider, r.url, "failed", str(e)[:200])
            print(f"  ERR {r.county_fips} {r.name:26s} {r.provider:9s} {type(e).__name__}: {str(e)[:70]}")
            fail += 1
    print(f"\nDone. ok={ok} fail={fail}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--crawl", action="store_true")
    ap.add_argument("--crawl-all", action="store_true", dest="crawl_all")
    ap.add_argument("--retry-zero-districts", action="store_true", dest="retry_zero")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="with --crawl-all: fetch only, no extract")
    ap.add_argument("--refetch", action="store_true", help="with --crawl-all: redo already-done rows")
    ap.add_argument("--name")
    ap.add_argument("--county")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    if args.detect:
        detect(args.county, args.limit)
    elif args.crawl:
        if not (args.name and args.county):
            ap.error("--crawl requires --name and --county")
        crawl(args.name, args.county, args.model)
    elif args.crawl_all:
        crawl_all(args.county, args.limit, args.model, args.dry_run, args.refetch)
    elif args.retry_zero:
        retry_zero_districts(args.county, args.limit, args.model)
    elif args.audit:
        audit()
    else:
        ap.error("choose --detect, --crawl, --crawl-all, --retry-zero-districts, or --audit")


if __name__ == "__main__":
    main()

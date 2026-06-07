"""Orchestration for the zoning crawler:
  detect host -> fetch code text -> extract districts -> persist.

Persists into:
  - lla.jurisdiction_sources  (where the code lives + crawl status)
  - lla.zoning_districts       (structured per-district rules)
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from lla.extract import DEFAULT_MODEL, extract_districts
from lla.firecrawl import FirecrawlClient, detect_code_host
from lla.gridics import fetch_code_text as gridics_fetch
from lla.gridics import slug_from_url as gridics_slug
from lla.municode import fetch_code_text as municode_fetch
from lla.amlegal import fetch_code_text as amlegal_fetch

# Providers we can fetch plain text from today:
#   - ecode360/encodeplus: Firecrawl basic scrape renders server-side
#   - gridics: public JSON API (no auth)
#   - municode: MunicodeNEXT API via city Referer + x-csrf header (no auth)
#   - amlegal: JSON API via Firecrawl stealth proxy (bypasses Cloudflare)
SCRAPABLE_PROVIDERS = {"ecode360", "encodeplus"}
FETCHABLE_PROVIDERS = SCRAPABLE_PROVIDERS | {"gridics", "municode", "amlegal"}
# Hosts that still need a dedicated path.
JS_PROVIDERS = {"zoninghub"}


def get_jurisdiction(conn: Connection, name: str, county_fips: str) -> str | None:
    row = conn.execute(
        text(
            "SELECT jurisdiction_id FROM lla.jurisdictions "
            "WHERE name = :n AND county_fips = :c"
        ),
        {"n": name, "c": county_fips},
    ).first()
    return str(row[0]) if row else None


def set_source(
    conn: Connection,
    jurisdiction_id: str,
    provider: str | None,
    url: str | None,
    status: str,
    notes: str | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO lla.jurisdiction_sources
                (jurisdiction_id, source_type, provider, url, crawl_status, last_crawled_at, notes, updated_at)
            VALUES (:jid, 'zoning_code', :provider, :url, :status, :ts, :notes, now())
            ON CONFLICT (jurisdiction_id, source_type) DO UPDATE
                SET provider = EXCLUDED.provider,
                    url = EXCLUDED.url,
                    crawl_status = EXCLUDED.crawl_status,
                    last_crawled_at = EXCLUDED.last_crawled_at,
                    notes = EXCLUDED.notes,
                    updated_at = now()
            """
        ),
        {
            "jid": jurisdiction_id,
            "provider": provider,
            "url": url,
            "status": status,
            "ts": datetime.now(timezone.utc) if status in ("crawled", "extracted") else None,
            "notes": notes,
        },
    )


_UPSERT_DISTRICT = text(
    """
    INSERT INTO lla.zoning_districts (
        jurisdiction_id, district_code, district_name, category,
        allows_residential, allows_multifamily, max_density_du_ac,
        max_height_ft, max_height_stories, max_far, min_lot_sf,
        max_lot_coverage, front_setback_ft, side_setback_ft, rear_setback_ft,
        parking_per_unit, code_citation, source_url, confidence,
        extraction_model, extracted_at
    ) VALUES (
        :jurisdiction_id, :district_code, :district_name, :category,
        :allows_residential, :allows_multifamily, :max_density_du_ac,
        :max_height_ft, :max_height_stories, :max_far, :min_lot_sf,
        :max_lot_coverage, :front_setback_ft, :side_setback_ft, :rear_setback_ft,
        :parking_per_unit, :code_citation, :source_url, :confidence,
        :extraction_model, now()
    )
    ON CONFLICT (jurisdiction_id, district_code) DO UPDATE SET
        district_name = EXCLUDED.district_name,
        category = EXCLUDED.category,
        allows_residential = EXCLUDED.allows_residential,
        allows_multifamily = EXCLUDED.allows_multifamily,
        max_density_du_ac = EXCLUDED.max_density_du_ac,
        max_height_ft = EXCLUDED.max_height_ft,
        max_height_stories = EXCLUDED.max_height_stories,
        max_far = EXCLUDED.max_far,
        min_lot_sf = EXCLUDED.min_lot_sf,
        max_lot_coverage = EXCLUDED.max_lot_coverage,
        front_setback_ft = EXCLUDED.front_setback_ft,
        side_setback_ft = EXCLUDED.side_setback_ft,
        rear_setback_ft = EXCLUDED.rear_setback_ft,
        parking_per_unit = EXCLUDED.parking_per_unit,
        code_citation = EXCLUDED.code_citation,
        source_url = EXCLUDED.source_url,
        confidence = EXCLUDED.confidence,
        extraction_model = EXCLUDED.extraction_model,
        extracted_at = now()
    """
)


def upsert_districts(
    conn: Connection,
    jurisdiction_id: str,
    districts: list[dict],
    source_url: str,
    model: str,
) -> int:
    n = 0
    for d in districts:
        params = {k: d.get(k) for k in (
            "district_code", "district_name", "category", "allows_residential",
            "allows_multifamily", "max_density_du_ac", "max_height_ft",
            "max_height_stories", "max_far", "min_lot_sf", "max_lot_coverage",
            "front_setback_ft", "side_setback_ft", "rear_setback_ft",
            "parking_per_unit", "code_citation", "confidence",
        )}
        params.update(
            jurisdiction_id=jurisdiction_id, source_url=source_url, extraction_model=model
        )
        conn.execute(_UPSERT_DISTRICT, params)
        n += 1
    return n


def fetch_zoning_text(
    client: FirecrawlClient, provider: str, url: str, name: str | None = None
) -> str:
    """Return plain-text code content for fetchable providers. Raises for
    auth-gated providers that still need the headless-browser path."""
    if provider == "gridics":
        return gridics_fetch(gridics_slug(url))
    if provider == "municode":
        return municode_fetch(url=url, name=name)
    if provider == "amlegal":
        return amlegal_fetch(client, url)
    if provider in SCRAPABLE_PROVIDERS:
        data = client.scrape(url, formats=["markdown"], only_main_content=False, wait_for=4000)
        return data.get("markdown", "") or ""
    raise NotImplementedError(
        f"provider '{provider}' needs the headless-browser session path"
    )

"""American Legal Publishing (codelibrary.amlegal.com) fetcher.

American Legal is a React SPA behind a Cloudflare managed challenge, so neither
plain HTTP nor a normal headless render reaches the content. However its JSON
API is fully usable when requests are routed through Firecrawl's stealth proxy
(which passes the Cloudflare challenge). Flow:

    sec-info/{client}/latest/{product}/{rootDocId}/  -> code_uuid
    code-toc/{code_uuid}/                            -> top-level sections
    section-toc/{numericId}/                         -> children (recurse)
    render-doc/{client}/latest/{product}/{docId}/    -> per-section html

We walk the section tree, render every node, strip HTML, and concatenate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lla.errors import (
    AntiBotChallengeError,
    NoRelevantZoningChapterError,
    VendorCreditExhaustedError,
)
from lla.firecrawl import FirecrawlClient, FirecrawlError

BASE = "https://codelibrary.amlegal.com/api"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")

# Disk cache for stealth API responses. Each American Legal city is 200-400
# metered stealth calls; caching means we never re-pay for the same response
# during reruns/testing. Override location with LLA_CACHE_DIR.
_CACHE_DIR = Path(os.environ.get("LLA_CACHE_DIR", ".cache")) / "amlegal"

# Specific chapter/title names whose entire subtree is zoning content worth
# rendering in full.
_ZONING_KW = (
    "ZONING",
    "LAND DEVELOPMENT CODE",
    "LAND DEVELOPMENT REGULATION",
    "LAND DEVELOPMENT",
    "UNIFIED LAND",
    "DEVELOPMENT CODE",
    "DEVELOPMENT REGULATION",
    "DISTRICT REGULATION",
    "ZONING DISTRICTS",
    "USE REGULATION",
    " LDR",
    " LDC",
)

# Broad container titles (e.g. "TITLE XV: LAND USAGE") that hold the zoning
# chapter among unrelated siblings; we traverse into them but do not render
# their non-zoning children.
_CONTAINER_KW = (
    "LAND USAGE",
    "LAND USE",
    "PLANNING AND DEVELOPMENT",
    "PLANNING AND ZONING",
    "PLANNING, ZONING",
)

# Branches that are never zoning; never descend or render these even if a
# stray keyword matches. Saves stealth credits on charter/traffic/parks/etc.
_EXCLUDE_KW = (
    "CHARTER",
    "TRAFFIC",
    "MOTOR VEHICLE",
    "PARKS",
    "RECREATION",
    "POLICE",
    "FIRE DEPARTMENT",
    "ELECTION",
    "FINANCE",
    "TAXATION",
    "TAXES",
    "PROCUREMENT",
    "PURCHASING",
    "SOLID WASTE",
    "UTILITIES",
    "PUBLIC WORKS",
    "EMERGENC",
    "GENERAL OFFENSES",
    "BUSINESS REGULATION",
    "TABLE OF SPECIAL ORDINANCES",
    "PARALLEL REFERENCES",
)


def _is_excluded_title(title: str) -> bool:
    up = (title or "").upper()
    return any(k in up for k in _EXCLUDE_KW)


def _is_zoning_title(title: str) -> bool:
    up = (title or "").upper()
    if _is_excluded_title(title):
        return False
    return any(k in up for k in _ZONING_KW)


def _is_container_title(title: str) -> bool:
    up = (title or "").upper()
    if _is_excluded_title(title):
        return False
    return any(k in up for k in _CONTAINER_KW)


class AmLegalError(NoRelevantZoningChapterError):
    """Backwards-compatible alias; American Legal walk failures are almost
    always 'TOC fetched but no usable zoning chapter found'."""


def _cache_path(url: str) -> Path:
    return _CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest() + ".txt")


def _cache_get(url: str) -> str | None:
    p = _cache_path(url)
    if p.exists():
        try:
            return p.read_text()
        except OSError:
            return None
    return None


def _cache_set(url: str, body: str) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(url).write_text(body)
    except OSError:
        pass


def _cached_stealth_fetch(client: FirecrawlClient, url: str) -> tuple[str, int | None]:
    """Stealth fetch with a persistent disk cache. 402 propagates as
    VendorCreditExhaustedError (never cached); other vendor errors return
    ('', None) so callers can retry."""
    cached = _cache_get(url)
    if cached is not None:
        return cached, 200
    try:
        raw, status = client.stealth_fetch(url)
    except VendorCreditExhaustedError:
        raise
    except FirecrawlError:
        return "", None
    if status == 200 and raw:
        _cache_set(url, raw)
    return raw, status


def parse_amlegal_url(url: str) -> tuple[str, str, str]:
    """Return (client_slug, product_slug, root_doc_id) from a codelibrary URL."""
    m = re.search(
        r"amlegal\.com/codes/([^/]+)/[^/]+/([^/]+)/([0-9\-]+)",
        url or "",
    )
    if not m:
        raise AmLegalError(f"cannot parse American Legal URL: {url!r}")
    return m.group(1), m.group(2), m.group(3)


def _get_json(client: FirecrawlClient, url: str) -> dict | list | None:
    raw, status = _cached_stealth_fetch(client, url)  # 402 -> VendorCreditExhaustedError
    if status and status != 200:
        return None
    raw = (raw or "").strip()
    if not raw:
        return None
    # rawHtml sometimes wraps JSON in <html><body><pre>...; strip tags if present
    if raw.startswith("<"):
        raw = _TAG.sub("", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", raw, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None


def _get_json_retry(
    client: FirecrawlClient, url: str, tries: int = 4
) -> dict | list | None:
    """_get_json with retries; use for bootstrap calls whose failure would
    abort the whole crawl (sec-info, code-toc)."""
    for attempt in range(tries):
        result = _get_json(client, url)
        if result:
            return result
        time.sleep(2.0 * (attempt + 1))
    return None


def _code_uuid(client: FirecrawlClient, slug: str, product: str, root_doc: str) -> str:
    info = _get_json_retry(client, f"{BASE}/sec-info/{slug}/latest/{product}/{root_doc}/")
    if not isinstance(info, dict) or not info.get("code_uuid"):
        # Bootstrap call yielded nothing through the stealth proxy -> the
        # Cloudflare challenge was not cleared (not a missing-chapter problem).
        raise AntiBotChallengeError(
            f"sec-info returned no code_uuid for {slug}/{product} "
            "(stealth fetch did not clear the challenge)"
        )
    return info["code_uuid"]


def _strip_html(html: str) -> str:
    s = html.replace("</p>", "\n").replace("</tr>", "\n").replace("</td>", " | ")
    s = _TAG.sub(" ", s)
    s = (
        s.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#160;", " ")
    )
    return _WS.sub(" ", s).strip()


def _collect_doc_ids(
    client: FirecrawlClient,
    sections: list[dict],
    out: list[str],
    seen: set[str],
    max_nodes: int,
    in_zoning: bool,
    descend_all: bool = False,
) -> None:
    """Depth-first walk collecting doc_ids in reading order.

    Three cases per node:
      * already inside a zoning subtree -> render this node and all children;
      * a specific zoning title (ZONING / LAND DEVELOPMENT) -> enter zoning,
        render its whole subtree;
      * a broad container title (LAND USAGE / LAND USE) or, at the very top
        level -> traverse children to locate zoning chapters, but do not
        render the container node itself.
    This keeps full-municipal-code URLs from rendering unrelated chapters.
    `descend_all` relaxes the container gate (fallback when nothing is found)."""
    for sec in sections:
        if len(out) >= max_nodes:
            return
        title = sec.get("title") or ""
        has_kids = bool(sec.get("has_children") or sec.get("has_section_children"))

        if in_zoning or _is_zoning_title(title):
            doc_id = sec.get("doc_id")
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                out.append(doc_id)
            if has_kids:
                kids = _children(client, sec)
                if kids:
                    _collect_doc_ids(client, kids, out, seen, max_nodes, True)
            continue

        # Never spend stealth calls descending obvious non-zoning branches.
        if _is_excluded_title(title):
            continue
        # Not (yet) zoning. Descend into containers (or any branch in fallback)
        # to locate a zoning chapter, without rendering the container node.
        if has_kids and (descend_all or _is_container_title(title)):
            kids = _children(client, sec)
            if kids:
                _collect_doc_ids(client, kids, out, seen, max_nodes, False, descend_all)


def _children(client: FirecrawlClient, sec: dict) -> list[dict]:
    sid = sec.get("id")
    if sid is None:
        return []
    time.sleep(0.1)
    child = _get_json(client, f"{BASE}/section-toc/{sid}/")
    if isinstance(child, dict):
        return child.get("children") or []
    return []


def _render_one(client: FirecrawlClient, slug: str, product: str, doc_id: str) -> tuple[str, str]:
    """Render a single node; returns (doc_id, plain_text)."""
    doc = _get_json(client, f"{BASE}/render-doc/{slug}/latest/{product}/{doc_id}/")
    if not isinstance(doc, dict):
        return doc_id, ""
    html = doc.get("html") or ""
    title = doc.get("title") or ""
    return doc_id, _strip_html(f"{title}. {html}" if title else html)


def fetch_code_text(
    client: FirecrawlClient,
    url: str,
    max_chars: int = 400_000,
    max_nodes: int = 400,
    workers: int = 6,
) -> str:
    """Fetch and concatenate the plain text of an American Legal zoning code.

    The section tree is walked sequentially (few calls), then every node is
    rendered concurrently through the Firecrawl stealth proxy."""
    slug, product, root_doc = parse_amlegal_url(url)
    code_uuid = _code_uuid(client, slug, product, root_doc)
    toc = _get_json_retry(client, f"{BASE}/code-toc/{code_uuid}/")
    if not isinstance(toc, dict):
        raise AntiBotChallengeError(f"code-toc fetch failed for {slug}/{product}")
    sections = toc.get("sections") or []
    if not sections:
        raise NoRelevantZoningChapterError(f"empty TOC for {slug}/{product}")

    doc_ids: list[str] = []
    _collect_doc_ids(client, sections, doc_ids, set(), max_nodes, False)
    if not doc_ids:
        # Fallback: zoning chapter sits under an unrecognised container; walk all.
        _collect_doc_ids(client, sections, doc_ids, set(), max_nodes, False, descend_all=True)
    if not doc_ids:
        raise NoRelevantZoningChapterError(
            f"no zoning/land-development chapter found in TOC for {slug}/{product}"
        )

    # Render all nodes concurrently, preserving reading order.
    texts: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_render_one, client, slug, product, d): d for d in doc_ids
        }
        for fut in futures:
            doc_id, text = fut.result()
            texts[doc_id] = text

    parts: list[str] = []
    total = 0
    for doc_id in doc_ids:
        text = texts.get(doc_id) or ""
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(parts)[:max_chars]

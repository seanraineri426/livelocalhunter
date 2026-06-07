"""Municode fetcher (plain HTTP).

Municode's MunicodeNEXT API is reachable without login as long as requests
carry a city-page Referer and the custom `x-csrf: 1` header that its Angular
app sends. Flow:
    Clients/name -> ClientContent (codes) -> Jobs/latest -> codesToc ->
    CodesContent (returns all descendant Docs of a node).
We locate the zoning/land-development chapter(s) and return concatenated text.
"""

from __future__ import annotations

import re
import time

import requests

BASE = "https://library.municode.com/api"
GENERIC_REFERER = "https://library.municode.com/fl"
ZONING_KEYWORDS = ("ZONING", "LAND DEVELOPMENT", "LAND USE", "LAND USES", "LDR", "LDC")
_DEDICATED_HINTS = ("zoning", "land development", "land develop", "unified land")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class MunicodeError(RuntimeError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_FL_CLIENTS: dict[str, int] | None = None


def get_fl_clients(force: bool = False) -> dict[str, int]:
    """Authoritative {normalized ClientName: ClientID} for all FL Municode clients."""
    global _FL_CLIENTS
    if _FL_CLIENTS is not None and not force:
        return _FL_CLIENTS
    r = requests.get(
        f"{BASE}/Clients/stateAbbr?stateAbbr=fl",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": GENERIC_REFERER,
            "x-csrf": "1",
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise MunicodeError(f"client list {r.status_code}")
    _FL_CLIENTS = {_norm(c.get("ClientName")): c.get("ClientID") for c in r.json() if c.get("ClientID")}
    return _FL_CLIENTS


def resolve_client_id(name: str) -> int | None:
    """Match a jurisdiction name to a Municode ClientID. Strips common civic
    prefixes/suffixes ('City of', 'Town of', 'Unincorporated', 'County')."""
    clients = get_fl_clients()
    raw = name or ""
    for pat in (r"^city of ", r"^town of ", r"^village of ", r"^unincorporated "):
        raw = re.sub(pat, "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\(.*?\)", "", raw)  # drop parentheticals e.g. "(BMSD)"
    cand = _norm(raw)
    if cand in clients:
        return clients[cand]
    no_county = _norm(re.sub(r"\bcounty\b", "", raw, flags=re.IGNORECASE))
    if no_county in clients:
        return clients[no_county]
    for suffix in ("county", "village", "town", "city"):
        if (cand + suffix) in clients:
            return clients[cand + suffix]
    return None


def parse_slug(url: str) -> tuple[str, str | None]:
    """Return (client_slug, code_hint) from a stored Municode URL."""
    m = re.search(r"municode\.com/[a-z]{2}/([a-z0-9_\-]+)", (url or "").lower())
    if not m:
        raise MunicodeError(f"cannot parse Municode slug from {url!r}")
    slug = m.group(1)
    hint = None
    h = re.search(r"/codes/([a-z0-9_]+)", (url or "").lower())
    if h:
        hint = h.group(1)
    return slug, hint


def _headers(slug: str) -> dict[str, str]:
    referer = GENERIC_REFERER if slug in ("", "fl") else f"https://library.municode.com/fl/{slug}"
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
        "x-csrf": "1",
    }


def _get(url: str, slug: str, retries: int = 3) -> dict | list:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=_headers(slug), timeout=60)
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 204 or not r.text:
            return {}  # No Content (e.g. unknown client name) -> soft miss
        if r.status_code in (429, 500, 502, 503, 504):
            last = MunicodeError(f"{r.status_code} for {url}")
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise MunicodeError(f"{r.status_code} for {url}")
        return r.json()
    raise MunicodeError(f"failed after {retries} retries: {last}")


def get_client_id(slug: str) -> int:
    # The Clients/name lookup expects the display name (spaces), while the URL
    # path uses an underscore slug. Try the spaced form first, then the raw slug.
    for client_name in (slug.replace("_", " "), slug):
        d = _get(f"{BASE}/Clients/name?clientName={client_name}&stateAbbr=fl", slug)
        cid = d.get("ClientID") if isinstance(d, dict) else None
        if cid:
            return cid
    raise MunicodeError(f"no ClientID for {slug}")


def get_codes(client_id: int, slug: str) -> list[dict]:
    d = _get(f"{BASE}/ClientContent/{client_id}", slug)
    return d.get("codes") or [] if isinstance(d, dict) else []


def pick_product(codes: list[dict], hint: str | None) -> dict:
    if not codes:
        raise MunicodeError("no codes for client")
    norm = lambda s: re.sub(r"[^a-z]", "", (s or "").lower())
    hint_n = norm(hint.replace("_", " ")) if hint else ""
    if hint_n:
        for c in codes:
            if norm(c.get("productName")) == hint_n:
                return c
        for c in codes:
            if hint_n in norm(c.get("productName")):
                return c
    for c in codes:  # prefer a dedicated zoning / land development code
        pn = (c.get("productName") or "").lower()
        if any(h in pn for h in _DEDICATED_HINTS):
            return c
    for c in codes:
        if "code of ordinances" in (c.get("productName") or "").lower():
            return c
    return codes[0]


def get_job_id(product_id: int, slug: str) -> int:
    d = _get(f"{BASE}/Jobs/latest/{product_id}", slug)
    jid = d.get("Id") if isinstance(d, dict) else None
    if not jid:
        raise MunicodeError(f"no jobId for product {product_id}")
    return jid


def _walk(node: dict, out: list[dict], depth: int = 0) -> None:
    out.append({"id": node.get("Id"), "heading": node.get("Heading") or "", "depth": depth})
    for c in node.get("Children") or []:
        _walk(c, out, depth + 1)


def find_zoning_nodes(toc: dict, product_name: str) -> list[str]:
    """Node ids whose heading marks a zoning / land-development section.

    We always target specific chapters by heading (even inside a dedicated LDC
    product) so the char budget is spent on zoning text, not on unrelated
    chapters (admin, sewers, taxation). Falls back to the root only if nothing
    matches. Matches are ranked so the most zoning-specific chapters come first."""
    root_id = toc.get("Id")
    flat: list[dict] = []
    _walk(toc, flat)
    skip = ("RESERVED", "COMPARATIVE TABLE", "ORDINANCE DISPOSITION", "STATUTORY REFERENCE")

    def rank(h: str) -> int:
        u = h.upper()
        if "ZONING" in u:
            return 0
        if "LAND DEVELOPMENT" in u or "LDR" in u or "LDC" in u:
            return 1
        if "LAND USE" in u or "LAND USES" in u:
            return 2
        if "DISTRICT" in u or "DIMENSIONAL" in u:
            return 3
        return 99

    hits = [
        (rank(n["heading"]), n["id"], n["heading"])
        for n in flat
        if n.get("id")
        and rank(n["heading"]) < 99
        and not any(s in n["heading"].upper() for s in skip)
    ]
    hits.sort(key=lambda x: x[0])
    ids = [h[1] for h in hits]
    return ids or [root_id]


def _content_text(job_id: int, product_id: int, node_id: str, slug: str) -> tuple[str, int]:
    """Return (text, num_docs_with_real_content) for a single node's CodesContent."""
    d = _get(f"{BASE}/CodesContent?jobId={job_id}&nodeId={node_id}&productId={product_id}", slug)
    docs = d.get("Docs") or [] if isinstance(d, dict) else []
    parts = []
    for doc in docs:
        title = doc.get("Title") or ""
        content = doc.get("Content") or ""
        text = _TAG.sub(" ", f"{title} {content}")
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = _WS.sub(" ", text).strip()
        if text:
            parts.append(text)
    return "\n".join(parts), len(parts)


def _children(job_id: int, product_id: int, node_id: str, slug: str) -> list[dict]:
    d = _get(
        f"{BASE}/codesToc/children?jobId={job_id}&nodeId={node_id}&productId={product_id}", slug
    )
    nodes = d if isinstance(d, list) else (d.get("Children") or [] if isinstance(d, dict) else [])
    return [{"id": n.get("Id"), "heading": n.get("Heading") or ""} for n in nodes if n.get("Id")]


# Headings worth fetching first; headings we fetch last (or skip when budget-tight).
_RELEVANT = (
    "ZONING", "DISTRICT", "DIMENSIONAL", "BULK", "SETBACK", "HEIGHT", "DENSITY",
    "LAND USE", "PERMITTED USE", "USE REGULATION", "DEVELOPMENT STANDARD", "OVERLAY",
    "RESIDENTIAL", "COMMERCIAL", "MIXED USE", "INDUSTRIAL", "LIVE LOCAL", "AFFORDABLE",
)
_IRRELEVANT = (
    "ADMINISTRATION", "SUBDIVISION", "ENVIRONMENTAL", "ADULT", "SEXUAL", "PROCEDURE",
    "ENFORCEMENT", "PLATTING", "CONCURRENCY", "IMPACT FEE", "TREE", "LANDSCAP",
    "SIGN", "FLOOD", "HISTORIC PRESERVATION", "RESERVED",
)


def _child_rank(heading: str) -> int:
    u = (heading or "").upper()
    if any(k in u for k in _RELEVANT):
        return 0
    if any(k in u for k in _IRRELEVANT):
        return 2
    return 1


def fetch_node_text(
    job_id: int, product_id: int, node_id: str, slug: str, depth: int = 0, budget: list[int] | None = None
) -> str:
    """Content of a node, drilling into children when a node returns only
    headings (Municode loads full section text at the article/section level)."""
    if budget is None:
        budget = [400_000]
    text, ndocs = _content_text(job_id, product_id, node_id, slug)
    # A node that returns only headings has a low average chars/doc; its real
    # section bodies live one level deeper. Drill when the content looks thin.
    avg = len(text) / ndocs if ndocs else len(text)
    thin = (ndocs == 0) or (ndocs > 1 and avg < 250) or len(text) < 600
    budget[0] -= len(text)
    if depth < 4 and thin and budget[0] > 0:
        kids = _children(job_id, product_id, node_id, slug)
        if kids:
            # Fetch zoning-relevant children first; skip clearly-irrelevant ones
            # (admin, subdivision, signs...) once the budget is running low.
            kids.sort(key=lambda k: _child_rank(k["heading"]))
            parts = [text] if text else []
            for k in kids:
                if budget[0] <= 0:
                    break
                if _child_rank(k["heading"]) == 2 and budget[0] < 150_000:
                    continue
                time.sleep(0.2)
                parts.append(
                    fetch_node_text(job_id, product_id, k["id"], slug, depth + 1, budget)
                )
            return "\n".join(p for p in parts if p)
    return text


def fetch_code_text(
    url: str | None = None,
    name: str | None = None,
    max_chars: int = 400_000,
) -> str:
    """Fetch concatenated zoning text. Resolves the Municode ClientID by
    jurisdiction name (authoritative FL client list); falls back to the URL
    slug. `url` is still used to detect a code-type hint (e.g. land_development_code)."""
    hint = None
    slug = "fl"
    if url:
        try:
            slug, hint = parse_slug(url)
        except MunicodeError:
            pass
    client_id = resolve_client_id(name) if name else None
    if not client_id:
        client_id = get_client_id(slug)  # slug-based fallback
    codes = get_codes(client_id, slug)
    product = pick_product(codes, hint)
    product_id = product.get("productId")
    job_id = get_job_id(product_id, slug)
    toc = _get(f"{BASE}/codesToc?jobId={job_id}&productId={product_id}", slug)
    node_ids = find_zoning_nodes(toc, product.get("productName") or "")
    budget = [max_chars]
    chunks: list[str] = []
    for nid in node_ids:
        if budget[0] <= 0:
            break
        chunks.append(fetch_node_text(job_id, product_id, nid, slug, budget=budget))
        time.sleep(0.3)
    return "\n".join(chunks)[:max_chars]

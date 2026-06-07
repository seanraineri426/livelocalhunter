"""Gridics CodeHub fetcher.

Gridics serves each jurisdiction's full code via a public JSON API (no auth):
    https://codehub.gridics.com/api/v1/codehub/{city_id}?_format=json
The city_id and current revision (vid) are embedded in the city page HTML.
We pull the flat node list, strip HTML, and return concatenated plain text
that the OpenRouter extractor turns into structured district rules.
"""

from __future__ import annotations

import re

import requests

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://codehub.gridics.com/"}


class GridicsError(RuntimeError):
    pass


def slug_from_url(url: str) -> str:
    m = re.search(r"codehub\.gridics\.com/us/[a-z]{2}/([a-z0-9\-]+)", url or "")
    if not m:
        raise GridicsError(f"cannot parse Gridics slug from {url!r}")
    return m.group(1)


def get_city_meta(slug: str) -> dict:
    """Return {city_id, revision, name} from the city page HTML."""
    html = requests.get(
        f"https://codehub.gridics.com/us/fl/{slug}",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    ).text
    m = re.search(r'"id":"(\d+)","vid":"(\d+)"(?:,"hasEditAccess":[^,]+)?,"draft":"[^"]*","name":"([^"]*)"', html)
    if not m:
        m = re.search(r'"id":"(\d+)","vid":"(\d+)"', html)
        if not m:
            raise GridicsError(f"cannot find city_id/revision for {slug!r}")
        return {"city_id": m.group(1), "revision": m.group(2), "name": slug}
    return {"city_id": m.group(1), "revision": m.group(2), "name": m.group(3)}


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
_ZONING_HEADING = (
    "ZONING", "DISTRICT", "LAND DEVELOPMENT", "LAND USE", " ZONE", "ZONES",
    "TRANSECT", "DIMENSIONAL", "SPECIFIC TO ZONE",
)
_IRRELEVANT_HEADING = (
    "SIGN REGUL", "LANDSCAPE", "THOROUGHFARE", "ART IN PUBLIC", "PROCEDURE",
    "NONCONFORM", "DEFINITION", "PREAMBLE", "AMENDMENT", "COMP PLAN",
)


def _strip_html(s: str) -> str:
    s = s.replace("</p>", "\n").replace("</tr>", "\n").replace("</td>", " | ")
    s = _TAG.sub("", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return _WS.sub(" ", s).strip()


def _heading(node: dict) -> str:
    return _TAG.sub("", node.get("text") or "").strip()


def _pos_depth(node: dict) -> int:
    pos = node.get("pos") or ""
    return len(pos.split("-")) if pos else 0


def _is_zoning_article(heading: str) -> bool:
    u = heading.upper()
    if any(k in u for k in _IRRELEVANT_HEADING):
        return False
    return any(k in u for k in _ZONING_HEADING)


def _select_zoning_paths(nodes: list[dict]) -> list[str]:
    """Top-level article paths whose headings mark zoning content."""
    anchors: list[str] = []
    for n in nodes:
        if _pos_depth(n) != 1:
            continue
        h = _heading(n)
        path = n.get("path") or ""
        if h and path and _is_zoning_article(h):
            anchors.append(path)
    return anchors


def fetch_code_text(slug: str, max_chars: int = 400_000) -> str:
    """Return concatenated plain-text of the jurisdiction's zoning chapters."""
    meta = get_city_meta(slug)
    url = f"https://codehub.gridics.com/api/v1/codehub/{meta['city_id']}?_format=json"
    r = requests.get(url, headers=UA, timeout=90)
    if r.status_code != 200:
        raise GridicsError(f"gridics api {r.status_code} for {slug}")
    nodes = r.json()
    if not isinstance(nodes, list):
        return ""

    anchors = _select_zoning_paths(nodes)
    use_filter = bool(anchors)

    def _in_zoning(n: dict) -> bool:
        if not use_filter:
            return True
        path = n.get("path") or ""
        return any(path == a or path.startswith(a + "/") for a in anchors)

    parts: list[str] = []
    for n in nodes:
        if use_filter and not _in_zoning(n):
            continue
        txt = n.get("text") or ""
        if not txt:
            continue
        clean = _strip_html(txt) if "<" in txt else txt.strip()
        if clean:
            parts.append(clean)
        if sum(len(p) for p in parts) > max_chars:
            break
    text = "\n".join(parts)[:max_chars]
    if use_filter and len(text) < 2000:
        # Fallback: anchors missed — take from first zoning keyword hit onward
        start = 0
        for i, n in enumerate(nodes):
            h = _heading(n).upper()
            if any(k in h for k in _ZONING_HEADING):
                start = i
                break
        parts = []
        for n in nodes[start:]:
            txt = n.get("text") or ""
            if not txt:
                continue
            clean = _strip_html(txt) if "<" in txt else txt.strip()
            if clean:
                parts.append(clean)
            if sum(len(p) for p in parts) > max_chars:
                break
        text = "\n".join(parts)[:max_chars]
    return text

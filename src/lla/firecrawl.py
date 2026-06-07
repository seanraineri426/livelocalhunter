"""Thin Firecrawl client: web search (to locate a city's code host) and scrape
(to fetch page content). Firecrawl renders some JS sites; heavily client-side
hosts (Municode, Gridics, American Legal) need the browser/API path instead.
"""

from __future__ import annotations

import time

import requests

from lla.config import require_env
from lla.errors import VendorCreditExhaustedError

BASE = "https://api.firecrawl.dev/v1"


class FirecrawlError(RuntimeError):
    pass


class FirecrawlClient:
    def __init__(self, api_key: str | None = None, timeout: int = 150) -> None:
        self.api_key = api_key or require_env("FIRECRAWL_API_KEY")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def stealth_fetch(self, url: str, wait_for: int = 0, retries: int = 3) -> tuple[str, int | None]:
        """Fetch a URL through Firecrawl's v2 stealth proxy (bypasses Cloudflare
        managed challenges). Returns (rawHtml/body, upstream_status_code).
        Retries transient proxy/site errors. Use for direct API/JSON URLs on
        bot-protected hosts (e.g. American Legal)."""
        body: dict = {"url": url, "formats": ["rawHtml"], "onlyMainContent": False, "proxy": "stealth"}
        if wait_for:
            body["waitFor"] = wait_for
        last = ""
        for attempt in range(retries):
            try:
                r = requests.post(
                    "https://api.firecrawl.dev/v2/scrape",
                    headers=self._headers(),
                    json=body,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last = str(e)
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                data = r.json().get("data") or {}
                raw = data.get("rawHtml") or data.get("html") or data.get("markdown") or ""
                status = (data.get("metadata") or {}).get("statusCode")
                return raw, status
            # Billing failure is an account issue, not a site/code issue.
            if r.status_code == 402:
                raise VendorCreditExhaustedError(f"Firecrawl 402: {r.text[:160]}")
            # Transient Firecrawl-side errors: retry. Upstream 4xx: stop.
            last = f"{r.status_code}: {r.text[:160]}"
            if r.status_code in (408, 429, 500, 502, 503, 504):
                time.sleep(2.0 * (attempt + 1))
                continue
            raise FirecrawlError(f"stealth_fetch {last}")
        raise FirecrawlError(f"stealth_fetch failed after {retries} retries: {last}")

    def search(self, query: str, limit: int = 6) -> list[dict]:
        r = requests.post(
            f"{BASE}/search",
            headers=self._headers(),
            json={"query": query, "limit": limit},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise FirecrawlError(f"search {r.status_code}: {r.text[:200]}")
        return r.json().get("data") or []

    def scrape(
        self,
        url: str,
        formats: list[str] | None = None,
        only_main_content: bool = True,
        wait_for: int = 0,
        **opts,
    ) -> dict:
        body = {
            "url": url,
            "formats": formats or ["markdown"],
            "onlyMainContent": only_main_content,
        }
        if wait_for:
            body["waitFor"] = wait_for
        body.update(opts)
        r = requests.post(f"{BASE}/scrape", headers=self._headers(), json=body, timeout=self.timeout)
        if r.status_code != 200:
            raise FirecrawlError(f"scrape {r.status_code}: {r.text[:200]}")
        return r.json().get("data") or {}


# --- Code-host detection -------------------------------------------------

_HOST_PROVIDERS = [
    ("library.municode.com", "municode"),
    ("municode.com", "municode"),
    ("ecode360.com", "ecode360"),
    ("amlegal.com", "amlegal"),
    ("encodeplus.com", "encodeplus"),
    ("gridics.com", "gridics"),
    ("zoninghub", "zoninghub"),
]


def classify_host(url: str) -> str | None:
    for needle, provider in _HOST_PROVIDERS:
        if needle in (url or ""):
            return provider
    return None


def detect_code_host(client: FirecrawlClient, jurisdiction_name: str) -> dict:
    """Return {provider, url} for a jurisdiction's zoning/land development code."""
    results = client.search(f"{jurisdiction_name} Florida zoning code land development code ordinance")
    for it in results:
        provider = classify_host(it.get("url", ""))
        if provider:
            return {"provider": provider, "url": it["url"]}
    first = results[0]["url"] if results else None
    return {"provider": "other", "url": first}

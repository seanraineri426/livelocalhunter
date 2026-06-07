"""Zoning-crawl error taxonomy.

Different failure modes need totally different fixes, so we classify them
explicitly instead of collapsing everything into a generic "FLU fallback".
Each error carries a stable `code` that is persisted to
lla.jurisdiction_sources.notes so the pipeline state is self-describing.
"""

from __future__ import annotations


class ZoningCrawlError(RuntimeError):
    """Base class. `code` is a stable, machine-readable failure label."""

    code = "ZONING_CRAWL_ERROR"

    def __str__(self) -> str:  # noqa: D105
        base = super().__str__()
        return f"{self.code}: {base}" if base else self.code


class SourceMappingError(ZoningCrawlError):
    """The jurisdiction is pointed at the wrong source/host (zoning lives
    elsewhere). Example: West Palm Beach mapped to Municode when its zoning is
    on enCodePlus."""

    code = "SOURCE_MAPPING_ERROR"


class AntiBotChallengeError(ZoningCrawlError):
    """Host is behind an anti-bot challenge (Cloudflare managed challenge,
    Turnstile, reCAPTCHA) that blocks both page and API routes."""

    code = "ANTI_BOT_CHALLENGE"


class VendorCreditExhaustedError(ZoningCrawlError):
    """A paid fetch vendor refused the request for billing reasons (e.g.
    Firecrawl HTTP 402 Insufficient credits). This is an account issue, not a
    code/site issue, and must not be misread as a downstream symptom."""

    code = "VENDOR_CREDIT_EXHAUSTED"


class SpaNotHydratedError(ZoningCrawlError):
    """A single-page app shell loaded but its content XHR never populated, so
    the rendered HTML has no substantive text."""

    code = "SPA_NOT_HYDRATED"


class NoRelevantZoningChapterError(ZoningCrawlError):
    """The table of contents was fetched successfully but no zoning / land
    development / land use branch was found to extract from."""

    code = "NO_RELEVANT_ZONING_CHAPTER_FOUND"


class ProviderHandlerMissingError(ZoningCrawlError):
    """No dedicated fetch handler is implemented for this provider yet."""

    code = "PROVIDER_HANDLER_MISSING"

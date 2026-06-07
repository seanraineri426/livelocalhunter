"""OpenRouter-backed extraction: turn raw zoning-code text into structured
per-district rules that map 1:1 onto lla.zoning_districts.
"""

from __future__ import annotations

import json
import re

import requests

from lla.config import get_env, require_env

from lla.sectioner import SectionChunk, rank_chunks

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"

# Columns the model is asked to fill (besides jurisdiction + provenance).
DISTRICT_FIELDS = [
    "district_code",
    "district_name",
    "category",
    "allows_residential",
    "allows_multifamily",
    "max_density_du_ac",
    "max_height_ft",
    "max_height_stories",
    "max_far",
    "min_lot_sf",
    "max_lot_coverage",
    "front_setback_ft",
    "side_setback_ft",
    "rear_setback_ft",
    "parking_per_unit",
    "code_citation",
    "confidence",
]

SYSTEM_PROMPT = (
    "You are a land-use analyst extracting zoning district regulations from a "
    "municipal code. Return ONLY valid JSON. Use null for any value not stated "
    "in the text. Never invent numbers. Convert acres-based density to dwelling "
    "units per acre when possible. 'category' must be one of: residential, "
    "commercial, mixed_use, industrial, agricultural, civic, other. "
    "'confidence' is high/medium/low based on how explicit the text is."
)

USER_TEMPLATE = """Jurisdiction: {jurisdiction}

Extract every distinct zoning district described in the text below. For each
district return an object with these keys:
{fields}

Rules:
- district_code: the short code (e.g. "RM-20", "B-1", "RS-3").
- allows_residential / allows_multifamily: true/false/null.
- numeric fields: numbers only (no units), or null.
- code_citation: the section number if visible (e.g. "Sec. 155.04").
- Output JSON of the form: {{"districts": [ {{...}}, ... ]}}

TEXT:
\"\"\"
{text}
\"\"\"
"""


class ExtractionError(RuntimeError):
    pass


def _coerce(d: dict) -> dict:
    out = {k: d.get(k) for k in DISTRICT_FIELDS}
    # normalize booleans/numbers that arrive as strings
    for k in ("allows_residential", "allows_multifamily"):
        v = out.get(k)
        if isinstance(v, str):
            out[k] = {"true": True, "false": False}.get(v.strip().lower())
    for k in DISTRICT_FIELDS:
        if k in ("district_code", "district_name", "category", "code_citation", "confidence"):
            continue
        v = out.get(k)
        if isinstance(v, str):
            m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
            out[k] = float(m.group()) if m else None
    return out


def _parse_response(content: str) -> list[dict]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            raise ExtractionError(f"non-JSON response: {content[:200]}")
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError as e:
            # Truncated JSON: try closing open arrays/objects
            raw = m.group()
            for suffix in ("]}", "}]}", "]}]}"):
                try:
                    data = json.loads(raw + suffix)
                    break
                except json.JSONDecodeError:
                    continue
            else:
                raise ExtractionError(f"JSON parse failed: {e}") from e
    districts = data.get("districts") if isinstance(data, dict) else data
    if not isinstance(districts, list):
        raise ExtractionError(f"expected districts list, got: {str(data)[:200]}")
    return [_coerce(d) for d in districts if d.get("district_code")]


def extract_districts(
    text: str,
    jurisdiction: str,
    model: str | None = None,
    max_chars: int = 60000,
    max_tokens: int = 12000,
) -> list[dict]:
    api_key = require_env("OPENROUTER_API_KEY")
    model = model or get_env("OPENROUTER_MODEL") or DEFAULT_MODEL
    prompt = USER_TEMPLATE.format(
        jurisdiction=jurisdiction,
        fields="\n".join(f"  - {f}" for f in DISTRICT_FIELDS),
        text=text[:max_chars],
    )
    last_err: Exception | None = None
    for attempt, tokens in enumerate((max_tokens, max(max_tokens, 16000))):
        r = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": tokens,
                "response_format": {"type": "json_object"},
            },
            timeout=180,
        )
        if r.status_code != 200:
            raise ExtractionError(f"openrouter {r.status_code}: {r.text[:300]}")
        content = r.json()["choices"][0]["message"]["content"]
        try:
            return _parse_response(content)
        except ExtractionError as e:
            last_err = e
            if attempt == 0:
                continue
            raise
    raise last_err or ExtractionError("extraction failed")


_CONF_RANK = {"high": 3, "medium": 2, "low": 1, None: 0}


def _norm_code(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (code or "").lower())


def _merge_district(existing: dict, incoming: dict) -> dict:
    """Merge two district rows for the same code; prefer higher confidence and non-null values."""
    out = dict(existing)
    inc_conf = _CONF_RANK.get(incoming.get("confidence"), 0)
    ex_conf = _CONF_RANK.get(out.get("confidence"), 0)
    for k in DISTRICT_FIELDS:
        iv, ev = incoming.get(k), out.get(k)
        if iv is None:
            continue
        if ev is None or inc_conf > ex_conf:
            out[k] = iv
        elif inc_conf == ex_conf and k in (
            "max_density_du_ac", "max_height_ft", "max_height_stories", "max_far",
            "min_lot_sf", "max_lot_coverage", "front_setback_ft", "side_setback_ft",
            "rear_setback_ft", "parking_per_unit",
        ):
            # Prefer larger numeric limits when tied (conservative for maxima)
            try:
                if float(iv) > float(ev):
                    out[k] = iv
            except (TypeError, ValueError):
                pass
    if inc_conf > ex_conf:
        out["confidence"] = incoming.get("confidence")
    return out


def merge_districts(rows: list[dict]) -> list[dict]:
    """Deduplicate district rows by normalized district_code."""
    by_code: dict[str, dict] = {}
    for d in rows:
        code = d.get("district_code")
        if not code:
            continue
        key = _norm_code(code)
        if key in by_code:
            by_code[key] = _merge_district(by_code[key], d)
        else:
            by_code[key] = d
    return list(by_code.values())


def extract_districts_chunked(
    text: str,
    jurisdiction: str,
    model: str | None = None,
    *,
    max_chunks: int = 8,
    chunk_chars: int = 35_000,
    stop_after: int = 3,
) -> tuple[list[dict], list[SectionChunk]]:
    """Extract districts from ranked sections of *text*.

    Returns (merged_districts, chunks_used).
    Stops early once *stop_after* chunks yield at least one district each.
    """
    chunks = rank_chunks(text, max_size=chunk_chars, max_chunks=max_chunks)
    if not chunks:
        return [], []

    all_rows: list[dict] = []
    used: list[SectionChunk] = []
    productive = 0

    for chunk in chunks:
        rows = extract_districts(
            chunk.text,
            jurisdiction=jurisdiction,
            model=model,
            max_chars=min(len(chunk.text), chunk_chars),
        )
        used.append(chunk)
        if rows:
            all_rows.extend(rows)
            productive += 1
            if productive >= stop_after and len(merge_districts(all_rows)) >= 5:
                break

    return merge_districts(all_rows), used

"""Deterministic zoning text sectioning.

Long municipal codes are rarely extractable from a single front slice.
This module splits fetched text into ranked chunks that are likely to contain
district schedules, bulk/dimensional standards, and use tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Municipal code heading boundaries (ARTICLE, Chapter, DIVISION, Sec., etc.)
_HEADING = re.compile(
    r"(?m)^(?:"
    r"(?:ARTICLE|Article|CHAPTER|Chapter|DIVISION|Division|PART|Part|TITLE|Title)"
    r"[\s\dIVXLC.\-]+[^\n]{0,100}|"
    r"Sec(?:tion)?\.?\s+[\d\.\-]+[^\n]{0,80}|"
    r"§\s*[\d\.\-]+[^\n]{0,80}"
    r")\s*$"
)

# Labels assigned by dominant keyword signals in a chunk.
_LABELS: list[tuple[str, tuple[str, ...]]] = [
    ("district_schedule", (
        "zoning district", "district schedule", "residential district",
        "commercial district", "industrial district", "mixed use district",
        "district map", "district designation", "district code",
    )),
    ("bulk_standards", (
        "dimensional standard", "bulk standard", "development standard",
        "setback", "height limit", "maximum height", "lot coverage",
        "floor area ratio", "far ", "minimum lot", "dwelling unit",
        "units per acre", "density",
    )),
    ("use_table", (
        "permitted use", "use schedule", "use table", "use classification",
        "allowed use", "principal use", "conditional use",
    )),
    ("overlay", ("overlay", "special district", "planned development", "pud")),
    ("definitions", ("definition", "shall mean", "means:", "for purposes of")),
]

_SCORE: dict[str, int] = {
    "district_schedule": 100,
    "bulk_standards": 80,
    "use_table": 60,
    "overlay": 40,
    "definitions": 10,
}

# Generic words that inflate scores without indicating district tables.
_NOISE = re.compile(
    r"\b(?:parking|sign|landscape|tree|flood|historic|procedure|enforcement|"
    r"subdivision|plat|variance|appeal|fee|tax|traffic|animal)\b",
    re.I,
)


@dataclass(frozen=True)
class SectionChunk:
    label: str
    heading: str
    text: str
    score: int
    start: int


def _classify(text: str) -> tuple[str, int]:
    low = text.lower()
    best_label, best_score = "fallback", 5
    for label, keywords in _LABELS:
        hits = sum(1 for k in keywords if k in low)
        if hits:
            s = _SCORE[label] + hits * 5
            if s > best_score:
                best_label, best_score = label, s
    # Penalize admin-heavy chunks
    noise = len(_NOISE.findall(low))
    best_score = max(0, best_score - noise * 3)
    return best_label, best_score


def _split_sections(text: str) -> list[tuple[str, str, int]]:
    """Return [(heading, body, start_offset), ...]."""
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [("", text, 0)]
    out: list[tuple[str, str, int]] = []
    for i, m in enumerate(matches):
        start = m.start()
        heading = m.group(0).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            out.append((heading, body, start))
    if not out:
        return [("", text, 0)]
    return out


def _merge_to_size(
    sections: list[tuple[str, str, int]],
    target: int = 30_000,
    max_size: int = 40_000,
) -> list[tuple[str, str, int]]:
    """Combine adjacent sections until each chunk is model-friendly."""
    if not sections:
        return []
    merged: list[tuple[str, str, int]] = []
    cur_h, cur_t, cur_s = sections[0]
    for heading, body, start in sections[1:]:
        if len(cur_t) + len(body) <= max_size and len(cur_t) < target:
            cur_t = f"{cur_t}\n\n{body}"
        else:
            merged.append((cur_h, cur_t, cur_s))
            cur_h, cur_t, cur_s = heading, body, start
    merged.append((cur_h, cur_t, cur_s))
    return merged


def rank_chunks(
    text: str,
    *,
    target_size: int = 30_000,
    max_size: int = 40_000,
    min_score: int = 15,
    max_chunks: int = 8,
) -> list[SectionChunk]:
    """Split *text* and return top-ranked chunks for extraction."""
    if not text or len(text.strip()) < 500:
        return []

    sections = _split_sections(text)
    merged = _merge_to_size(sections, target=target_size, max_size=max_size)

    chunks: list[SectionChunk] = []
    for heading, body, start in merged:
        label, score = _classify(body)
        if score >= min_score or label != "fallback":
            chunks.append(SectionChunk(label=label, heading=heading[:120], text=body, score=score, start=start))

    chunks.sort(key=lambda c: (-c.score, c.start))

    # If nothing scored well, fall back to sliding windows over the full text
    if not chunks and len(text) > 60_000:
        step = 35_000
        for i in range(0, len(text), step):
            slice_ = text[i : i + max_size]
            label, score = _classify(slice_)
            chunks.append(
                SectionChunk(label=label or "fallback", heading=f"offset_{i}", text=slice_, score=score, start=i)
            )
    elif not chunks:
        label, score = _classify(text)
        chunks = [SectionChunk(label=label, heading="", text=text, score=score, start=0)]

    return chunks[:max_chunks]

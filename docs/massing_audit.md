# Massing Sanity Audit

The massing sanity audit is a reviewer layer around stored Live Local zoning and massing output. It does not replace the calculator and does not create legal facts.

## Architecture

1. Legal and zoning sources are extracted into structured parcel, jurisdiction, and zoning constraints.
2. The deterministic massing engine solves density, FAR, footprint-height, parking, and related envelope assumptions.
3. `src/lla/massing_audit.py` reviews the stored parcel context and `entitlement.massing_inputs` for reasonableness.
4. Optional OpenRouter AI explains ambiguity and review needs from the context and deterministic flags.
5. Human review resolves zoning ambiguity, site boundary issues, and legal diligence items before anyone relies on the output.

## Buckets

- `deterministic`: rule-based flags from stored context and massing outputs.
- `ai_assisted`: optional AI explanation generated only when requested.
- `human_required`: items that require zoning counsel, staff, analyst, or site-boundary review.

## Status Values

- `ok`: no deterministic reasonableness flags.
- `review`: output may be reasonable, but ambiguity or assumptions need review.
- `likely_bad_input`: the stored output conflicts with core inputs or is missing required massing values.
- `not_applicable`: eligibility failed, so unit-count sanity checks are skipped.

## Deterministic Heuristics

The audit checks for:

- Ineligible parcels where massing should be treated as not applicable.
- Eligible parcels missing `max_units` or producing non-positive units.
- Unit counts above 5,000 and extreme units per acre relative to height or FAR.
- Parcels above 50 acres that likely need a manual development site boundary.
- Missing or low-confidence zoning matches when massing exists.
- Missing `binding_constraint`.
- Density-only counts that are huge but appropriately capped by FAR or footprint-height.
- Very low density on commercial, industrial, or mixed-use Live Local signals, including cases like 2 units on 3 acres unless single-family or agricultural zoning explains it.
- Large divergence between FAR-limited and footprint-height-limited candidate unit counts.
- Surface parking estimates that may not fit on residual open land.
- Existing massing flags such as height rollup use, missing historic height screen, unmatched subject zoning, and manual boundary review.

## AI Reviewer

`ai_massing_audit(context, deterministic_audit)` sends only the parcel context and deterministic audit to OpenRouter. The prompt requires strict JSON and explicitly prohibits recalculating units, inventing zoning, or giving legal conclusions. If OpenRouter is unavailable or returns invalid JSON, the function returns an `unavailable` fallback and leaves deterministic output canonical.

## API

Use:

```bash
GET /parcels/{parcel_id}/massing-audit
GET /parcels/{parcel_id}/massing-audit?use_ai=true
POST /parcels/{parcel_id}/massing-audit
```

The `POST` body is:

```json
{
  "use_ai": true,
  "model": "openai/gpt-4o-mini"
}
```

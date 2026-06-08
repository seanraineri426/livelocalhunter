# Parcel Chat Usage

`scripts/chat_parcel.py` opens an interactive chat over one Live Local parcel context packet. The chat layer is grounded in stored parcel, eligibility, massing, zoning, jurisdiction, exclusion, provenance fields, and the latest saved financial scenario when one exists; it should explain those facts and identify verification items, not invent legal, zoning, financial, or feasibility conclusions.

## Requirements

- `DATABASE_URL` must point to the Supabase Postgres database.
- `OPENROUTER_API_KEY` must be set for chat responses.
- Optional: set `OPENROUTER_MODEL`, or pass `--model` for a one-off override.

## Lookup Examples

Chat by internal parcel UUID:

```bash
python scripts/chat_parcel.py --parcel-id <parcel_uuid>
```

Chat by county folio/source parcel id:

```bash
python scripts/chat_parcel.py --folio <folio> --county miami_dade
```

Print the parcel context JSON without calling the model:

```bash
python scripts/chat_parcel.py --parcel-id <parcel_uuid> --context-only
```

Use a specific OpenRouter model:

```bash
python scripts/chat_parcel.py --parcel-id <parcel_uuid> --model openai/gpt-4o-mini
```

## Good Questions

- `Summarize this parcel for Live Local diligence.`
- `Why is this parcel eligible or ineligible?`
- `What is driving the max unit count?`
- `Which massing flags should counsel review?`
- `What data gaps affect parking or height?`
- `Draft a concise LOI diligence summary from the stored facts.`

## Guardrails

The assistant system prompt requires the model to use the parcel context JSON as the only factual source. If the context is missing a fact, the assistant should say the fact is missing and explain what counsel, zoning staff, or an analyst should verify.

Interactive chat logs are written to `/tmp/lla_parcel_chat.log` for debugging.

Financial scenario assumptions and outputs are produced by deterministic code in
`src/lla/feasibility_calc.py`. The optional cost audit in `src/lla/cost_audit.py`
is advisory JSON and must not be treated as a recalculation or legal/tax opinion.

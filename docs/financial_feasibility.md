# Financial Feasibility Backend

This is the backend-only first pass for Live Local parcel financial feasibility.
It is an internal screening engine, not an appraisal, tax opinion, legal opinion,
or development pro forma. Supabase remains the system of record.

## Sources

Rent limits:

- Florida Housing Finance Corporation rent limits page:
  https://www.floridahousing.org/owners-and-managers/compliance/rent-limits
- Shimberg / Florida Housing Data Clearinghouse income and rent limits page:
  https://flhousingdata.shimberg.ufl.edu/income-and-rent-limits/results?nid=1

The ingestion script uses Firecrawl for discovery/scrape when available and a
direct Shimberg fallback. Loaded rows store source URL, source label, effective
date where known, and raw provenance in `lla.rent_limits`.

Tax exemption legal basis:

- Fla. Stat. 196.1978(3), current Senate statute page:
  https://www.flsenate.gov/Laws/Statutes/2025/0196.1978
- Florida Housing Coalition overview used as secondary interpretation:
  https://flhousing.org/wp-content/uploads/2024/08/FHC-Live-Local-Act-Overview-2024.pdf
- Florida Housing Coalition opt-out summary used as secondary interpretation:
  https://flhousing.org/wp-content/uploads/2024/03/FHC-Summary.-New-Multifamily-Middle-Market-Tax-Exemption-Opt-out-3.8.24.pdf

Millage and opt-out ingestion sources:

- Miami-Dade County Property Appraiser 2025 proposed millage table:
  https://www.miamidadepa.gov/resources-pa/library/reports/millage/2025-proposed-millage-rate-table.pdf
- Broward County Property Appraiser 2025 final millage table:
  https://bcpa.net/Includes/Downloads/2025/2025%20Final%20Millage%20Rate%20Table.pdf
- Palm Beach County Property Appraiser taxing authority code description:
  https://pbcpao.gov/pdf/taxroll/Palm_Beach_County_Tax_Auth_Code_Description.pdf
- Florida Housing Coalition Missing Middle opt-out eligibility summary:
  https://flhousing.org/wp-content/uploads/2024/03/FHC-Summary.-New-Multifamily-Middle-Market-Tax-Exemption-Opt-out-3.8.24.pdf

`scripts/ingest_millage.py` uses Firecrawl for source discovery/scrape first and falls back to
direct official PDF parsing when Firecrawl markdown tables are not line-parseable. Loaded rows
store `millage_source_url`, `opt_out_source_url` when applicable, `effective_date`, `raw`, and
`jurisdiction_name`. `opted_out_middle` remains `null` unless a verified local opt-out
resolution is ingested. For the three pilot counties, FHC lists taxing authorities as not
eligible to opt out based on Shimberg adequate-supply analysis, so `county_has_adequate_supply`
is stored as `true` with the FHC URL as provenance. This is screening context only, not a legal
determination of a recorded opt-out vote.

## Tables

- `lla.rent_limits`: bedroom-specific rent limits by county, year, AMI band,
  and bedroom count.
- `lla.millage`: existing table, extended with source URLs, jurisdiction name,
  effective date, raw JSON, and update timestamp.
- `lla.parcel_scenarios`: parcel-level assumptions, deterministic feasibility
  output, tax exemption estimate, and optional AI cost audit.
- `lla.parcel_notes` and `lla.parcel_review_status`: lightweight internal
  review support.

The legacy `lla.ami_rent_limits` and site-level `lla.feasibility` tables remain
intact.

## Deterministic Calculators

`src/lla/rent_limits.py` looks up stored rent-limit rows. It does not crawl at
runtime.

`src/lla/tax_exemption.py` estimates Missing Middle / Live Local ad valorem
exemption value by authority. It distinguishes units at or below 80% AMI from
units above 80% and up to 120% AMI. The model applies 100% exemption value to
the <=80% tier and 75% exemption value to the 81-120% tier unless an authority
opt-out is stored. It warns when millage, adequate-supply, assessed value, or
unit threshold data is missing.

`src/lla/feasibility_calc.py` computes:

- market and affordable unit split, defaulting to 60% market / 40% affordable;
- gross income, vacancy, effective income, operating expense, and NOI;
- supportable total project cost by required yield-on-cost;
- hard costs by gross SF, per-unit, or total hard-cost basis;
- soft costs, contingency, financing/carry, and TDC excluding land;
- supportable land value, per-unit metrics, feasibility ratio, and result.

The result bands are:

- `pursue`: feasibility ratio >= 1.10;
- `watch`: feasibility ratio >= 0.90 and below 1.10;
- `fail`: feasibility ratio below 0.90, or negative supportable land value
  where enough inputs exist;
- `needs_review`: missing core inputs such as market rent, max units, hard cost,
  or required yield.

## AI Audit

`src/lla/cost_audit.py` calls OpenRouter for a structured JSON audit of the
assumptions and deterministic output. The model is instructed not to recalculate
math, invent rents or costs, or provide legal conclusions. If OpenRouter is
unavailable or returns invalid JSON, the module returns a safe `unavailable`
payload.

## CLI Usage

Load current pilot-county rent limits:

```bash
python scripts/ingest_rent_limits.py
```

Load current pilot-county millage and adequate-supply context:

```bash
python scripts/ingest_millage.py
```

Use `--no-firecrawl` to force direct official PDF fetch. Logs are written to
`/tmp/lla_millage_ingest.log`. The ingestion pass currently loads 2025 official millage rows;
rerun feasibility with `--tax-year 2025` until 2026 certified tables are published.

Run one parcel feasibility screen:

```bash
python scripts/run_feasibility.py \
  --parcel-id <uuid> \
  --assumptions '{"market_monthly_rent":3000,"required_yield_on_cost":0.065,"gross_sf":120000,"hard_cost_per_gross_sf":240,"acquisition_price":5000000,"assessed_value":25000000}' \
  --save
```

Add `--audit` to request the AI cost audit. Future UI work can call the same
parcel-id path after a map click; no frontend was added in this pass.

## Limitations

- Market rent, hard costs, acquisition price, assessed value, and yield are
  user/human assumptions. The engine does not invent them.
- Tax exemption output is a screening estimate. FHFC certification, property
  appraiser review, recorded restrictions, disqualifiers, and counsel review
  control real eligibility.
- Authority-level opt-out treatment depends on loaded `lla.millage` rows. If
  opt-out or adequate-supply data is missing, the calculator emits warnings.
- `opted_out_middle` is nullable. Unknown opt-out status is conservative: the
  81-120% AMI tier is not applied until a verified non-opt-out is stored.
- Hospital-district splits, DDA overlays, and special-district variants in county
  TRIM tables may not map 1:1 to every parcel. Unmatched source rows are logged
  during ingestion and remain human-review items.
- The current stored rent rows cover pilot counties for 2026, AMI bands 80 and
  120, bedrooms 0-4.

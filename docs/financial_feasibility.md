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

Utility allowances:

- Miami-Dade Housing Choice Voucher / PHCD program information:
  https://mdvoucher.com/en-us/Pages/View/2/program-information
- Miami-Dade 2025 centralized utility allowance schedule:
  https://mdvoucher.com/Media/Shared/Documents/2025%20Utility%20Allowance%20Schedules.pdf
- Broward County Housing Authority participant resources:
  https://bchafl.org/ova_sev/participants/
- Broward County Housing Authority 2026 utility allowance schedule:
  https://bchafl.org/wp-content/uploads/2024/06/Utility-Allowance-Schedule-2026.pdf
- West Palm Beach Housing Authority utility allowances:
  https://www.wpbha.org/utility-allowances
- West Palm Beach Housing Authority 2026 utility allowance schedule:
  https://www.wpbha.org/utility/openPDF/wpbhfl/Utility_Allowance_Schedule__01.01.2026.pdf?alt=media
- HUD Multifamily Utility Allowance Factors, used only as source context and
  not as parcel-level allowance rows:
  https://www.huduser.gov/portal/datasets/muaf.html

`scripts/ingest_utility_allowances.py` uses Firecrawl discovery/scrape against
official PHA PDFs and stores a conservative, explicit
`all_tenant_paid_electric_apartment_baseline` profile. These rows are PHA or
county schedule rows, not parcel-specific utility splits; feasibility emits
warnings for that scope.

HUD cross-check sources:

- HUD HOME rent limits:
  https://www.huduser.gov/portal/datasets/HOME-Rent-limits.html
- HUD MTSP income limits:
  https://www.huduser.gov/portal/datasets/mtsp.html
- HUD FY 2026 Fair Market Rent schedule:
  https://www.huduser.gov/portal/datasets/fmr/fmr2026/FY2026_FMR_Schedule.pdf

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
`jurisdiction_name`.

`scripts/ingest_tax_opt_outs.py` is the separate verified opt-out ingestion path. It only updates
`lla.millage.opted_out_middle` when a record has an explicit true/false status, jurisdiction and
authority scope, source URL, evidence summary, and local action/date provenance. Secondary
summaries such as Florida Housing Coalition materials may be used for discovery, but they are not
enough by themselves to write a local opt-out or non-opt-out decision. Absence of a discovered
resolution is also not enough to set `opted_out_middle=false`.

As of the June 2026 follow-up, targeted Firecrawl/web discovery did not find official Doral,
Tamarac, Palm Beach County, or county-level pilot opt-out resolutions for the Missing Middle
80-120% AMI ad valorem exemption. No `opted_out_middle` rows were updated; unknowns remain `null`.
Existing `county_has_adequate_supply` values are retained as secondary screening context only and
must not be treated as a verified local opt-out vote.

## Tables

- `lla.rent_limits`: bedroom-specific rent limits by county, year, AMI band,
  and bedroom count. These are treated as gross rent limits.
- `lla.utility_allowances`: source-backed county/PHA utility allowance schedule
  rows by year, bedroom count, unit type, utility profile, source URL, raw
  provenance, and confidence.
- `lla.market_rent_sources`: parcel or area market-rent assumptions with
  source type, report metadata, submarket, bedroom count, rent, vacancy,
  concessions, confidence, notes, and optional source-file reference.
- `lla.scenario_templates`: seeded template metadata and assumption JSON for
  conservative, base-case, aggressive, internal-cost-advantage,
  tax-exemption, and no-tax-benefit cases. Runtime code keeps the same defaults
  in `src/lla/feasibility_defaults.py` so API smoke tests and local tools do
  not require a live DB just to list templates.
- `lla.millage`: existing table, extended with source URLs, jurisdiction name,
  effective date, raw JSON, and update timestamp.
- `lla.parcel_scenarios`: parcel-level assumptions, deterministic feasibility
  output, tax exemption estimate, and optional AI cost audit.
- `lla.parcel_notes` and `lla.parcel_review_status`: lightweight internal
  review support.

The legacy `lla.ami_rent_limits` and site-level `lla.feasibility` tables remain
intact.

## Deterministic Calculators

`src/lla/rent_limits.py` looks up stored rent-limit and utility allowance rows.
It does not crawl at runtime.

`src/lla/tax_exemption.py` estimates Missing Middle / Live Local ad valorem
exemption value by authority. It distinguishes units at or below 80% AMI from
units above 80% and up to 120% AMI. The model applies 100% exemption value to
the <=80% tier and 75% exemption value to the 81-120% tier unless an authority
opt-out is stored. It warns when millage, adequate-supply, assessed value, or
unit threshold data is missing.

`src/lla/feasibility_calc.py` computes:

- market and affordable unit split, defaulting to 60% market / 40% affordable;
- affordable tenant-paid rent as gross rent limit minus stored utility allowance,
  unless `utilities_included=true` is explicitly supplied;
- gross income, vacancy, effective income, operating expense, and NOI;
- supportable total project cost by required yield-on-cost;
- hard costs by gross SF, per-unit, or total hard-cost basis;
- soft costs, contingency, financing/carry, and TDC excluding land;
- supportable land value, per-unit metrics, feasibility ratio, and result.

`src/lla/feasibility_service.py` is the shared orchestration layer used by the
API. It applies a scenario template, merges explicit assumptions, builds parcel
context, looks up affordable rent limits and utility allowances, attaches the
latest market-rent provenance when available, runs the tax exemption estimate,
and finally calls the pure calculator.

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

Load pilot-county utility allowances:

```bash
python scripts/ingest_utility_allowances.py
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

71+ unit tax demonstration scenario:

```bash
python scripts/run_feasibility.py \
  --parcel-id <uuid> \
  --assumptions /tmp/lla_71plus_assumptions.json \
  --scenario-name pilot_tax_exemption_71plus_2026 \
  --tax-year 2025 \
  --rent-year 2026 \
  --save
```

The June 2026 sample demonstration used 120 total units, 72 units at or below 80% AMI,
`affordable_share=0.6`, and `market_share=0.4`. This meets the more-than-70 affordable-unit
threshold and lets the <=80% AMI 100% exemption tier produce positive screening tax savings
without assuming a verified non-opt-out for the 81-120% AMI tier. If a user override exceeds the
stored massing `max_units`, the calculator emits `total_units_exceeds_massing_max_units`.

## Limitations

- Market rent, hard costs, acquisition price, assessed value, and yield are
  user/human assumptions. The engine does not invent them.
- Affordable rent limits are gross rent limits. If tenants pay utilities, the
  calculator subtracts a stored utility allowance before using affordable rent
  in gross income. If no utility allowance exists and `utilities_included=true`
  was not explicit, the calculator emits `utility_allowance_missing` and does
  not silently treat the allowance as zero.
- Current utility allowance rows are PHA/county schedule baselines for common
  apartment/multifamily all-tenant-paid electric profiles. Real underwriting
  still needs the project utility split, owner-paid services, applicable PHA,
  and housing-program compliance review.
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

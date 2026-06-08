# Live Local Site Intelligence — Build Spec

A working brief to build from. Keep this file in the repo root and reference it when
prompting the AI agent ("per spec.md, build the …"). Build in the order below; do not
scaffold the whole thing at once.

---

## 1. What you're building

A Florida Live Local parcel-intelligence engine. It identifies parcels one by one,
computes deterministic eligibility and Live Local massing from stored data, and makes
those facts explainable through a chat layer.

The edge is the **parcel context layer**: auditable eligibility, massing inputs,
flags, data gaps, and provenance that can be discussed conversationally. The AI
assistant explains stored facts and diligence questions; it does not invent
eligibility, massing, legal conclusions, ranking scores, or feasibility numbers.

---

## 2. Tech stack

- **Canonical store:** Supabase Postgres with the PostGIS extension enabled.
  Connect from Replit using the **Session pooler** connection string (IPv4),
  stored in Replit Secrets as `DATABASE_URL`. Not the direct string.
- **Build + host:** Replit (agent + hosting). Use **Scheduled Deployments** for
  the recurring data-ingestion jobs.
- **Language:** Python for ingestion, the engine, and the statute rules;
  PostGIS/SQL for anything spatial (within-a-mile, adjacency, within-a-quarter-mile).
- **Map:** MapLibre GL front end. v1 reads GeoJSON from the Supabase auto-API;
  move to vector tiles (pg_tileserv / Martin) only when volume demands it.
- **CRM v1:** a Supabase table (or Airtable) keyed by `site_id`. Build custom later.

---

## 3. The core design idea — three separated layers

Keep these three apart and the system stays maintainable. Tangle them and every new
town becomes a fork.

1. **Statute rules** — the same across all of Florida, versioned by year
   (SB 102 2023, SB 328 2024, SB 1730 2025, HB 1389 2026). Lives in **code**,
   effective-dated, so any parcel can be re-run against any version.
2. **Jurisdiction parameters** — the local numbers the statute points to:
   max density, max FAR, the zoning-code→use crosswalk, millage by taxing authority.
   Lives in **data tables**.
3. **Local requirements** — per-town overlays and "poison pills" (minimum unit size,
   minimum nonresidential %, setbacks), each tagged `preempted` / `surviving` /
   `contested` with an effective date. Lives in **data tables**.

Adding a county = new data rows. The law changing = a new rules version. Never re-fork
the engine.

---

## 4. Data model (core tables)

**sites** — your CRM/deal unit; an assemblage of one or more parcels
`site_id (uuid pk) · stage · owner_contact · notes · created_at`

**parcels** — versioned source records
`parcel_id (uuid) · county_fips · source_parcel_id (raw + normalized) · geom (PostGIS) ·
acreage · lot_sf · zoning_code · use_class · jurisdiction_id · valid_from · valid_to ·
source · as_of_date`

**parcel_lineage** — handles splits/merges/replats
`predecessor_parcel_id · successor_parcel_id`

**site_parcels** — many-to-many link
`site_id · parcel_id`

**jurisdiction_params**
`jurisdiction_id · max_density_du_ac · max_far · far_2023_snapshot ·
zoning_crosswalk_ref · base_parking_per_unit`

**millage** — broken out by authority (required for the exemption opt-out math)
`jurisdiction_id · authority_name (county/municipal/school/special) · millage ·
opted_out_middle (bool) · county_has_adequate_supply (bool)`

**local_requirements** — the poison-pill layer
`jurisdiction_id · req_type · value · code_citation · effective_date ·
preemption_status (preempted/surviving/contested) · preempted_by_version · notes`

**excluded_areas** — `geom · type (airport / wekiva / everglades / waterfront)`

**transit_stops** — `geom` (from GTFS)

**ami_rent_limits** — `county · year · ami_band (80 / 120) · max_rent` (from FHFC)

**entitlement** — engine output, one row per site/parcel
`site_id · eligible (bool) · failed_reasons[] · max_units · max_height_stories ·
buildable_sf · required_parking · statute_version · params_version · confidence ·
computed_at`

**feasibility** — `site_id · noi · stabilized_value · total_cost · spread_bps · computed_at`

**permits** — `permit_id · parcel_id · type · status · dates · likely_lla (bool) · source`

---

## 5. Build order

- **v0 — eligibility only.** Ingest parcels for ONE pilot county (Miami-Dade is
  data-rich). Apply only the eligibility gates. Output: a list of eligible parcels.
  Validate against 10 parcels you've checked by hand before trusting it.
- **v1 — simple massing + map.** Add the basic envelope (density × acres,
  FAR × lot_sf, height = max(within-mile, 3 stories)). Write to `entitlement`.
  Render parcels on a MapLibre map, colored by eligibility.
- **v1.5 — parcel intelligence + chat.** Build a parcel context packet from stored
  parcel, entitlement, jurisdiction, zoning, excluded-area, candidate, enrichment,
  flags, inputs, data-gap, and provenance fields. Put an AI chat layer on top that
  answers questions like "Why is this eligible?", "What's driving unit count?",
  "What would counsel verify?", and "Summarize for LOI" using only that context.
- **v2 — nuance.** Add the `local_requirements` haircuts (e.g., minimum unit size
  reducing unit count), the single-family adjacency 10-story cap, the quarter-mile
  parking reduction, and conservative/aggressive parameter pairs.
- **v3 — pipeline + feasibility.** Add FHFC rent limits, tax-exemption math,
  feasibility screens, permit/meeting tracking, owner-motivation overlays, CRM, and
  map/list ranking only after parcel-level facts are reliable.

---

## 6. Engine logic (reference)

**Eligibility gates** (each returns pass/fail + reason):
- use_class ∈ {commercial, industrial, mixed-use, qualifying PUD, faith-owned/YIGBY}
- parcel does NOT intersect any `excluded_areas` polygon
- (project commitment assumed: ≥40% units ≤120% AMI for 30 yrs; YIGBY 10%)

**Massing envelope** (if eligible):
- max_units = jurisdiction max_density_du_ac × net acres
- buildable_sf = max_far × lot_sf  (current reading: FAR benefit = 150% of local max;
  FAR now includes lot coverage; benchmark to least-restrictive of current or 7/1/2023)
- max_height = max(tallest within 1 mile, 3 stories); cap at 10 stories if the parcel
  abuts single-family residential on ≥2 sides with ≥25 contiguous homes
- required_parking = base_parking × 0.85 if within 0.25 mi of a transit stop
- mixed-use: nonresidential ≤ 10% of total SF

**Tax exemption** (see `missing_middle_exemption.py`):
- needs ≥71 units serving ≤120% AMI
- 100% exemption for units ≤80% AMI (never opt-out-able)
- 75% exemption for units 81–120% AMI (a taxing authority can opt out of ITS share
  only where the county is Shimberg-"adequate")
- computed per taxing authority, not on a blended millage
- disqualifiers: Ch. 420 LURA or existing s.196.1979 local exemption

**Feasibility**:
- affordable_rent = min(FHFC limit at band, 0.90 × market rent)
- gross rent → less vacancy & operating expenses (exemption cuts the tax line) → NOI
- stabilized_value = NOI ÷ exit_cap_rate
- spread_bps = (NOI ÷ total_development_cost − exit_cap_rate) × 10,000
- screen, not an appraisal — sorts which parcels deserve a real model

---

## 7. Where each table is fed

| Table | Source |
|---|---|
| parcels, ownership | County Property Appraiser ArcGIS FeatureServer, or Regrid license |
| zoning / FLU | Jurisdiction GIS layer + comprehensive plan |
| excluded_areas | State GIS (airport-impact, Wekiva, Everglades, waterfront) |
| transit_stops | Transit agency GTFS feeds |
| ami_rent_limits | FHFC posted limits / Shimberg Center clearinghouse |
| millage + opt-out | County property appraiser / TRIM tables; council vote records |
| local_requirements | Municode / eLaws code text → AI extraction (see §8) |
| permits / decisions | Shovels or GatherGov API, or city portal scrape |

---

## 8. Researching the per-town rules with AI (the moat-scaler)

Never ask a model about a town's code from memory — it will invent it. Make AI an
**extraction engine over primary sources**:

1. Fetch the actual text — the town's land development code on Municode/eLaws, its
   Live Local ordinance, the comp-plan density/FLU tables, council/planning agendas.
2. The model extracts structured fields into `jurisdiction_params` and
   `local_requirements`, each with the exact code citation and a confidence flag.
3. The model proposes `preempted` / `surviving` / `contested` against the current
   statute version; you (or an attorney) confirm the contested calls — that's the
   judgment that is your edge, so keep it human.
4. Monitor for change (meeting feed + Municode change-logs) and re-extract when a town
   amends its code.

---

## 9. How to prompt the agent

**Principles**
- One component per prompt, with a clear done-state. Never "build the whole platform."
- Front-load context: paste the relevant schema and the exact rule/formula into the
  prompt so the agent doesn't invent them.
- Demand testable increments: "write X, then run it on this sample input and print the
  result." Verify before moving on.
- Build data layer → logic → UI, in that order. One parcel queryable in PostGIS before
  the engine; the engine producing a table before the map.
- Keep secrets out of prompts — reference `DATABASE_URL` by name, set it in Secrets.
- When it drifts, revert to a Replit checkpoint rather than patching a patch.

**Example prompt sequence**

> Set up a Python project. Connect to my Supabase Postgres using SQLAlchemy with the
> connection string in Secrets as `DATABASE_URL`. Verify the PostGIS extension is
> enabled and print its version. Then create the `parcels` and `entitlement` tables
> per the schema below: [paste §4 for those two tables].

> Write a GeoPandas script that reads the parcel GeoJSON from this Miami-Dade ArcGIS
> FeatureServer URL [url], maps these source columns to my `parcels` columns [list],
> and upserts into PostGIS keyed on (county_fips, source_parcel_id). Print the row
> count and one sample row.

> Write a function `eligibility(parcel)` returning `{eligible, reasons}` that applies
> these gates: [paste §6 eligibility gates]. The excluded-area check is a PostGIS
> ST_Intersects against the `excluded_areas` table. Run it on 10 parcels and print a
> results table.

> Write `massing(parcel)` returning max_units, buildable_sf, max_height, and parking
> per these formulas: [paste §6 massing]. Implement "tallest within 1 mile" as a
> PostGIS ST_DWithin query. Write to the `entitlement` table and print 5 results.

Add the next component only once the previous one runs and you've eyeballed the output.

# Live Local 4.0 Massing Basis

This engine applies the Florida Live Local Act massing preemptions as amended by
Ch. 2025-172 / CS/CS/SB 1730, effective July 1, 2025. It is a screening model,
not a legal opinion or site plan approval package.

## Primary Sources

- Fla. Stat. 125.01055(7), counties:
  https://www.leg.state.fl.us/Statutes/index.cfm?App_mode=Display_Statute&URL=0100-0199/0125/Sections/0125.01055.html
- Fla. Stat. 166.04151(7), municipalities:
  https://www.flsenate.gov/laws/statutes/2025/166.04151
- Enrolled CS/CS/SB 1730 (2025), Ch. 2025-172:
  https://www.flsenate.gov/Session/Bill/2025/1730/BillText/er/HTML
- Florida Senate 2025 bill summary for CS/CS/SB 1730:
  https://www.flsenate.gov/Committees/BillSummaries/2025/html/1730

Secondary interpretation checked for implementation context:

- Holland & Knight, "2025 Updates to Florida's Live Local Act":
  https://www.hklaw.com/en/insights/publications/2025/07/2025-updates-to-floridas-live-local-act
- Nelson Mullins, "SB 1730's Bold Overhaul of Land Use for Affordable Housing in Florida":
  https://www.nelsonmullins.com/insights/alerts/nelson-mullins-affordable-housing-news/all/sb-1730-s-bold-overhaul-of-land-use-for-affordable-housing-in-florida

## Rules Implemented

- Eligibility premise: massing is only written for rows already marked eligible by
  the eligibility engine under the commercial, industrial, mixed-use, qualifying
  flexible-zoning/PUD, and exclusion-area screens.
- Parcel zoning premise: the eligibility/context path now favors parcel-specific
  zoning/FLU signals when they are present. Current use and candidate buckets can
  explain why a parcel was screened in, but if the subject zoning cannot be
  matched or clearly categorized the massing output is flagged for review rather
  than treated as a zoning-grounded entitlement.
- Density: use the highest density in the local government where residential
  development is allowed, excluding unavailable bonus/variance/special-exception
  values to the extent the extracted zoning rows distinguish them. The 2025
  "currently allowed or July 1, 2023, whichever is least restrictive" benchmark is
  represented by the current extracted maximum because the database does not yet
  store a dated July 1, 2023 density snapshot.
- FAR/intensity: use 150 percent of the highest FAR where development is allowed.
  The statute now says FAR includes floor lot ratio and lot coverage; this model
  stores the statutory 150 percent FAR in `jurisdiction_params.max_far`.

## Building-envelope reconciliation (max_units)

`max_units` is no longer a raw `density_du_ac * acreage` product. That single
formula produced physically impossible counts on large or low-density tracts
(e.g. a 453-acre Doral parcel at 75 du/ac returned 34,027 units at 3.5 stories).
`max_units` is now the **binding (minimum)** of three independently computed
constraints, and the binding constraint is recorded in
`massing_inputs.binding_constraint`:

1. **Density-limited** = `floor(max_density_du_ac * acreage)`. Acreage is gross
   (no right-of-way/net-lot deduction is available in the data yet).
2. **FAR-limited** = `floor(statutory_FAR * lot_sf * UNIT_GROSS_EFFICIENCY / AVG_UNIT_NET_SF)`.
   The statutory FAR is `1.5 * local FAR`. The buildable gross floor area is
   converted to dwelling units using two documented screening constants:
   `AVG_UNIT_NET_SF` (default 900 sf net per unit) and `UNIT_GROSS_EFFICIENCY`
   (default 0.82 net rentable / gross, i.e. corridors, cores, walls, amenity and
   mechanical consume the remaining 18 percent).
3. **Footprint x height-limited** = `floor(footprint_sf * max_height_stories * UNIT_GROSS_EFFICIENCY / AVG_UNIT_NET_SF)`.
   The per-floor footprint is `min(lot_coverage_fraction * lot_sf, setback_footprint_sf)`.
   - `lot_coverage_fraction` comes from the matched zoning district's
     `max_lot_coverage` (stored inconsistently as a percent like `40` or a
     fraction like `0.8`; both are normalized to a 0-1 fraction). When no zoning
     district is matched, a conservative `DEFAULT_LOT_COVERAGE` of 0.40 is used and
     the row is flagged `lot_coverage_defaulted` / `envelope_uses_default_lot_coverage`.
   - `setback_footprint_sf` approximates the buildable footprint after front/side/
     rear setbacks. Because only lot area (not lot dimensions) is stored, the parcel
     is modeled as a square (`side = sqrt(lot_sf)`); front+rear are subtracted from
     the depth and twice the side setback from the width. Missing setbacks flag
     `setbacks_defaulted` and skip this refinement.
   - `max_height_stories` is the existing Live Local height (highest allowed within
     ~1 mile or 3 stories, whichever is greater).

`buildable_sf` is now capped to the lesser of the FAR cap and the
footprint x height envelope, so it can no longer exceed what the height limit
physically permits.

`massing_inputs` records every intermediate value: `density_limited_units`,
`far_limited_units`, `envelope_limited_units`, `far_buildable_sf`,
`envelope_buildable_sf`, `footprint_sf`, `lot_coverage_fraction`, the subject
zoning setbacks/coverage/min-lot used, and the chosen `binding_constraint`.

Parking land consumption is approximated: surface stalls
(`required_parking * SURFACE_PARKING_SF_PER_STALL`, default 350 sf/stall) are
compared against open lot area; if they do not fit, the row is flagged
`surface_parking_may_not_fit_structured_parking_likely`. Full structured-parking
modeling is out of scope.

Parcels larger than `OVERSIZED_PARCEL_ACRES` (default 50 acres) are almost always
aggregate tracts (whole sections, golf courses, government land) rather than a
single development site; they are flagged `oversized_parcel_review_required` and
`manual_site_boundary_required`, and their confidence is degraded to `low`. The
unit count remains an arithmetic screen, but the UI/context presents it as
review-required until a developable site boundary or subdivision parcel is used.

When zoning is unmatched or envelope inputs are defaulted, confidence is degraded
(to `medium` for defaulted envelope inputs, to `low` for oversized parcels or
missing jurisdiction params).
- Height: the statutory rule is the highest currently allowed or July 1, 2023
  height for a commercial or residential building in the jurisdiction within one
  mile of the parcel, or three stories, whichever is higher. The current database
  has extracted zoning rule rows but no zoning district geometry, so the engine
  uses a jurisdiction-level commercial/residential height rollup and flags
  `height_within_1mi_uses_jurisdiction_rollup`.
- Single-family adjacency: if parcel geometry suggests at least two adjacent
  single-family-like parcels, the model flags `single_family_adjacency_possible`
  and caps the screen at 10 stories when the jurisdiction rollup exceeds that
  cap. The database does not contain the 25-contiguous-home test or adjacent
  building heights, so this is conservative and review-required.
- Historic height exception: National Register historic-district/building data is
  not in the schema. Rows are flagged `historic_height_screen_missing`; the
  three-fourths-mile historic comparator is not applied.
- Parking: the engine applies the mandatory 15 percent reduction for parcels
  within one-quarter mile of a loaded transit stop. It flags missing transit, TOD,
  major-hub, and available-parking inputs. The live database currently has no
  transit stops loaded, so no parking reduction can be proven.

## Audit Output

`lla.entitlement.massing_flags` records missing or approximated legal inputs.
`lla.entitlement.massing_inputs` records the numeric inputs used for the massing
calculation. Confidence is degraded when statutory fields are defaulted or the
height/historic inputs are approximate.

`massing_inputs.subject_zoning`, `parcel_zoning_match`,
`parcel_zoning_confidence`, `land_category_reason`, `binding_constraint`,
`avg_unit_net_sf`, and `unit_gross_efficiency` are included so downstream UI and
LLM context can distinguish a zoning-grounded parcel from a current-use candidate
or aggregate tract that needs manual review.

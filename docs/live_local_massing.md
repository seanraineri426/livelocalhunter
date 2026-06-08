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
- Density: use the highest density in the local government where residential
  development is allowed, excluding unavailable bonus/variance/special-exception
  values to the extent the extracted zoning rows distinguish them. The 2025
  "currently allowed or July 1, 2023, whichever is least restrictive" benchmark is
  represented by the current extracted maximum because the database does not yet
  store a dated July 1, 2023 density snapshot.
- FAR/intensity: use 150 percent of the highest FAR where development is allowed.
  The statute now says FAR includes floor lot ratio and lot coverage; this model
  stores the statutory 150 percent FAR in `jurisdiction_params.max_far`, but
  `buildable_sf` remains `FAR * lot_sf` because lot-coverage constraints are not
  yet modeled as a separate building footprint/height interaction.
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

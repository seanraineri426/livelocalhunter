export const COUNTY_LABELS = {
  miami_dade: 'Miami-Dade',
  broward: 'Broward',
  palm_beach: 'Palm Beach',
  '12086': 'Miami-Dade',
  '12011': 'Broward',
  '12099': 'Palm Beach',
}

const REVIEW_FLAGS = new Set([
  'oversized_parcel_review_required',
  'manual_site_boundary_required',
  'parcel_zoning_unmatched_review_required',
  'parcel_zoning_qualification_unverified',
  'land_category_from_current_use_or_candidate_bucket',
])

export function money(value) {
  if (value === null || value === undefined || value === '') return 'n/a'
  return Number(value).toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  })
}

export function number(value, maximumFractionDigits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return 'n/a'
  return Number(value).toLocaleString(undefined, { maximumFractionDigits })
}

export function formatAddress(parcel) {
  if (!parcel?.site_address) return ''
  return [parcel.site_address, parcel.site_city, parcel.site_zip].filter(Boolean).join(', ')
}

export function formatAcres(parcel) {
  const acres = parcel?.acreage ?? (parcel?.lot_sf ? Number(parcel.lot_sf) / 43560 : null)
  if (acres === null || acres === undefined || Number.isNaN(Number(acres))) return 'acreage n/a'
  return `${Number(acres).toLocaleString(undefined, { maximumFractionDigits: 2 })} ac`
}

export function reviewRequired(flags = []) {
  return flags.some((flag) => REVIEW_FLAGS.has(flag))
}

export function eligibilityTone(parcel, summary) {
  if (parcel?.eligible === false || summary?.eligibility?.status === 'ineligible') return 'danger'
  if (summary?.eligibility?.review_required || reviewRequired(parcel?.massing_flags || [])) return 'warning'
  if (parcel?.eligible === true || summary?.eligibility?.status === 'eligible') return 'success'
  return 'neutral'
}

export function eligibilityLabel(parcel, summary) {
  if (parcel?.eligible === false) {
    const reasons = parcel.failed_reasons || []
    return reasons.length ? `Ineligible: ${reasons.slice(0, 2).join(', ')}` : 'Ineligible'
  }
  if (summary?.eligibility?.status) return title(summary.eligibility.status)
  if (parcel?.eligible === true) return reviewRequired(parcel.massing_flags || []) ? 'Needs review' : 'Eligible'
  return 'Not computed'
}

export function massingLabel(parcel, contextSummary) {
  if (parcel?.eligible === false || contextSummary?.massing?.applies === false) {
    const reasons = parcel?.failed_reasons || contextSummary?.eligibility?.failed_reasons || []
    return reasons.length ? `Not applicable: ${reasons[0]}` : 'Not applicable'
  }
  if (contextSummary?.massing?.review_required || reviewRequired(parcel?.massing_flags || [])) {
    return 'Review required'
  }
  const units = parcel?.max_units ?? contextSummary?.massing?.max_units
  return units ? `${number(units)} units` : 'n/a'
}

export function markerColor(tone) {
  if (tone === 'success') return '#f5f5f5'
  if (tone === 'warning') return '#a3a3a3'
  if (tone === 'danger') return '#737373'
  return '#d4d4d4'
}

export function title(value = '') {
  return String(value)
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

// Formatting + reference tables for the Cross-border ledger tab.

export const KEY = {
  'Selangor': 'sl', 'W.P. Kuala Lumpur': 'kl', 'W.P. Putrajaya': 'pj', 'Negeri Sembilan': 'ns', 'Melaka': 'me',
  'Johor': 'jh', 'Perak': 'pk', 'Pulau Pinang': 'pg', 'Kedah': 'kh', 'Perlis': 'pl', 'Pahang': 'ph',
  'Kelantan': 'kn', 'Terengganu': 'te', 'Sarawak': 'sk', 'Sabah': 'sa', 'W.P. Labuan': 'la',
}
export const NAME = Object.fromEntries(Object.entries(KEY).map(([n, k]) => [k, n]))
export const short = n => (n || '').replace(/^W\.P\. /, '')

// Sector order = stacking order. Colours validated (dataviz validate_palette.js, light surface; CVD ΔE >= 10.9).
export const SECTORS = [
  { k: 'tourism_loss', label: 'Tourism & hospitality', c: '#D9541E' },
  { k: 'productivity_loss', label: 'Other sectors (sick days)', c: '#2a78d6' },
  { k: 'construction_outdoor_loss', label: 'Construction & outdoor', c: '#eda100' },
  { k: 'agriculture_loss', label: 'Agriculture', c: '#1b8a5a' },
  { k: 'health', label: 'Healthcare', c: '#e87ba4' },
  { k: 'transport_loss', label: 'Transport & logistics', c: '#1c5cab' },
]
export const SECTOR = Object.fromEntries(SECTORS.map(s => [s.k, s]))

export const SCEN = [
  { k: 'best', label: 'Best case' },
  { k: 'expected', label: 'Expected' },
  { k: 'worst', label: 'Worst case' },
]
export const WIN = { 24: '0–24 h', 48: '24–48 h', 72: '48–72 h' }

/** RM, compact: RM 1.40 bn · RM 97.5 m · RM 340 k · RM 230 */
export function rm(v, { unit = true } = {}) {
  if (v == null || !isFinite(v)) return '–'
  const a = Math.abs(v)
  let s
  if (a >= 1e9) s = (v / 1e9).toFixed(2) + ' bn'
  else if (a >= 1e8) s = Math.round(v / 1e6) + ' m'
  else if (a >= 1e6) s = (v / 1e6).toFixed(1) + ' m'
  else if (a >= 1e4) s = Math.round(v / 1e3) + ' k'
  else if (a >= 1e3) s = (v / 1e3).toFixed(1) + ' k'
  else s = Math.round(v).toString()
  return unit ? 'RM ' + s : s
}
export const nf = (v, d = 0) => v == null || !isFinite(v) ? '–'
  : Number(v).toLocaleString('en-MY', { minimumFractionDigits: d, maximumFractionDigits: d })
export const pct = (v, d = 0) => v == null || !isFinite(v) ? '–' : (v * 100).toFixed(d) + '%'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export function dshort(iso) {
  const [y, m, d] = String(iso).split('-').map(Number)
  return `${d} ${MONTHS[m - 1]}`
}
export function dlong(iso) {
  const [y, m, d] = String(iso).split('-').map(Number)
  return `${d} ${MONTHS[m - 1]} ${y}`
}

// Plain-language names for the model's coefficients (assumptions + sensitivity).
export const COEF_LABEL = {
  crf_resp_admission: 'Respiratory admissions: rise per +10 µg/m³',
  crf_cardio_admission: 'Heart admissions: rise per +10 µg/m³',
  crf_outpatient: 'Clinic visits: rise per +10 µg/m³',
  base_resp_adm: 'Baseline respiratory admissions',
  base_cardio_adm: 'Baseline heart admissions',
  base_outpatient: 'Baseline respiratory clinic visits',
  pm25_threshold: 'PM2.5 level where health costs start',
  cost_resp_admission: 'Cost of a respiratory admission',
  cost_cardio_admission: 'Cost of a heart admission',
  cost_outpatient: 'Cost of a clinic visit',
  transport_share_of_services: 'Transport share of services output',
  outdoor_share_of_services: 'Outdoor business share of services output',
  school_cost_per_student_day: 'Cost per student per closed school day',
  cloud_seeding_cost: 'Cost per cloud-seeding operation',
  baseline_gdp_rm_bn: 'State GDP',
  baseline_sector_shares: 'Sector shares of state GDP',
  baseline_tourism: 'Tourism receipts by state',
  baseline_population: 'State population',
  haze_history: 'Haze history (episode so far)',
  forecast_input: 'PM2.5 for the next 72 hours',
  pm25_to_api: 'PM2.5 to API conversion',
  affected_share: 'Share of a state affected',
  double_counting: 'Double counting',
  benchmark_2026: 'Benchmark: 2026 haze',
  benchmark_2013: 'Benchmark: 2013 haze',
}
export function coefLabel(name) {
  if (COEF_LABEL[name]) return COEF_LABEL[name]
  const m = /^loss rate: (.*)$/.exec(name)
  if (m) return `Loss rate: ${m[1]}`
  const lr = /^loss_rate_(.*)$/.exec(name)
  if (lr) return `Loss rate: ${SECTOR[lr[1]] ? SECTOR[lr[1]].label : lr[1]}`
  return name.replace(/_/g, ' ')
}

/** 'DUMMY — replace' / 'APPROX — check' flags in a source string. */
export function quality(src = '') {
  if (/DUMMY/.test(src)) return 'placeholder'
  if (/APPROX/.test(src)) return 'approx'
  return 'sourced'
}
export function cleanSource(src = '') {
  let s = String(src).replace(/^(DUMMY — replace|APPROX — check)[:\s]*/, '').trim()
  if (/^\([^()]*\)$/.test(s)) s = s.slice(1, -1)
  return s
}

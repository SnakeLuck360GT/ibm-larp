// Shared data helpers for the Command map tab: state keys, colour scales, date formatting.

// Forecast "zone" names (physical layer) and states.json names (routing layer) -> geo.js keys.
export const NAME2KEY = {
  'Selangor': 'sl', 'Kuala Lumpur': 'kl', 'W.P. Kuala Lumpur': 'kl', 'Putrajaya': 'pj', 'W.P. Putrajaya': 'pj',
  'Negeri Sembilan': 'ns', 'Melaka': 'me', 'Johor': 'jh', 'Perak': 'pk', 'Pulau Pinang': 'pg', 'Penang': 'pg',
  'Kedah': 'kh', 'Perlis': 'pl', 'Pahang': 'ph', 'Kelantan': 'kn', 'Terengganu': 'te', 'Sarawak': 'sk',
  'Sabah': 'sa', 'Labuan': 'la', 'W.P. Labuan': 'la',
}
export const NAMES = {
  sl: 'Selangor', kl: 'Kuala Lumpur', pj: 'Putrajaya', ns: 'Negeri Sembilan', me: 'Melaka', jh: 'Johor',
  pk: 'Perak', pg: 'Pulau Pinang', kh: 'Kedah', pl: 'Perlis', ph: 'Pahang', kn: 'Kelantan', te: 'Terengganu',
  sk: 'Sarawak', sa: 'Sabah', la: 'Labuan',
}
// Name used by the physical-layer files (forecast + metrics), keyed by geo key.
export const ZONE = Object.fromEntries(Object.entries(NAME2KEY).filter(([n]) => !n.startsWith('W.P.') && n !== 'Penang').map(([n, k]) => [k, n]))
// Big states first; the tiny federal territories last so they sit on top.
export const DRAW_ORDER = ['sa', 'sk', 'pl', 'kh', 'pg', 'pk', 'kn', 'te', 'ph', 'sl', 'ns', 'me', 'jh', 'la', 'kl', 'pj']
export const SMALL = new Set(['kl', 'pj', 'la'])

// One ordinal ramp, light -> dark ("thicker smoke"); lightness falls monotonically so it survives colour-blindness.
const RAMP = ['#e4e9ec', '#f0c872', '#e07533', '#a3301c', '#4a1410']
const INK = ['#111', '#111', '#111', '#fff', '#fff']

// Malaysian Air Pollutant Index bands (DOE).
export const API_SCALE = {
  id: 'api', unit: 'API', unitLong: 'Air Pollutant Index', dp: 0, line: 101, lineLabel: 'Unhealthy (101)',
  arrivalWord: 'unhealthy', arrivalRule: 'first forecast day at or above API 101',
  bands: [
    { lo: 0, hi: 50, label: 'Good', range: '0–50' },
    { lo: 51, hi: 100, label: 'Moderate', range: '51–100' },
    { lo: 101, hi: 200, label: 'Unhealthy', range: '101–200' },
    { lo: 201, hi: 300, label: 'Very unhealthy', range: '201–300' },
    { lo: 301, hi: Infinity, label: 'Hazardous', range: '300+' },
  ].map((b, i) => ({ ...b, c: RAMP[i], ink: INK[i] })),
  band(v) { return v == null ? null : v <= 50 ? 0 : v <= 100 ? 1 : v <= 200 ? 2 : v <= 300 ? 3 : 4 },
  bad(v) { return v != null && v >= 101 },
}

// Live: DOE's API on the same bands. "Now" is DOE's measured API; the +h windows are CAMS PM2.5 converted with pmToApi.
export const LIVE_API_SCALE = {
  ...API_SCALE, id: 'live',
  arrivalWord: 'unhealthy', arrivalRule: 'first 24-h window whose CAMS PM2.5 mean converts to API 101+',
}

// DOE Malaysia PM2.5 sub-index (24-h mean, µg/m³ -> API), from DOE's "API Calculation" sheet (eqms.doe.gov.my).
// [conc lo, conc hi, API lo, API hi]; a value between two rows (e.g. 12.05) belongs to the lower row, as in DOE's IFS formula.
const PM25_API = [[0, 12.0, 0, 50], [12.1, 50.4, 51, 100], [50.5, 55.4, 101, 150], [55.5, 150.4, 151, 200],
  [150.5, 250.4, 201, 300], [250.5, 350.4, 301, 400], [350.5, 500.4, 401, 500]]
export function pmToApi(x) {
  if (x == null || !Number.isFinite(x)) return null
  for (let i = 0; i < PM25_API.length; i++) {
    const [cl, ch, il, ih] = PM25_API[i]
    const next = PM25_API[i + 1] ? PM25_API[i + 1][0] : Infinity
    // the top row extends past 500 (DOE's "Emergency" level) rather than capping
    if (x < next) return Math.round(il + ((ih - il) / (ch - cl)) * (Math.max(x, cl) - cl))
  }
}

// PM2.5 24-h mean, µg/m³: WHO 2021 guideline 15; Malaysian standard (MAAQS 2020) 35; MAAQS 2015 interim target 75.
export const PM_SCALE = {
  id: 'pm', unit: 'µg/m³', unitLong: 'PM2.5, 24-hour mean', dp: 1, line: 35, lineLabel: 'Malaysian standard (35)',
  arrivalWord: 'above the 35 µg/m³ standard', arrivalRule: 'first forecast window above 35 µg/m³',
  bands: [
    { lo: 0, hi: 15, label: 'Within WHO guideline', short: 'Good', range: '0–15' },
    { lo: 15, hi: 35, label: 'Within Malaysian standard', short: 'Under MY limit', range: '15–35' },
    { lo: 35, hi: 75, label: 'Above Malaysian standard', short: 'Over MY limit', range: '35–75' },
    { lo: 75, hi: Infinity, label: 'Above 2015 interim limit', short: 'Very high', range: '75+' },
  ].map((b, i) => ({ ...b, c: RAMP[i], ink: INK[i] })),
  band(v) { return v == null ? null : v <= 15 ? 0 : v <= 35 ? 1 : v <= 75 ? 2 : 3 },
  bad(v) { return v != null && v > 35 },
}

export const fmtV = (v, scale) => (v == null ? '–' : scale.dp ? Number(v).toFixed(scale.dp) : String(Math.round(v)))

const D = s => new Date(s + 'T00:00:00Z')
export const addDays = (s, n) => { const d = D(s); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10) }
export const fmtDay = (s, o = {}) => D(s).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC', ...o })
export const fmtShort = s => D(s).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })
export const fmtMYT = iso => {
  try {
    return new Date(iso).toLocaleString('en-GB', { timeZone: 'Asia/Kuala_Lumpur', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) + ' MYT'
  } catch (e) { return iso }
}

// "Now" / "in 48 h" / "Not within 72 h"
export function arrivalText(h, long) {
  if (h === 0) return long ? 'Already unhealthy today' : 'Now'
  if (h == null) return long ? 'Not forecast within 72 h' : 'Not in 72 h'
  return `in ${h} h`
}
export const ARR_RANK = h => (h == null ? 999 : h)

export const REDUCE = typeof window !== 'undefined' && !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)

// Arrival in the current mode's terms. Replay (API): first forecast day at 101+, 0 = today.
// Live: h = end of the first 24-h window at API 101+ (24 = the next 24 h).
export function arrivalFor(h, scale, long) {
  if (scale.id === 'api') return arrivalText(h, long)
  if (h == null) return long ? 'Not forecast within 72 h' : 'Not in 72 h'
  if (h === 24) return long ? 'Within the next 24 h' : 'Next 24 h'
  return long ? `In the ${h - 24}–${h} h window` : `${h - 24}–${h} h`
}

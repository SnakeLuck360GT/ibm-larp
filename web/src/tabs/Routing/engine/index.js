/* In-browser ranking engine for the Routing tab: runs the trained XGBoost ranker on the current
   year's state rows so the page can re-rank and re-allocate instantly ("simulate").

   Mirrors pipeline/03_equity.py rank_scenario / rank_current on data/ranker_web.json (written by the
   same hourly run as states.json). If ranker_web.json is missing, simulate() falls back to the
   published plan from states.json unchanged (ms 0, meta.trees null). The API below is the contract.

   const engine = await loadEngine(baseUrl)        // baseUrl = import.meta.env.BASE_URL
   engine.states   -> [{ state, key, short, group, population, income_median, income_mean,
                         poverty_rate, beds_per_1000, u_rate, gini, pm25: {'24','48','72'},
                         pm25_ytd, vulnerable }]     // inputs, one per state, plan order
   engine.stock    -> { mobile_clinics, n95 }      // default stock from the pipeline config
   engine.meta     -> { generated_at, reference_year, model: {type, 'ndcg@5', 'baseline_ndcg@5',
                         trained_years}, trees, features, assumptions }
   engine.plan     -> Result for the published plan (same as simulate({}))
   engine.simulate(scenario) -> Result, synchronous, a few ms

   scenario = {
     window: 'all' | '24' | '48' | '72',          // which forecast window to rank on (default 'all')
     pm25: { [state]: number },                    // override PM2.5 (µg/m³) for that window, by state name
     pm25Scale: number,                            // multiply every state's PM2.5 (e.g. 1.5 = haze worsens 50%)
     stock: { mobile_clinics, n95 },               // override stock
   }
   Notes: an override is the final value for that state (pm25Scale is not applied on top of it). For
   window 'all' an override rescales that state's three 24 h windows so their mean equals it, and the
   unhealthy-day feature is recomputed from the three windows (as the pipeline does).
   Result = {
     ranked: [{ state, key, priority, score, risk_score, pm25, mobile_clinic, n95, planPriority, delta }],
                                                   // sorted by priority; delta = planPriority - priority (+ = more urgent)
     used: { mobile_clinics, n95 }, stock, window, ms   // ms = inference time
   }
*/
const KEY = {
  'Selangor': 'sl', 'W.P. Kuala Lumpur': 'kl', 'W.P. Putrajaya': 'pj', 'Negeri Sembilan': 'ns', 'Melaka': 'me',
  'Johor': 'jh', 'Perak': 'pk', 'Pulau Pinang': 'pg', 'Kedah': 'kh', 'Perlis': 'pl', 'Pahang': 'ph',
  'Kelantan': 'kn', 'Terengganu': 'te', 'Sarawak': 'sk', 'Sabah': 'sa', 'W.P. Labuan': 'la',
}
const EAST = new Set(['Sabah', 'Sarawak'])
export const stateKey = name => KEY[name] || name
export const shortName = name => name.replace(/^W\.P\. /, '')
export const groupOf = name => name.startsWith('W.P.') ? 'Federal territory' : EAST.has(name) ? 'East Malaysia' : 'Peninsular'

const WINDOWS = ['24', '48', '72']
const f32 = Math.fround
const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now())

// numpy's round(x, 3): rint (half to even) of x * 1000.
function round3(x) {
  const y = x * 1000
  let r = Math.round(y)
  if (Math.abs(y - Math.trunc(y)) === 0.5) r = 2 * Math.round(y / 2)
  return r / 1000
}

// pandas groupby mean (Kahan sum) and std (Welford, ddof=1); std 0 -> 1, as zscore_by_group.
function zscore(xs) {
  let sum = 0, comp = 0
  for (const x of xs) { const y = x - comp, t = sum + y; comp = (t - sum) - y; sum = t }
  const mean = sum / xs.length
  let m = 0, m2 = 0, n = 0
  for (const x of xs) { n++; const d = x - m; m += d / n; m2 += d * (x - m) }
  let std = Math.sqrt(m2 / (n - 1))
  if (std === 0) std = 1
  return xs.map(x => (x - mean) / std)
}

// XGBoost predict: features as float32, go left when x < split, accumulate leaves in float32.
function predict(trees, baseScore, x) {
  let acc = f32(baseScore)
  for (const [left, right, feat, cond, defLeft] of trees) {
    let n = 0
    while (left[n] !== -1) {
      const v = x[feat[n]]
      n = Number.isNaN(v) ? (defLeft[n] ? left[n] : right[n]) : v < cond[n] ? left[n] : right[n]
    }
    acc = f32(acc + cond[n])
  }
  return acc
}

// Exact port of allocate_n95: need ordered most urgent first.
export function allocateN95(need, eligible, stock, perPerson) {
  const cap = need.map((v, i) => eligible[i] ? Math.floor(v * perPerson) : 0)
  const total = cap.reduce((a, b) => a + b, 0)
  if (total <= stock) return cap
  const share = cap.map(c => Math.min(Math.floor(stock * c / total), c))
  let left = stock - share.reduce((a, b) => a + b, 0)
  for (let i = 0; i < share.length; i++) {
    const add = Math.min(cap[i] - share[i], left)
    share[i] += add
    left -= add
  }
  return share
}

function publishedPlan(statesJson) {
  const mean = p => (p['24'] + p['48'] + p['72']) / 3
  return (scenario = {}) => {
    const window = scenario.window || 'all'
    const ranked = statesJson.states.map(s => ({
      state: s.state, key: stateKey(s.state), priority: window === 'all' ? s.priority : s.priority_by_horizon[window],
      score: s.risk_score, risk_score: s.risk_score, pm25: window === 'all' ? mean(s.pm25) : s.pm25[window],
      mobile_clinic: s.allocation.mobile_clinic, n95: s.allocation.n95, planPriority: s.priority, delta: 0,
    })).sort((a, b) => a.priority - b.priority)
    ranked.forEach(r => { r.delta = r.planPriority - r.priority })
    return {
      ranked, stock: { ...statesJson.stock, ...scenario.stock }, window, ms: 0,
      used: { mobile_clinics: ranked.filter(r => r.mobile_clinic).length, n95: ranked.reduce((t, r) => t + r.n95, 0) },
    }
  }
}

/* Pure: web = ranker_web.json (or null for the published-plan fallback), statesJson = states.json. */
export function buildEngine(web, statesJson) {
  const planPriority = Object.fromEntries(statesJson.states.map(s => [s.state, s.priority]))
  const webRow = web ? Object.fromEntries(web.rows.map(r => [r.state, r])) : {}
  const weights = web ? Object.entries(web.vulnerable_weights) : []
  // vulnerable_people: population * sum(col * wt) / 100, summed in config order.
  const vulnerableOf = r => r.population * weights.reduce((t, [c, w]) => t + r[c] * w, 0) / 100
  const states = statesJson.states.map(s => ({
    state: s.state, key: stateKey(s.state), short: shortName(s.state), group: groupOf(s.state),
    population: s.population, income_median: s.income_median, income_mean: s.income_mean,
    poverty_rate: s.poverty_rate, beds_per_1000: s.beds_per_1000, u_rate: s.u_rate, gini: s.gini,
    pm25: s.pm25, pm25_ytd: s.pm25_ytd, vulnerable: webRow[s.state] ? vulnerableOf(webRow[s.state]) : null,
  }))
  const baseStock = web ? web.stock : statesJson.stock
  const meta = {
    generated_at: statesJson.generated_at, reference_year: statesJson.reference_year, model: statesJson.model,
    trees: web ? web.trees.length : null, features: web ? web.features : null, assumptions: statesJson.assumptions,
  }
  if (!web) {
    const simulate = publishedPlan(statesJson)
    return { states, stock: baseStock, meta, simulate, plan: simulate({}) }
  }

  const { rows, features, base_features: baseFeatures, exposure: [pmCol, udCol], unhealthy } = web
  // Splits and leaves are float32 printed as decimals: round back to float32, or a feature sitting
  // exactly on a split (x == cond) goes left here and right in XGBoost.
  const trees = web.trees.map(([l, r, f, c, d]) => [l, r, f, Float32Array.from(c), d])
  const vulnerable = rows.map(vulnerableOf)
  const fIndex = Object.fromEntries(features.map((f, i) => [f, i]))
  // Columns that never change between scenarios: z-score them once.
  const fixed = {}
  for (const f of baseFeatures) if (f !== pmCol && f !== udCol) fixed[f] = zscore(rows.map(r => r[f]))
  fixed.log_population = zscore(rows.map(r => Math.log(r.population)))

  // Exposure (pm25_mean, unhealthy_days_per_yr) per state for a scenario, as exposure_windows.
  function exposureFor(r, window, override, scale) {
    const perturbed = override != null || scale !== 1
    if (window !== 'all') {
      if (!perturbed) return r.exposure[window]
      const pm = override != null ? override : r.exposure[window][0] * scale
      return [pm, (pm > unhealthy ? 1 : 0) * 365]
    }
    if (!perturbed) return r.exposure.all
    let days = WINDOWS.map(w => r.exposure[w][0])
    if (override != null) {
      const m = (days[0] + days[1] + days[2]) / 3
      days = m > 0 ? days.map(d => d * override / m) : days.map(() => override)
    } else days = days.map(d => d * scale)
    const pm = (days[0] + days[1] + days[2]) / 3
    return [pm, days.filter(d => d > unhealthy).length / 3 * 365]
  }

  function simulate(scenario = {}) {
    const t0 = now()
    const window = scenario.window && scenario.window !== 'all' ? String(scenario.window) : 'all'
    const scale = scenario.pm25Scale == null ? 1 : Number(scenario.pm25Scale)
    const overrides = scenario.pm25 || {}
    const stock = { ...baseStock, ...scenario.stock }

    const exp = rows.map(r => exposureFor(r, window, overrides[r.state] == null ? null : Number(overrides[r.state]), scale))
    const z = { ...fixed, [pmCol]: zscore(exp.map(e => e[0])), [udCol]: zscore(exp.map(e => e[1])) }
    const zKeys = features.map(f => f.replace(/^z_/, ''))
    const out = rows.map((r, i) => {
      const x = new Array(features.length)
      for (const k of zKeys) x[fIndex[`z_${k}`]] = f32(z[k][i])
      const pm = exp[i][0]
      const labelExp = web.label_exposure === udCol ? exp[i][1] : pm
      return { i, state: r.state, score: predict(trees, web.base_score, x), harm: labelExp * vulnerable[i], pm }
    })
    out.sort((a, b) => b.score - a.score || b.harm - a.harm)
    const lo = Math.min(...out.map(o => o.score)), hi = Math.max(...out.map(o => o.score))
    const n95 = allocateN95(out.map(o => vulnerable[o.i]), out.map((_, k) => k + 1 <= web.n95_max_priority),
                            stock.n95, web.n95_per_vulnerable_person)
    const ranked = out.map((o, k) => {
      const priority = k + 1
      const plan = planPriority[o.state] ?? priority
      return {
        state: o.state, key: stateKey(o.state), priority, score: o.score,
        risk_score: hi > lo ? round3((o.score - lo) / (hi - lo)) : 0, pm25: Math.round(o.pm * 10) / 10,
        mobile_clinic: priority <= stock.mobile_clinics, n95: n95[k], planPriority: plan, delta: plan - priority,
      }
    })
    const ms = now() - t0
    return {
      ranked, stock, window, ms,
      used: { mobile_clinics: ranked.filter(r => r.mobile_clinic).length, n95: ranked.reduce((t, r) => t + r.n95, 0) },
    }
  }

  return { states, stock: baseStock, meta, simulate, plan: simulate({}) }
}

export async function loadEngine(baseUrl = './') {
  const get = name => fetch(`${baseUrl}data/${name}`, { cache: 'no-store' })
  const [r, w] = await Promise.all([get('states.json'), get('ranker_web.json').catch(() => null)])
  if (!r.ok) throw new Error(`states.json: HTTP ${r.status}`)
  const statesJson = await r.json()
  let web = null
  try { if (w && w.ok) web = await w.json() } catch { web = null }
  try { return buildEngine(web, statesJson) } catch (e) {
    console.warn('ranking engine unavailable, showing the published plan', e)
    return buildEngine(null, statesJson)
  }
}

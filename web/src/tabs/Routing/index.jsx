/* HazeGuard-MY · Resource routing, "Briefing" design.
   An editorial data story: a headline computed from the live ranking, key figures set in type,
   a "try a scenario" sentence with controls inside it, then 16 small multiples sorted by priority.
   Every control change calls engine.simulate(); the headline, figures and grid re-sort live. */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { loadEngine } from './engine/index.js'
import { pmToApi } from '../Map/scales.js'
import './briefing.css'

const REDUCE = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)
// Shown as DOE's Air Pollutant Index, as on the Command map: each window's CAMS PM2.5 mean (µg/m³) through DOE's
// PM2.5 sub-index formula. The ranking model itself still takes PM2.5 in µg/m³.
const UNHEALTHY = 101 // API, DOE "Unhealthy" band
const bad = a => a != null && a >= UNHEALTHY
const WINS = ['24', '48', '72']
const WIN_TEXT = { all: 'the whole 0–72 h', 24: 'the first day (0–24 h)', 48: 'the second day (24–48 h)', 72: 'the third day (48–72 h)' }
const WIN_SHORT = { all: '0–72 h', 24: '0–24 h', 48: '24–48 h', 72: '48–72 h' }

/* ---------- format ---------- */
const nf = (v, d = 0) => v == null || !isFinite(v) ? '–' : Number(v).toLocaleString('en-MY', { minimumFractionDigits: d, maximumFractionDigits: d })
const people = v => v == null ? '–' : v >= 1e6 ? nf(v / 1e6, 1) + 'M' : nf(v / 1e3, 0) + 'k'
const short = name => name.replace(/^W\.P\. /, '')
const listText = a => a.length <= 1 ? (a[0] || '') : a.length === 2 ? `${a[0]} and ${a[1]}` : `${a.slice(0, -1).join(', ')} and ${a[a.length - 1]}`
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : s
const cx = (...a) => a.filter(Boolean).join(' ')
const pad = n => String(n).padStart(2, '0')
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
function myt(iso) { // "22:58 MYT, Sat 17 Oct" whatever the viewer's time zone
  const t = Date.parse(iso)
  if (!isFinite(t)) return null
  const d = new Date(t + 8 * 3600e3)
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} MYT, ${DOW[d.getUTCDay()]} ${d.getUTCDate()} ${MON[d.getUTCMonth()]}`
}
function ago(iso, now) {
  const m = Math.round((now - Date.parse(iso)) / 60e3)
  if (!isFinite(m)) return ''
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  return h < 48 ? `${h} h ago` : `${Math.floor(h / 24)} days ago`
}
const fmtMs = ms => ms == null || !isFinite(ms) ? '–' : ms < 0.1 ? '<0.1' : ms < 10 ? ms.toFixed(1) : nf(ms)
const meanPm = p => (p['24'] + p['48'] + p['72']) / 3
const basePm = (s, w) => w === 'all' ? meanPm(s.pm25) : s.pm25[w]

/* ---------- scenario ---------- */
const WEST = ['pl', 'kh', 'pg', 'pk', 'sl', 'kl', 'pj', 'ns', 'me', 'jh']
const REGIONS = { all: { text: 'everywhere', keys: null }, west: { text: 'on the west coast', keys: WEST }, east: { text: 'on the east coast', keys: ['kn', 'te', 'ph'] }, borneo: { text: 'in Sarawak and Sabah', keys: ['sk', 'sa'] } }
const regionKeys = where => where.startsWith('st:') ? [where.slice(3)] : REGIONS[where] ? REGIONS[where].keys : null
const whereText = (where, states) => {
  if (where.startsWith('st:')) { const s = states.find(x => x.key === where.slice(3)); return 'in ' + (s ? s.short : where.slice(3)) }
  return (REGIONS[where] || REGIONS.all).text
}
const factorOf = (sc, key) => { const ks = regionKeys(sc.where); return !ks || ks.includes(key) ? sc.haze : 1 }

function toScenario(sc, states) {
  const out = { window: sc.window, stock: { mobile_clinics: sc.clinics, n95: sc.n95 } }
  if (sc.haze !== 1) {
    const ks = regionKeys(sc.where)
    // Everywhere: the documented global multiplier. A region: absolute values for just those states,
    // so the result is the same whether or not the engine also applies pm25Scale to overrides.
    if (!ks) out.pm25Scale = sc.haze
    else out.pm25 = Object.fromEntries(states.filter(s => ks.includes(s.key)).map(s => [s.state, +(basePm(s, sc.window) * sc.haze).toFixed(2)]))
  }
  return out
}
const sameSc = (a, b) => a.haze === b.haze && a.clinics === b.clinics && a.n95 === b.n95 && a.window === b.window && (a.haze === 1 || a.where === b.where)
const presetsFor = D => [
  { id: 'plan', label: 'Published plan', sub: 'as ranked by the pipeline', sc: D },
  { id: 'sumatra', label: 'Sumatra fires', sub: 'west coast haze ×2', sc: { ...D, haze: 2, where: 'west' } },
  { id: 'borneo', label: 'Borneo fires', sub: 'Sarawak and Sabah ×3', sc: { ...D, haze: 3, where: 'borneo' } },
  { id: 'clear', label: 'Haze eases', sub: 'everywhere ×0.5', sc: { ...D, haze: 0.5, where: 'all' } },
  { id: 'short', label: 'Short on stock', sub: '3 clinics, 150,000 N95s', sc: { ...D, clinics: 3, n95: 150000 } },
  { id: 'day1', label: 'Next 24 hours', sub: 'rank on 0–24 h only', sc: { ...D, window: '24' } },
]

/* ---------- reasons: the inputs where a state is most extreme among the 16 ---------- */
function reasonsFor(row, all, win) {
  const feats = [
    { k: 'pm', get: r => r.apiWin, high: true },
    { k: 'pop', get: r => r.population, high: true },
    { k: 'pov', get: r => r.poverty_rate, high: true },
    { k: 'inc', get: r => r.income_median, high: false },
    { k: 'beds', get: r => r.beds_per_1000, high: false },
  ]
  const ranked = feats.map(f => {
    const v = f.get(row)
    if (v == null || !isFinite(v)) return null
    const vals = all.map(f.get).filter(x => x != null && isFinite(x))
    const worse = 1 + vals.filter(x => f.high ? x > v : x < v).length // 1 = most vulnerable on this input
    return { ...f, v, worse, n: vals.length }
  }).filter(Boolean)
  const up = row.priority <= Math.ceil(all.length / 2)
  const pick = [...ranked].sort((a, b) => up ? a.worse - b.worse : b.worse - a.worse).slice(0, 2)
  const say = ({ k, v, worse, n }) => {
    const top = worse === 1, bottom = worse === n
    if (up) return {
      pm: bad(v) ? `API ${nf(v, 0)}, unhealthy` : `API ${nf(v, 0)}${top ? ', the highest' : ''}`,
      pop: `${people(v)} people${top ? ', the most' : ''}`,
      pov: `${nf(v, 1)}% of homes poor${top ? ', the most' : ''}`,
      inc: `median income RM ${nf(v)}${top ? ', the lowest' : ''}`,
      beds: `${nf(v, 2)} beds per 1,000${top ? ', the fewest' : ''}`,
    }[k]
    return {
      pm: `API ${nf(v, 0)}${bottom ? ', the cleanest' : ''}`,
      pop: `${people(v)} people${bottom ? ', the fewest' : ''}`,
      pov: `${nf(v, 1)}% of homes poor`,
      inc: `median income RM ${nf(v)}`,
      beds: `${nf(v, 2)} beds per 1,000`,
    }[k]
  }
  return { up, text: cap(pick.map(say).join('; ')) }
}

/* ---------- hooks ---------- */
function useNow(ms) {
  const [n, setN] = useState(Date.now())
  useEffect(() => { const id = setInterval(() => setN(Date.now()), ms); return () => clearInterval(id) }, [ms])
  return n
}
// Count numbers to their new value so a change in the scenario is visible in the figures.
function useTween(v, ms = 480) {
  const [d, setD] = useState(v)
  const cur = useRef(v)
  useEffect(() => {
    if (REDUCE || v == null || !isFinite(v) || cur.current == null || !isFinite(cur.current)) { cur.current = v; setD(v); return }
    const a = cur.current, t0 = performance.now()
    let id
    const step = t => {
      const k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3)
      cur.current = a + (v - a) * e
      setD(cur.current)
      if (k < 1) id = requestAnimationFrame(step)
    }
    id = requestAnimationFrame(step)
    return () => cancelAnimationFrame(id)
  }, [v])
  return d
}
// Panels slide to their new place in the grid when the ranking changes (FLIP, in two dimensions).
function useFlipGrid(ref, orderKey) {
  const prev = useRef(new Map())
  const snap = useCallback(() => {
    const m = new Map()
    ref.current && ref.current.querySelectorAll('[data-flip]').forEach(n => m.set(n.dataset.flip, [n.offsetLeft, n.offsetTop]))
    return m
  }, [ref])
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const next = snap()
    if (!REDUCE) el.querySelectorAll('[data-flip]').forEach(n => {
      const a = prev.current.get(n.dataset.flip), b = next.get(n.dataset.flip)
      if (!a || !n.animate) return
      const dx = a[0] - b[0], dy = a[1] - b[1]
      if (Math.abs(dx) > 1 || Math.abs(dy) > 1) {
        // Panels moving up the ranking travel on top of the ones they pass.
        const up = dy < -1 || (Math.abs(dy) <= 1 && dx < 0)
        n.style.zIndex = up ? 2 : 1
        const anim = n.animate([{ transform: `translate(${dx}px,${dy}px)` }, { transform: 'none' }], { duration: 620, easing: 'cubic-bezier(.2,.75,.2,1)' })
        anim.onfinish = anim.oncancel = () => { n.style.zIndex = '' }
      }
    })
    prev.current = next
  }, [orderKey])
  useEffect(() => {
    const f = () => { prev.current = snap() }
    addEventListener('resize', f)
    return () => removeEventListener('resize', f)
  }, [snap])
}

/* ---------- pieces ---------- */
const Up = () => <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M5 1.5 9 7.5H1z" fill="currentColor" /></svg>
const Down = () => <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M5 8.5 1 2.5h8z" fill="currentColor" /></svg>

function Move({ d, was }) {
  if (!d) return null
  return (
    <span className={cx('briefing-move', d > 0 ? 'up' : 'down')} title={`Published plan: priority ${was}`}>
      {d > 0 ? <Up /> : <Down />}{Math.abs(d)}
      <span className="briefing-sr">{d > 0 ? ' places up' : ' places down'} from {was} in the published plan</span>
    </span>
  )
}

function Stepper({ id, label, value, set, min, max, step, fmt, width }) {
  const clamp = v => Math.max(min, Math.min(max, v))
  const [text, setText] = useState(null)
  return (
    <span className="briefing-step">
      <button type="button" onClick={() => set(clamp(value - step))} disabled={value <= min} aria-label={`Fewer ${label}`}>−</button>
      <input id={id} type="text" inputMode="numeric" aria-label={label} style={{ width }}
        value={text != null ? text : fmt(value)}
        onFocus={e => { setText(String(value)); requestAnimationFrame(() => e.target.select()) }}
        onChange={e => setText(e.target.value)}
        onBlur={() => { const v = parseInt(String(text).replace(/[^\d]/g, ''), 10); if (isFinite(v)) set(clamp(v)); setText(null) }}
        onKeyDown={e => {
          if (e.key === 'Enter') e.target.blur()
          else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); const v = clamp(value + (e.key === 'ArrowUp' ? step : -step)); set(v); setText(String(v)) }
        }} />
      <button type="button" onClick={() => set(clamp(value + step))} disabled={value >= max} aria-label={`More ${label}`}>+</button>
    </span>
  )
}

function Scenario({ sc, set, D, states, result, ms, changes, onReset, isPlan }) {
  const presets = presetsFor(D)
  const upd = patch => set({ ...sc, ...patch })
  return (
    <section className="briefing-scen" aria-labelledby="briefing-scen-h">
      <div className="briefing-scen-top">
        <h2 id="briefing-scen-h" className="briefing-kick">Try a scenario</h2>
        <button type="button" className="briefing-reset" onClick={onReset} disabled={isPlan}>Reset</button>
      </div>
      <p className="briefing-sentence">
        If haze is{' '}
        <span className="briefing-haze">
          <input type="range" min="0.5" max="4" step="0.1" value={sc.haze} aria-label="Haze multiplier on forecast PM2.5"
            aria-valuetext={`${sc.haze.toFixed(1)} times the forecast`} style={{ '--p': (sc.haze - 0.5) / 3.5 * 100 + '%' }}
            onChange={e => upd({ haze: +(+e.target.value).toFixed(1) })} />
          <b className="num" aria-hidden="true">{sc.haze.toFixed(1)}×</b>
        </span>{' '}
        the forecast{' '}
        <span className="briefing-nw"><span className="briefing-sel">
          <span className="briefing-sel-text" aria-hidden="true">{whereText(sc.where, states)}</span>
          <select aria-label="Where the haze change applies" value={sc.where} onChange={e => upd({ where: e.target.value })}>
            <optgroup label="Region">
              {Object.entries(REGIONS).map(([k, r]) => <option key={k} value={k}>{r.text}</option>)}
            </optgroup>
            <optgroup label="One state">
              {[...states].sort((a, b) => a.short.localeCompare(b.short)).map(s => <option key={s.key} value={'st:' + s.key}>in {s.short}</option>)}
            </optgroup>
          </select>
        </span>, and</span> we have{' '}
        <Stepper id="briefing-clinics" label="mobile clinics" value={sc.clinics} set={v => upd({ clinics: v })} min={0} max={16} step={1} fmt={v => String(v)} width="2.2ch" />{' '}
        <span className="briefing-nw">mobile clinics</span> and{' '}
        <Stepper id="briefing-n95" label="N95 masks" value={sc.n95} set={v => upd({ n95: v })} min={0} max={2000000} step={50000} fmt={v => nf(v)} width="7.6ch" />{' '}
        <span className="briefing-nw">N95 masks,</span> ranked on{' '}
        <span className="briefing-nw"><span className="briefing-sel">
          <span className="briefing-sel-text" aria-hidden="true">{WIN_TEXT[sc.window]} forecast</span>
          <select aria-label="Forecast window to rank on" value={sc.window} onChange={e => upd({ window: e.target.value })}>
            {['all', ...WINS].map(w => <option key={w} value={w}>{WIN_TEXT[w]} forecast</option>)}
          </select>
        </span>…</span>
      </p>
      <div className="briefing-presets" role="group" aria-label="Preset scenarios">
        {presets.map(p => (
          <button key={p.id} type="button" aria-pressed={sameSc(sc, p.sc)} onClick={() => set(p.sc)}>
            <b>{p.label}</b><span>{p.sub}</span>
          </button>
        ))}
      </div>
      <p className="briefing-ran" aria-live="polite">
        <span className="briefing-ranmark" aria-hidden="true" />
        <span>
          <b>XGBoost re-ranked {result.ranked.length} states in {fmtMs(ms)} ms</b>
          {!isPlan && <>{' · '}{changes.moved ? `${changes.moved} ${changes.moved === 1 ? 'state' : 'states'} changed place` : 'no state changed place'}</>}
          {!isPlan && changes.clinic && <>{' · '}{changes.clinic}</>}
        </span>
      </p>
    </section>
  )
}

function MiniChart({ api, yMax, win }) {
  return (
    <div className="briefing-mc" aria-hidden="true">
      <div className="briefing-mc-plot">
        <span className="briefing-mc-std" style={{ bottom: UNHEALTHY / yMax * 100 + '%' }} />
        {WINS.map(w => (
          <span key={w} className={cx('briefing-mc-col', win !== 'all' && win !== w && 'dim')}>
            <i className={bad(api[w]) ? 'over' : ''} style={{ height: Math.max(2, Math.min(1, api[w] / yMax) * 100) + '%' }} />
          </span>
        ))}
      </div>
      <div className="briefing-mc-v">
        {WINS.map(w => <span key={w} className={cx('num', bad(api[w]) && 'over', win !== 'all' && win !== w && 'dim')}>{nf(api[w], 0)}</span>)}
      </div>
    </div>
  )
}

const getsText = (clinic, n95) => clinic && n95 ? `Mobile clinic + ${nf(n95)} N95s` : clinic ? 'Mobile clinic' : n95 ? `${nf(n95)} N95s` : 'No clinic or masks'
const getsShort = (clinic, n95) => clinic && n95 ? `clinic + ${nf(n95)}` : clinic ? 'clinic' : n95 ? `${nf(n95)} N95s` : 'nothing'

function Panel({ r, yMax, win, sel, onPick, isPlan }) {
  const changed = !isPlan && (r.mobile_clinic !== r.plan.mobile_clinic || r.n95 !== r.plan.n95)
  const tone = r.mobile_clinic ? 'clinic' : r.n95 ? 'mask' : 'none'
  const label = `Priority ${r.priority}: ${r.short}. Risk ${nf(r.risk_score, 2)}. Gets ${getsText(r.mobile_clinic, r.n95).toLowerCase()}. ` +
    `Forecast API ${WINS.map(w => `${WIN_SHORT[w]} ${nf(r.api[w], 0)}`).join(', ')}. ${r.why.text}.`
  return (
    <li data-flip={r.key} className="briefing-cell">
      <button type="button" className={cx('briefing-panel', tone, sel && 'sel')} aria-pressed={sel} aria-label={label} onClick={() => onPick(r.key)}>
        <span className="briefing-p-top">
          <span className="briefing-rank num">{r.priority}</span>
          <Move d={isPlan ? 0 : r.delta} was={r.planPriority} />
        </span>
        <span className="briefing-p-name">{r.short}</span>
        <MiniChart api={r.api} yMax={yMax} win={win} />
        <span className="briefing-p-risk">
          <span className="briefing-p-k">Risk</span>
          <span className="briefing-track" aria-hidden="true"><i style={{ width: Math.max(0, Math.min(1, r.risk_score)) * 100 + '%' }} /></span>
          <b className="num">{nf(r.risk_score, 2)}</b>
        </span>
        <span className={cx('briefing-p-gets', tone)}>
          <span className={r.mobile_clinic ? 'yes' : 'no'}><i aria-hidden="true" />{r.mobile_clinic ? 'Mobile clinic' : 'No clinic'}</span>
          <span className={r.n95 ? 'yes' : 'no'}><i aria-hidden="true" className="m" />{r.n95 ? `${nf(r.n95)} N95s` : 'No N95s'}</span>
          {changed && <small className="briefing-was">Plan: {getsShort(r.plan.mobile_clinic, r.plan.n95)}</small>}
        </span>
      </button>
    </li>
  )
}

/* ---------- table ---------- */
const COLS = [
  { k: 'priority', label: 'Priority', get: r => r.priority, first: 'asc', num: true },
  { k: 'state', label: 'State', get: r => r.short, first: 'asc' },
  { k: 'risk', label: 'Risk score', get: r => r.risk_score, first: 'desc', num: true },
  { k: 'pm', label: 'Forecast API', unit: 'from PM2.5', get: r => r.apiWin, first: 'desc', num: true },
  { k: 'income', label: 'Median income', unit: 'RM/month', get: r => r.income_median, first: 'asc', num: true },
  { k: 'poverty', label: 'Poverty', unit: '% homes', get: r => r.poverty_rate, first: 'desc', num: true },
  { k: 'beds', label: 'Beds', unit: 'per 1,000', get: r => r.beds_per_1000, first: 'asc', num: true },
  { k: 'n95', label: 'N95s', get: r => r.n95, first: 'desc', num: true },
  { k: 'clinic', label: 'Mobile clinic', get: r => r.mobile_clinic ? 1 : 0, first: 'desc' },
]
function Table({ rows, win, isPlan }) {
  const [sort, setSort] = useState({ k: 'priority', dir: 'asc' })
  const c = COLS.find(x => x.k === sort.k), dir = sort.dir === 'asc' ? 1 : -1
  const sorted = [...rows].sort((a, b) => {
    const x = c.get(a), y = c.get(b)
    if (x == null || y == null) return (x == null) - (y == null) || a.priority - b.priority
    return (typeof x === 'string' ? x.localeCompare(y) : x - y) * dir || a.priority - b.priority
  })
  const onSort = k => setSort(s => s.k === k ? { k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { k, dir: COLS.find(x => x.k === k).first })
  return (
    <div className="briefing-tablewrap" tabIndex={0} role="region" aria-label="All figures by state, scrolls sideways on small screens">
      <table className="briefing-table">
        <caption className="briefing-sr">All 16 states. Forecast API is the {WIN_SHORT[win]} CAMS PM2.5 mean converted to DOE's Air Pollutant Index. Select a column heading to sort.</caption>
        <thead>
          <tr>
            {COLS.map(col => {
              const on = sort.k === col.k
              return (
                <th key={col.k} scope="col" className={col.num ? 'n' : undefined} aria-sort={on ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" className={cx('briefing-th', on && 'on')} onClick={() => onSort(col.k)}>
                    <span>{col.label}{col.unit && <small>{col.unit}</small>}</span>
                    <span className="briefing-sorti" aria-hidden="true">{on ? (sort.dir === 'asc' ? '↑' : '↓') : '↕'}</span>
                  </button>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map(r => (
            <tr key={r.key}>
              <td className="n"><b className="num">{r.priority}</b>{!isPlan && <Move d={r.delta} was={r.planPriority} />}</td>
              <th scope="row">{r.short}</th>
              <td className="n num">{nf(r.risk_score, 2)}</td>
              <td className={cx('n num', bad(r.apiWin) && 'briefing-over')}>{nf(r.apiWin, 0)}</td>
              <td className="n num">{nf(r.income_median)}</td>
              <td className="n num">{nf(r.poverty_rate, 1)}</td>
              <td className="n num">{nf(r.beds_per_1000, 2)}</td>
              <td className="n num">{r.n95 ? nf(r.n95) : <span className="muted">0</span>}</td>
              <td>{r.mobile_clinic ? 'Yes' : <span className="muted">No</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ---------- assumptions ---------- */
function Value({ v }) {
  if (v != null && typeof v === 'object') v = JSON.stringify(v)
  try { // config dicts like {"pct_65plus": 1} read better as "pct_65plus 1 · ..."
    const o = JSON.parse(v)
    if (o && typeof o === 'object' && !Array.isArray(o)) return <span className="mono">{Object.entries(o).map(([k, x]) => `${k} ${x}`).join(' · ')}</span>
  } catch (e) { /* plain text */ }
  return v == null ? '' : String(v)
}
function Source({ text }) {
  return String(text || '').split(/(https?:\/\/[^\s;,)]+)/).map((p, i) => /^https?:\/\//.test(p)
    ? <a key={i} href={p} target="_blank" rel="noopener noreferrer">{p.replace(/^https?:\/\//, '')}</a>
    : p)
}

function Expander({ id, title, count, open, setOpen, children }) {
  return (
    <section className={cx('briefing-exp', open && 'open')}>
      <h2>
        <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
          <span className="briefing-chev" aria-hidden="true" />{title}{count != null && <span className="briefing-count num">{count}</span>}
        </button>
      </h2>
      {open && <div id={id} className="briefing-expb">{children}</div>}
    </section>
  )
}

/* ---------- page ---------- */
export default function Briefing({ baseUrl = './', stKey, select }) {
  const [engine, setEngine] = useState(null)
  const [error, setError] = useState(null)
  const now = useNow(30e3)
  const load = useCallback(() => {
    loadEngine(baseUrl)
      .then(e => { setEngine(old => old && old.meta.generated_at === e.meta.generated_at ? old : e); setError(null) })
      .catch(e => setError(e.message || String(e)))
  }, [baseUrl])
  useEffect(() => { load(); const id = setInterval(load, 10 * 60e3); return () => clearInterval(id) }, [load]) // the pipeline re-ranks hourly

  const D = useMemo(() => engine && { haze: 1, where: 'all', clinics: engine.stock.mobile_clinics, n95: engine.stock.n95, window: 'all' }, [engine])
  const [scState, setSc] = useState(null)
  const sc = scState || D
  const [selLocal, setSelLocal] = useState(null)
  const [tableOpen, setTableOpen] = useState(false)
  const [assumeOpen, setAssumeOpen] = useState(false)
  const [howOpen, setHowOpen] = useState(false)
  const gridRef = useRef(null)

  // Run the model on every control change.
  const run = useMemo(() => {
    if (!engine || !sc) return null
    const t0 = performance.now()
    const result = engine.simulate(toScenario(sc, engine.states))
    const measured = performance.now() - t0
    return { result, ms: result.ms || measured }
  }, [engine, sc])

  const view = useMemo(() => {
    if (!run) return null
    const { result } = run
    const byKey = Object.fromEntries(engine.states.map(s => [s.key, s]))
    const planBy = Object.fromEntries(engine.plan.ranked.map(r => [r.key, r]))
    const rows = result.ranked.map(r => {
      const s = byKey[r.key] || {}
      const f = factorOf(sc, r.key)
      const pm = Object.fromEntries(WINS.map(w => [w, s.pm25 ? s.pm25[w] * f : null]))
      const api = Object.fromEntries(WINS.map(w => [w, pmToApi(pm[w])]))
      const pmWin = sc.window === 'all' ? meanPm(pm) : pm[sc.window]
      return { ...s, ...r, short: s.short || short(r.state), pm, api, pmWin, apiWin: pmToApi(pmWin), plan: planBy[r.key] || r }
    })
    rows.forEach(r => { r.why = reasonsFor(r, rows, sc.window) })
    const yMax = Math.max(150, Math.ceil(Math.max(...rows.flatMap(r => WINS.map(w => r.api[w] || 0))) / 50) * 50)
    // Peak forecast API, by window, with ties (Selangor and KL share one CAMS grid cell).
    let peak = { v: -1, names: [], w: null }
    rows.forEach(r => WINS.forEach(w => {
      const v = r.api[w] ?? -1
      if (v > peak.v) peak = { v, names: [r.short], w }
      else if (v === peak.v && !peak.names.includes(r.short)) peak.names.push(r.short)
    }))
    const over = rows.filter(r => WINS.some(w => bad(r.api[w]))).length
    const clinicRows = rows.filter(r => r.mobile_clinic)
    const n95Rows = rows.filter(r => r.n95 > 0)
    const moved = rows.filter(r => r.delta).length
    const gained = rows.filter(r => r.mobile_clinic && !r.plan.mobile_clinic).map(r => r.short)
    const lost = rows.filter(r => !r.mobile_clinic && r.plan.mobile_clinic).map(r => r.short)
    const clinic = gained.length || lost.length
      ? [gained.length && `${gained.length === 1 ? 'clinic' : 'clinics'} added for ${listText(gained)}`, lost.length && `${listText(lost)} ${lost.length === 1 ? 'loses its' : 'lose their'} ${lost.length === 1 ? 'clinic' : 'clinics'}`].filter(Boolean).join('; ')
      : ''
    return { rows, yMax, peak, over, clinicRows, n95Rows, changes: { moved, clinic } }
  }, [run])

  const isPlan = !!(sc && D && sameSc(sc, D))
  const v = view
  const tClin = useTween(v ? run.result.used.mobile_clinics : null)
  const tN95 = useTween(v ? run.result.used.n95 : null)
  const tPeak = useTween(v ? v.peak.v : null)
  useFlipGrid(gridRef, v ? v.rows.map(r => r.key).join() : '')

  if (!engine || !v) {
    return (
      <div className="briefing briefing-empty" role="status">
        {error
          ? <><b>Couldn't load the priority list</b><span>{baseUrl}data/states.json: {error}</span><button type="button" className="btn" onClick={load}>Try again</button></>
          : <><b>Loading the model and the latest plan…</b></>}
      </div>
    )
  }

  const { result } = run
  const meta = engine.meta, model = meta.model || {}
  // The 'Actions' entry names the old action tiers; the page shows what each state gets instead.
  const assumptionList = (meta.assumptions || []).filter(a => a && a.name !== 'Actions')
  const top3 = v.rows.slice(0, 3).map(r => r.short)
  const planTop3 = engine.plan.ranked.slice(0, 3).map(r => r.key)
  const known = k => v.rows.some(r => r.key === k)
  const selKey = (known(stKey) && stKey) || (known(selLocal) && selLocal) || null
  const pick = k => { const n = selKey === k ? null : k; setSelLocal(n); select && n && select(n) }
  const stock = result.stock || {}
  const clinicsUsed = result.used.mobile_clinics, n95Used = result.used.n95
  const fresh = meta.generated_at && (Date.now() - Date.parse(meta.generated_at) < 3 * 3600e3)
  const winNote = sc.window === 'all' ? '' : ` on the ${WIN_SHORT[sc.window]} forecast`
  const ndcg = model['ndcg@5'], base = model['baseline_ndcg@5']


  return (
    <article className={cx('briefing', !isPlan && 'is-scen')}>
      <header className="briefing-head">
        <div className="briefing-lede">
          <p className="briefing-kick briefing-kickline">
            <span>Resource routing</span>
            {isPlan
              ? <span className="briefing-live">{fresh && <i aria-hidden="true" />}Live · {myt(meta.generated_at)} <span className="briefing-ago">({ago(meta.generated_at, now)})</span></span>
              : <span className="briefing-scenflag">Scenario</span>}
          </p>
          <h1 className="briefing-h1" key={top3.join() + isPlan}>
            {top3.map((n, i) => (
              <span key={n}>
                <span className={cx('briefing-name', !isPlan && !planTop3.includes(v.rows[i].key) && 'new')}>{n}</span>
                {i < top3.length - 2 ? ', ' : i === top3.length - 2 ? ' and ' : ''}
              </span>
            ))}{' '}need help first{winNote && <span className="briefing-h1win">{winNote}</span>}
          </h1>
        </div>

        <Scenario sc={sc} set={setSc} D={D} states={engine.states} result={result} ms={run.ms} changes={v.changes} isPlan={isPlan} onReset={() => setSc(null)} />

        <dl className="briefing-figs">
          <div>
            <dt>Mobile clinics placed</dt>
            <dd className="num"><b>{nf(tClin)}</b><span className="briefing-of">/{nf(stock.mobile_clinics)}</span></dd>
            <dd className="briefing-fsub">{clinicsUsed ? `Priorities 1–${clinicsUsed}` : 'None in stock'}</dd>
          </div>
          <div>
            <dt>N95 masks allocated</dt>
            <dd className="num"><b>{nf(tN95)}</b></dd>
            <dd className="briefing-fsub">of {nf(stock.n95)}, to {v.n95Rows.length} states</dd>
          </div>
          <div>
            <dt>Peak forecast API</dt>
            <dd className="num"><b className={bad(v.peak.v) ? 'briefing-over' : ''}>{nf(tPeak, 0)}</b><span className="briefing-of"> {bad(v.peak.v) ? 'unhealthy' : 'API'}</span></dd>
            <dd className="briefing-fsub">{v.peak.names[0]}{v.peak.names.length > 1 ? ` +${v.peak.names.length - 1}` : ''}, {WIN_SHORT[v.peak.w]}</dd>
          </div>
          <div>
            <dt>Ranker NDCG@5</dt>
            <dd className="num"><b>{ndcg != null ? ndcg.toFixed(3) : '–'}</b></dd>
            <dd className="briefing-fsub">vs {base != null ? base.toFixed(3) : '–'} baseline · proxy label</dd>
          </div>
        </dl>
      </header>

      <section className="briefing-gridsec" aria-labelledby="briefing-grid-h">
        <div className="briefing-gridh">
          <h2 id="briefing-grid-h">All {v.rows.length} states, most urgent first{winNote}</h2>
          <p className="briefing-legend">
            <span className="briefing-lg"><i className="bar" />Forecast API · 24 / 48 / 72 h</span>
            <span className="briefing-lg"><i className="std" />101 unhealthy</span>
            <span className="briefing-lg"><i className="bar over" />unhealthy</span>
            <span className="briefing-lg"><i className="sq clinic" />clinic</span>
            <span className="briefing-lg"><i className="sq mask" />N95s only</span>
          </p>
        </div>
        <ol className="briefing-grid" ref={gridRef}>
          {v.rows.map(r => <Panel key={r.key} r={r} yMax={v.yMax} win={sc.window} sel={r.key === selKey} onPick={pick} isPlan={isPlan} />)}
        </ol>
      </section>

      <footer className="briefing-foot">
        <Expander id="briefing-how" title="How it works" open={howOpen} setOpen={setHowOpen}>
          <ul className="briefing-notes">
            <li><b>Model:</b> {model.type || 'XGBRanker'}{meta.trees ? `, ${meta.trees} trees` : ''}, trained {model.trained_years ? `${model.trained_years[0]}–${model.trained_years[model.trained_years.length - 1]}` : 'on past years'}.</li>
            <li><b>Label:</b> proxy, PM2.5 × vulnerable people (65+, under-5, poor ×2). No official list exists.</li>
            <li><b>NDCG@5:</b> {ndcg != null ? ndcg.toFixed(3) : '–'} vs {base != null ? base.toFixed(3) : '–'} baseline, leave-one-year-out, against that proxy.</li>
            <li><b>Forecast:</b> Copernicus CAMS PM2.5 at each state capital. Each 24-h window mean is shown as DOE's Air Pollutant Index (DOE's PM2.5 sub-index formula, as on the Command map); unhealthy = API 101+. The ranking model itself takes the PM2.5 values in µg/m³.</li>
            <li><b>Allocation:</b> one clinic per state down the ranking; N95s to the top 10 by vulnerable people. Stock is a placeholder.</li>
            <li><b>Scenarios:</b> re-run the same model in your browser. Nothing is saved.</li>
          </ul>
        </Expander>

        <Expander id="briefing-table" title="All figures, sortable" count={v.rows.length} open={tableOpen} setOpen={setTableOpen}>
          <Table rows={v.rows} win={sc.window} isPlan={isPlan} />
        </Expander>
        <Expander id="briefing-assume" title="Assumptions and sources" count={assumptionList.length} open={assumeOpen} setOpen={setAssumeOpen}>
          <dl className="briefing-assume">
            {assumptionList.map((a, i) => (
              <div key={i}>
                <dt>{String(a.name || '').replace(/^Dataset:\s*/i, 'Data: ')}</dt>
                <dd><Value v={a.value} />{a.unit ? <span className="muted"> ({a.unit})</span> : null}</dd>
                <dd className="briefing-src"><Source text={a.source} /></dd>
              </div>
            ))}
          </dl>
        </Expander>
        <p className="briefing-credit">
          Data: <a href="https://open.dosm.gov.my" target="_blank" rel="noopener noreferrer">OpenDOSM</a> / <a href="https://data.gov.my" target="_blank" rel="noopener noreferrer">data.gov.my</a> (CC BY 4.0) · PM2.5: Copernicus CAMS via Open-Meteo (CC BY 4.0), shown as API with DOE's formula
        </p>
      </footer>
    </article>
  )
}

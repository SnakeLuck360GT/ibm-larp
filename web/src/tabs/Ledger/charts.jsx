// Hand-written SVG/CSS charts for the Cross-border ledger tab.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { MY_GEO, MY_VB } from '../Routing/geo.js'
import { NAME, SECTORS, WIN, dlong, dshort, rm, short } from './format.js'

/* ---------- utilities ---------- */
export function useWidth(fallback = 800) {
  const ref = useRef(null)
  const [w, setW] = useState(fallback)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const set = () => setW(Math.max(200, Math.round(el.getBoundingClientRect().width)))
    set()
    if (typeof ResizeObserver === 'undefined') { addEventListener('resize', set); return () => removeEventListener('resize', set) }
    const ro = new ResizeObserver(set)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

function niceMax(v) {
  if (!(v > 0)) return 1
  const p = Math.pow(10, Math.floor(Math.log10(v)))
  const f = v / p
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p
}

/* ---------- range strip: low · central · high on one track ---------- */
export function RangeTrack({ lo, mid, hi, max, tone = 'ink', small }) {
  const x = v => `${Math.max(0, Math.min(100, (v / max) * 100))}%`
  return (
    <span className={`ledger-trk${small ? ' sm' : ''} ${tone}`} aria-hidden="true">
      <i className="ledger-trk-rng" style={{ left: x(lo), width: `calc(${x(hi)} - ${x(lo)})` }} />
      <i className="ledger-trk-cap" style={{ left: x(lo) }} />
      <i className="ledger-trk-cap" style={{ left: x(hi) }} />
      {mid != null && <i className="ledger-trk-mid" style={{ left: x(mid) }} />}
    </span>
  )
}

/* ---------- headline 72 h range with published benchmark ---------- */
export function HeroRange({ best, expected, worst, bench, scen, setScen }) {
  const max = niceMax(Math.max(worst, bench ? bench[1] : 0) * 1.08)
  const x = v => (v / max) * 100
  const ticks = [0, max / 4, max / 2, (3 * max) / 4, max]
  const marks = [['best', best, 'Best'], ['expected', expected, 'Expected'], ['worst', worst, 'Worst']]
  return (
    <div className="ledger-hr" role="img"
      aria-label={`Next 72 hours: best case ${rm(best)}, expected ${rm(expected)}, worst case ${rm(worst)}${bench ? `; published 2026 estimate scaled to 3 days ${rm(bench[0])} to ${rm(bench[1])}` : ''}`}>
      <div className="ledger-hr-plot">
        {bench && (
          <div className="ledger-hr-bench" style={{ left: x(bench[0]) + '%', width: x(bench[1] - bench[0]) + '%' }}>
            <span>Published estimate × 3 days</span>
          </div>
        )}
        <div className="ledger-hr-rng" style={{ left: x(best) + '%', width: x(worst - best) + '%' }} />
        {marks.map(([k, v, l]) => (
          <button type="button" key={k} className={`ledger-hr-m ${k}${scen === k ? ' on' : ''}`} style={{ left: x(v) + '%' }}
            onClick={() => setScen(k)} aria-pressed={scen === k} aria-label={`${l} case: ${rm(v)}`} tabIndex={-1}>
            <i />
            <span><b className="num">{rm(v)}</b><small>{l}</small></span>
          </button>
        ))}
      </div>
      <div className="ledger-hr-ax mono">
        {ticks.map((t, i) => <span key={i} style={{ left: x(t) + '%' }}>{i === 0 ? 'RM 0' : rm(t, { unit: false })}</span>)}
      </div>
    </div>
  )
}

/* ---------- map: expected damage in the selected window ---------- */
const BINS = [1e5, 1e6, 5e6, 1e7]
const RAMP = ['#f7e8e0', '#f0c3a9', '#e5916a', '#D9541E', '#963412']
const binOf = v => (v == null ? -1 : BINS.findIndex(b => v < b) === -1 ? BINS.length : BINS.findIndex(b => v < b))
export const BIN_LABELS = ['under RM 0.1 m', 'RM 0.1–1 m', 'RM 1–5 m', 'RM 5–10 m', 'RM 10 m or more']

export function DamageMap({ values, sel, onSelect, horizon }) {
  const [hov, setHov] = useState(null)
  const order = useMemo(() => {
    const ks = Object.keys(MY_GEO)
    return [...ks.filter(k => k !== 'kl' && k !== 'pj'), 'kl', 'pj'].filter(k => MY_GEO[k])
  }, [])
  const [vx, vy, vw, vh] = MY_VB
  const tipKey = hov || null
  const tip = tipKey && MY_GEO[tipKey]
  return (
    <div className="ledger-map">
      <svg viewBox={MY_VB.join(' ')} preserveAspectRatio="xMidYMid meet" role="group"
        aria-label={`Map: expected damage per state in the ${WIN[horizon]} window`}>
        {order.map(k => {
          const v = values[NAME[k]]
          const b = binOf(v)
          const on = sel === k
          return (
            <path key={k} d={MY_GEO[k].d} className={`ledger-st${on ? ' on' : ''}`}
              fill={b < 0 ? '#eee' : RAMP[b]} tabIndex={0} role="button" aria-pressed={on}
              aria-label={`${short(NAME[k])}: ${rm(v)} expected in the ${WIN[horizon]} window`}
              onClick={() => onSelect(k)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(k) } }}
              onMouseEnter={() => setHov(k)} onMouseLeave={() => setHov(null)} onFocus={() => setHov(k)} onBlur={() => setHov(null)} />
          )
        })}
        {sel && MY_GEO[sel] && <path d={MY_GEO[sel].d} className="ledger-st-ring" />}
      </svg>
      {tip && (
        <div className="ledger-tip" style={{ left: ((tip.c[0] - vx) / vw) * 100 + '%', top: ((tip.c[1] - vy) / vh) * 100 + '%' }}>
          <b>{short(NAME[tipKey])}</b>{rm(values[NAME[tipKey]])} expected, {WIN[horizon]}
        </div>
      )}
      <div className="ledger-maplab ledger-maplab-pen" aria-hidden="true">Peninsular Malaysia</div>
      <div className="ledger-maplab ledger-maplab-bor" aria-hidden="true">Borneo</div>
    </div>
  )
}
export function MapLegend() {
  return (
    <div className="ledger-legend" aria-label="Map colour key">
      {RAMP.map((c, i) => <span key={c}><i style={{ background: c }} />{BIN_LABELS[i]}</span>)}
    </div>
  )
}

/* ---------- 100% sector bar ---------- */
export function SectorBar({ by }) {
  const tot = SECTORS.reduce((a, s) => a + (by[s.k] || 0), 0) || 1
  return (
    <div className="ledger-sbar" role="img"
      aria-label={SECTORS.map(s => `${s.label} ${Math.round(((by[s.k] || 0) / tot) * 100)}%`).join(', ')}>
      {SECTORS.map(s => (by[s.k] || 0) / tot > 0.004 && (
        <i key={s.k} style={{ flexGrow: by[s.k] / tot, background: s.c }} title={`${s.label}: ${rm(by[s.k])}`} />
      ))}
    </div>
  )
}

/* ---------- sparkline (one state's daily damage) ---------- */
export function Spark({ data, dates, height = 44 }) {
  const [ref, w] = useWidth(300)
  const max = Math.max(...data, 1)
  const n = data.length
  const bw = w / n
  const peak = data.indexOf(max)
  return (
    <div ref={ref} className="ledger-spark">
      <svg width={w} height={height} role="img" aria-label={`Daily damage, ${dlong(dates[0])} to ${dlong(dates[n - 1])}; peak ${rm(max)} on ${dlong(dates[peak])}`}>
        {data.map((v, i) => {
          const h = Math.max(v > 0 ? 1 : 0, (v / max) * (height - 4))
          return <rect key={i} x={i * bw + 0.5} y={height - h} width={Math.max(1, bw - 1)} height={h} rx={Math.min(1.5, bw / 3)}
            fill={i === peak ? 'var(--ink)' : '#c9c9c9'} />
        })}
      </svg>
      <div className="ledger-spark-ax mono"><span>{dshort(dates[0])}</span><span>peak {rm(max)}, {dshort(dates[peak])}</span><span>{dshort(dates[n - 1])}</span></div>
    </div>
  )
}

/* ---------- damage per day: episode so far + the next 72 h ---------- */
export function DailyChart({ daily, fc, horizon, bench }) {
  const [ref, w] = useWidth(1000)
  const [hov, setHov] = useState(null)
  const n = daily.dates.length
  const H = ['24', '48', '72']
  const ml = 54, mr = 10, mt = 14, top = 190, gap = 26, pmh = 64, mb = 22
  const pw = w - ml - mr
  const F = Math.max(96, Math.min(150, pw * 0.12))   // forecast region width
  const band = (pw - F) / (n + 0.5)
  const fband = F / H.length
  const bw = Math.max(1, band * (band > 8 ? 0.74 : 0.86))
  const totalH = mt + top + gap + pmh + mb
  const fmax = Math.max(...H.map(h => fc.worst.by_horizon_rm[h] || 0))
  const yMax = niceMax(Math.max(...daily.total_rm, fmax, bench ? bench[1] : 0) * 1.05)
  const y = v => mt + top - (v / yMax) * top
  const pmMax = Math.max(60, Math.ceil(Math.max(...daily.pm25_popweighted) / 20) * 20)
  const py = v => mt + top + gap + pmh - (v / pmMax) * pmh
  const xc = i => ml + band * (i + 0.5)
  const divX = ml + band * (n + 0.25)
  const fx = j => divX + fband * (j + 0.5)
  const ticks = [0, yMax / 2, yMax]
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((pw - F) / 64))))
  const pmPath = daily.pm25_popweighted.map((v, i) => `${i ? 'L' : 'M'}${xc(i).toFixed(1)},${py(v).toFixed(1)}`).join('')

  const onMove = e => {
    const r = e.currentTarget.getBoundingClientRect()
    const px = e.clientX - r.left
    const i = Math.floor((px - ml) / band)
    const j = Math.floor((px - divX) / fband)
    if (px < divX && i >= 0 && i < n) setHov({ t: 'd', i })
    else if (px >= divX && j >= 0 && j < H.length) setHov({ t: 'f', j })
    else setHov(null)
  }
  let tip = null
  if (hov && hov.t === 'd') {
    const i = hov.i
    tip = (
      <div className="ledger-tip ledger-tip-c" style={{ left: Math.min(Math.max(xc(i), 120), w - 120), top: y(daily.total_rm[i]) - 6 }}>
        <b>{dlong(daily.dates[i])}</b>
        <span className="ledger-tip-row tot"><span>Total</span><span>{rm(daily.total_rm[i])}</span></span>
        {SECTORS.map(s => (
          <span className="ledger-tip-row" key={s.k}><span><i style={{ background: s.c }} />{s.label}</span><span>{rm(daily.by_sector_rm[s.k][i])}</span></span>
        ))}
        <span className="ledger-tip-row"><span>PM2.5 (people-weighted)</span><span>{daily.pm25_popweighted[i]} µg/m³</span></span>
        <span className="ledger-tip-row"><span>States over API 100</span><span>{daily.states_over_api100[i]} of 16</span></span>
      </div>
    )
  } else if (hov && hov.t === 'f') {
    const h = H[hov.j]
    tip = (
      <div className="ledger-tip ledger-tip-c" style={{ left: Math.min(fx(hov.j), w - 120), top: y(fc.worst.by_horizon_rm[h] || 0) - 6 }}>
        <b>Forecast, {WIN[h]}</b>
        {[['worst', 'Worst case'], ['expected', 'Expected'], ['best', 'Best case']].map(([k, l]) => (
          <span className="ledger-tip-row" key={k}><span>{l}</span><span>{fc[k].by_horizon_rm[h] != null ? rm(fc[k].by_horizon_rm[h]) : 'haze cleared'}</span></span>
        ))}
      </div>
    )
  }

  return (
    <div ref={ref} className="ledger-daily">
      <svg width={w} height={totalH} role="img" onMouseMove={onMove} onMouseLeave={() => setHov(null)}
        aria-label={`Damage per day from ${dlong(daily.dates[0])} to ${dlong(daily.dates[n - 1])}, peak ${rm(Math.max(...daily.total_rm))}; then the next 72 hours, expected ${rm(fc.expected.by_horizon_rm['24'])} a day. Full figures in the table below.`}>
        <defs>
          <pattern id="ledger-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="5" height="5" fill="#fff" /><line x1="0" y1="0" x2="0" y2="5" stroke="#9a9a9a" strokeWidth="2" />
          </pattern>
        </defs>
        {bench && (
          <g>
            <rect x={ml} width={pw} y={y(bench[1])} height={y(bench[0]) - y(bench[1])} fill="#f3f3f3" />
            <text x={ml + 6} y={y(bench[1]) + 13} className="ledger-ax-t ledger-halo">Published 2026 estimate: RM 20–50 m a day</text>
          </g>
        )}
        {ticks.map(t => (
          <g key={t}>
            <line x1={ml} x2={w - mr} y1={y(t)} y2={y(t)} stroke={t ? '#ececec' : '#bdbdbd'} />
            <text x={ml - 8} y={y(t) + 4} textAnchor="end" className="ledger-ax-t mono">{t ? rm(t) : 'RM 0'}</text>
          </g>
        ))}
        {daily.dates.map((d, i) => {
          let acc = 0
          const dim = hov && !(hov.t === 'd' && hov.i === i)
          return (
            <g key={d} opacity={dim ? 0.55 : 1}>
              {SECTORS.map(s => {
                const v = daily.by_sector_rm[s.k][i]
                if (!(v > 0)) return null
                const y0 = y(acc), y1 = y(acc + v)
                acc += v
                return <rect key={s.k} x={xc(i) - bw / 2} y={y1} width={bw} height={Math.max(0, y0 - y1)} fill={s.c}
                  stroke="#fff" strokeWidth={bw > 4 ? 0.75 : 0} />
              })}
            </g>
          )
        })}
        {/* today / forecast divider */}
        <line x1={divX} x2={divX} y1={mt - 4} y2={mt + top + gap + pmh} stroke="#9a9a9a" strokeDasharray="3 3" />
        <text x={divX - 5} y={mt + 4} textAnchor="end" className="ledger-ax-t">So far</text>
        <text x={w - mr} y={mt + 4} textAnchor="end" className="ledger-ax-t">Next 72 h</text>
        {H.map((h, j) => {
          const e = fc.expected.by_horizon_rm[h] || 0
          const lo = fc.best.by_horizon_rm[h] || 0
          const hi = fc.worst.by_horizon_rm[h] || 0
          const on = h === horizon
          const fw = Math.min(fband * 0.6, 22)
          return (
            <g key={h} opacity={hov && !(hov.t === 'f' && hov.j === j) ? 0.6 : 1}>
              <rect x={fx(j) - fw / 2} y={y(e)} width={fw} height={y(0) - y(e)} fill="url(#ledger-hatch)" stroke={on ? 'var(--ink)' : '#8a8a8a'} strokeWidth={on ? 1.5 : 1} />
              <line x1={fx(j)} x2={fx(j)} y1={y(hi)} y2={y(lo)} stroke="var(--ink)" strokeWidth="1.5" />
              <line x1={fx(j) - 4} x2={fx(j) + 4} y1={y(hi)} y2={y(hi)} stroke="var(--ink)" strokeWidth="1.5" />
              <line x1={fx(j) - 4} x2={fx(j) + 4} y1={y(lo)} y2={y(lo)} stroke="var(--ink)" strokeWidth="1.5" />
            </g>
          )
        })}
        {/* PM2.5 small multiple, own axis */}
        <text x={ml} y={mt + top + gap - 6} className="ledger-ax-t">PM2.5, people-weighted national average (µg/m³)</text>
        <line x1={ml} x2={ml + band * n} y1={py(35)} y2={py(35)} stroke="var(--red)" strokeDasharray="4 3" strokeWidth="1" />
        <text x={ml - 8} y={py(35) + 4} textAnchor="end" className="ledger-ax-t mono" fill="var(--red)">35</text>
        <text x={ml - 8} y={py(pmMax) + 8} textAnchor="end" className="ledger-ax-t mono">{pmMax}</text>
        <line x1={ml} x2={ml + band * n} y1={py(0)} y2={py(0)} stroke="#bdbdbd" />
        <path d={pmPath} fill="none" stroke="var(--ink)" strokeWidth="1.75" strokeLinejoin="round" />
        {hov && hov.t === 'd' && (
          <>
            <line x1={xc(hov.i)} x2={xc(hov.i)} y1={mt} y2={mt + top + gap + pmh} stroke="var(--ink)" strokeOpacity=".25" />
            <circle cx={xc(hov.i)} cy={py(daily.pm25_popweighted[hov.i])} r="4" fill="var(--ink)" stroke="#fff" strokeWidth="2" />
          </>
        )}
        {/* x labels */}
        {daily.dates.map((d, i) => i % every === 0 && (
          <text key={d} x={xc(i)} y={totalH - 6} textAnchor="middle" className="ledger-ax-t mono">{dshort(d)}</text>
        ))}
        {H.map((h, j) => (
          <text key={h} x={fx(j)} y={totalH - 6} textAnchor="middle" className={`ledger-ax-t mono${h === horizon ? ' on' : ''}`}>{h}h</text>
        ))}
      </svg>
      {tip}
    </div>
  )
}

/* ---------- sensitivity tornado ---------- */
export function Tornado({ rows, central, label }) {
  const lo = Math.min(...rows.map(r => r.low_rm), central)
  const hi = Math.max(...rows.map(r => r.high_rm), central)
  const pad = (hi - lo) * 0.04
  const x = v => ((v - (lo - pad)) / (hi - lo + 2 * pad)) * 100
  return (
    <div className="ledger-torn">
      {rows.map(r => (
        <div className="ledger-torn-r" key={r.coefficient}>
          <span className="ledger-torn-n">{label(r.coefficient)}</span>
          <span className="ledger-torn-t" aria-label={`${rm(r.low_rm)} at its low end, ${rm(r.high_rm)} at its high end`}>
            <i className="ledger-torn-c" style={{ left: x(central) + '%' }} />
            <i className="ledger-torn-b lo" style={{ left: x(Math.min(r.low_rm, central)) + '%', width: (x(central) - x(Math.min(r.low_rm, central))) + '%' }} />
            <i className="ledger-torn-b hi" style={{ left: x(central) + '%', width: (x(Math.max(r.high_rm, central)) - x(central)) + '%' }} />
            <em className="mono" style={{ right: (100 - x(r.low_rm)) + '%' }}>{rm(r.low_rm, { unit: false })}</em>
            <em className="mono hi" style={{ left: x(r.high_rm) + '%' }}>{rm(r.high_rm, { unit: false })}</em>
          </span>
        </div>
      ))}
    </div>
  )
}

export { niceMax }

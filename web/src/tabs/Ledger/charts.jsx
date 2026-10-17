// Hand-written SVG/CSS charts for the Cross-border ledger tab.
import { useLayoutEffect, useRef, useState } from 'react'
import { rm } from './format.js'

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

export { niceMax }

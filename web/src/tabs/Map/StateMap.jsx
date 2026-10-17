import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { MY_GEO, MY_VB } from '../Routing/geo.js'
import { DRAW_ORDER, NAMES, SMALL, arrivalFor, fmtV, REDUCE } from './scales.js'

export const VIEWS = [
  { k: 'my', label: 'Malaysia', vb: MY_VB },
  { k: 'pen', label: 'Peninsula', vb: [-12, 58, 488, 562] },
  { k: 'klang', label: 'Klang Valley', vb: [108, 344, 214, 162] },
]
// Nudge a few value labels off their centroid so they don't sit on the tiny territories.
const NUDGE = { sl: [-16, -22], ns: [8, 6], la: [16, -10], pg: [-14, 0], me: [4, 4] }

// Animate the viewBox between zoom presets.
function useTween(target) {
  const [vb, setVb] = useState(target)
  const cur = useRef(target)
  useEffect(() => {
    const from = cur.current, to = target
    if (REDUCE || from.every((v, i) => v === to[i])) { cur.current = to; setVb(to); return }
    let raf, t0
    const step = t => {
      t0 = t0 || t
      const p = Math.min(1, (t - t0) / 520), e = 1 - Math.pow(1 - p, 3)
      const v = from.map((f, i) => f + (to[i] - f) * e)
      cur.current = v; setVb(v)
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target.join(',')])
  return vb
}

function useSize(ref) {
  const [s, setS] = useState({ w: 0, h: 0 })
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setS({ w: e.contentRect.width, h: e.contentRect.height }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return s
}

export default function StateMap({ rows, scale, horizon, sel, onSelect, view, dateLabel }) {
  const wrap = useRef(null)
  const { w, h } = useSize(wrap)
  const vb = useTween(VIEWS.find(v => v.k === view).vb)
  const [hover, setHover] = useState(null)
  // pixels per viewBox unit (preserveAspectRatio meet)
  const k = w && h ? Math.min(w / vb[2], h / vb[3]) : 1
  const px = n => n / k
  const byKey = Object.fromEntries(rows.map(r => [r.key, r]))

  const tipFor = key => {
    const r = byKey[key]
    if (!r) return null
    const g = MY_GEO[key]
    const ox = (w - vb[2] * k) / 2, oy = (h - vb[3] * k) / 2
    return { r, x: ox + (g.c[0] - vb[0]) * k, y: oy + (g.b[1] - vb[1]) * k }
  }
  const tip = hover && tipFor(hover)

  const label = r => {
    const v = r.v[horizon], b = scale.bands[scale.band(v)]
    return `${r.name}: ${fmtV(v, scale)} ${scale.unit} at +${horizon} h, ${b ? b.label.toLowerCase() : 'no data'}. Haze ${scale.id === 'api' ? 'unhealthy' : 'above standard'}: ${arrivalFor(r.arrival, scale, true).toLowerCase()}.`
  }

  return (
    <div className="map-canvas" ref={wrap} onMouseLeave={() => setHover(null)}>
      <svg viewBox={vb.join(' ')} preserveAspectRatio="xMidYMid meet" role="group" aria-label={`Map of Malaysia coloured by forecast ${scale.unitLong} at +${horizon} hours, ${dateLabel}. Select a state for its forecast.`}>
        {DRAW_ORDER.map(key => {
          const g = MY_GEO[key], r = byKey[key]
          const v = r && r.v[horizon], b = scale.band(v)
          const fill = b == null ? '#eee' : scale.bands[b].c
          return (
            <g key={key}>
              <path d={g.d} className={'map-st' + (sel === key ? ' on' : '')} fill={fill} tabIndex={0} role="button"
                aria-pressed={sel === key} aria-label={r ? label(r) : NAMES[key]}
                onClick={() => onSelect(key)} onMouseEnter={() => setHover(key)} onFocus={() => setHover(key)} onBlur={() => setHover(null)}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(key) } }} />
              {SMALL.has(key) && g.r * k < 8 && (
                <circle cx={g.c[0]} cy={g.c[1]} r={px(5.5)} fill={fill} stroke={sel === key ? '#111' : '#fff'} strokeWidth={px(sel === key ? 2.5 : 1.5)} pointerEvents="none" />
              )}
            </g>
          )
        })}
        {sel && MY_GEO[sel] && <path d={MY_GEO[sel].d} className="map-selring" pointerEvents="none" />}
        {DRAW_ORDER.map(key => {
          const g = MY_GEO[key], r = byKey[key]
          if (!r) return null
          const v = r.v[horizon], b = scale.band(v)
          const small = SMALL.has(key)
          const rp = g.r * k
          if (!small && rp < 9) return null
          if (small && key !== 'la' && rp < 5) return null // KL/Putrajaya: labelled only when zoomed in
          const [nx, ny] = small ? [0, 0] : (rp > 60 ? [0, 0] : NUDGE[key] || [0, 0])
          let x = g.c[0] + nx, y = g.c[1] + ny
          let ink = b == null ? '#111' : scale.bands[b].ink
          let anchor = 'middle'
          let halo = false
          if (key === 'la' && rp < 14) { x = g.c[0] + px(10); y = g.c[1] - px(10); ink = '#111'; anchor = 'start'; halo = true }
          if (small && key !== 'la') { x = g.c[0] + g.r + px(8); y = g.c[1] - px(6); ink = '#111'; anchor = 'start'; halo = true }
          const showName = rp > 34 || view === 'klang'
          return (
            <g key={'l' + key} pointerEvents="none" className={'map-lbl' + (halo ? ' halo' : '')}>
              <text x={x} y={y} textAnchor={anchor} dominantBaseline="central" fill={ink}
                style={{ fontSize: px(rp > 40 ? 13 : 11.5), fontWeight: 700 }}>{fmtV(v, scale)}</text>
              {showName && <text x={x} y={y + px(13)} textAnchor={anchor} dominantBaseline="central" fill={ink} opacity=".82"
                style={{ fontSize: px(10.5), fontWeight: 500 }}>{key === 'la' && rp < 14 ? 'Labuan' : NAMES[key]}</text>}
            </g>
          )
        })}
      </svg>
      {tip && w > 0 && (
        <div className="map-tip" style={{ left: Math.max(90, Math.min(w - 90, tip.x)), top: Math.max(56, tip.y) }} role="presentation">
          <b>{tip.r.name}</b>
          <span><i style={{ background: (scale.bands[scale.band(tip.r.v[horizon])] || {}).c }} />{fmtV(tip.r.v[horizon], scale)} {scale.unit} at +{horizon} h · {(scale.bands[scale.band(tip.r.v[horizon])] || {}).label}</span>
          <span>{scale.id === 'api' ? 'Unhealthy' : 'Above standard'}: {arrivalFor(tip.r.arrival, scale, true).replace('Already unhealthy today', 'already, today')}</span>
        </div>
      )}
    </div>
  )
}

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { MY_GEO, MY_VB } from '../Routing/geo.js'
import { DRAW_ORDER, NAMES, SMALL, arrivalFor, fmtV, REDUCE } from './scales.js'

export const VIEWS = [
  { k: 'my', label: 'Malaysia', vb: MY_VB },
  { k: 'pen', label: 'Peninsula', vb: [-12, 58, 488, 562] },
  { k: 'borneo', label: 'Borneo', vb: [574, -10, 960, 645] }, // Sabah, Sarawak, Labuan
]
// Nudge a few value labels off their centroid so they don't sit on the tiny territories.

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

// Grow a preset viewBox to the canvas's aspect ratio, keeping it inside the map's extent where it fits,
// so a tall preset (Borneo) on a wide canvas fills the spare width with map rather than empty sea.
function fitView(vb, w, h) {
  if (!w || !h) return vb
  let [x, y, vw, vh] = vb
  const [X, Y, W, H] = MY_VB
  const clamp = (v, lo, span, size) => (size <= span ? Math.min(Math.max(v, lo), lo + span - size) : lo + (span - size) / 2)
  if (vw / vh < w / h) {
    const nw = vh * (w / h)
    x = clamp(x - (nw - vw) / 2, X, W, nw); vw = nw
  } else {
    const nh = vw / (w / h)
    y = clamp(y - (nh - vh) / 2, Y, H, nh); vh = nh
  }
  return [x, y, vw, vh]
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
  const vb = fitView(useTween(VIEWS.find(v => v.k === view).vb), w, h)
  // pixels per viewBox unit (preserveAspectRatio meet)
  const k = w && h ? Math.min(w / vb[2], h / vb[3]) : 1
  const px = n => n / k
  const byKey = Object.fromEntries(rows.map(r => [r.key, r]))

  const now = horizon === 'now'
  const when = now ? 'measured now' : `at +${horizon} h`
  const label = r => {
    const v = r.v[horizon], b = scale.bands[scale.band(v)]
    return `${r.name}: ${scale.id === 'pm' ? `${fmtV(v, scale)} ${scale.unit}` : `${scale.unit} ${fmtV(v, scale)}`} ${when}, ${b ? b.label.toLowerCase() : 'no data'}. Haze ${scale.id === 'pm' ? 'above standard' : 'unhealthy'}: ${arrivalFor(r.arrival, scale, true).toLowerCase()}.`
  }

  return (
    <div className="map-canvas" ref={wrap}>
      <svg viewBox={vb.join(' ')} preserveAspectRatio="xMidYMid meet" role="group" aria-label={`Map of Malaysia coloured by ${now ? 'measured Air Pollutant Index now' : `forecast ${scale.unitLong} at +${horizon} hours, ${dateLabel}`}. Select a state for its forecast.`}>
        {DRAW_ORDER.map(key => {
          const g = MY_GEO[key], r = byKey[key]
          const v = r && r.v[horizon], b = scale.band(v)
          const fill = b == null ? '#eee' : scale.bands[b].c
          return (
            <g key={key}>
              <path d={g.d} className={'map-st' + (sel === key ? ' on' : '')} fill={fill} tabIndex={0} role="button"
                aria-pressed={sel === key} aria-label={r ? label(r) : NAMES[key]}
                onClick={() => onSelect(key)}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(key) } }} />
              {SMALL.has(key) && g.r * k < 8 && (
                <circle cx={g.c[0]} cy={g.c[1]} r={px(5.5)} fill={fill} stroke={sel === key ? '#111' : '#fff'} strokeWidth={px(sel === key ? 1 : 1.5)} pointerEvents="none" />
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
          let x = g.c[0], y = g.c[1]
          let ink = b == null ? '#111' : scale.bands[b].ink
          let anchor = 'middle'
          let halo = false
          if (key === 'la' && rp < 14) { x = g.c[0] + px(10); y = g.c[1] - px(10); ink = '#111'; anchor = 'start'; halo = true }
          if (small && key !== 'la') { x = g.c[0] + g.r + px(8); y = g.c[1] - px(6); ink = '#111'; anchor = 'start'; halo = true }
          const showName = rp > 34
          if (showName && !halo) y -= px(6.5) // centre the number + name pair on the state
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
    </div>
  )
}

import { useLayoutEffect, useRef, useState } from 'react'
import { fmtV } from './scales.js'

// Small line chart: one x slot per point, one y axis (the scale's unit).
// series: [{ id, label, cls, pts: [{i, v}], dash?, dots?: 'solid'|'hollow' }]
// slots: [{ label, sub? }]; marker: slot index of "forecast made"
export default function ForecastChart({ slots, series, scale, marker, ariaLabel, height = 150 }) {
  const ref = useRef(null)
  const [w, setW] = useState(320)
  const [hi, setHi] = useState(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(200, e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const H = height, L = 30, R = 16, T = 10, B = 22
  const all = series.flatMap(s => s.pts.map(p => p.v)).filter(v => v != null)
  const step = scale.id === 'api' ? 50 : 10
  const top = Math.max(scale.line * 1.25, ...all) * 1.08
  const yMax = Math.ceil(top / step) * step
  const n = slots.length
  const x = i => L + (n === 1 ? (w - L - R) / 2 : (i * (w - L - R)) / (n - 1))
  const y = v => T + (1 - v / yMax) * (H - T - B)
  const ticks = []
  for (let t = 0; t <= yMax; t += yMax / step > 5 ? step * 2 : step) ticks.push(t)

  const pathOf = pts => pts.filter(p => p.v != null).map((p, j) => `${j ? 'L' : 'M'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join('')
  const onMove = e => {
    const r = ref.current.getBoundingClientRect()
    const i = Math.round(((e.clientX - r.left - L) / (w - L - R)) * (n - 1))
    setHi(Math.max(0, Math.min(n - 1, i)))
  }
  const hiVals = hi == null ? [] : series.map(s => ({ s, p: s.pts.find(p => p.i === hi) })).filter(o => o.p && o.p.v != null)
  const tipLeft = hi == null ? 0 : Math.max(70, Math.min(w - 70, x(hi)))

  return (
    <div className="map-chart" ref={ref} onMouseMove={onMove} onMouseLeave={() => setHi(null)}>
      <svg width={w} height={H} role="img" aria-label={ariaLabel}>
        <rect x={L} y={T} width={w - L - R} height={Math.max(0, y(scale.line) - T)} className="map-ch-bad" />
        {ticks.map(t => (
          <g key={t}>
            <line x1={L} x2={w - R} y1={y(t)} y2={y(t)} className="map-ch-grid" />
            <text x={L - 6} y={y(t)} className="map-ch-tick" textAnchor="end" dominantBaseline="central">{t}</text>
          </g>
        ))}
        <line x1={L} x2={w - R} y1={y(scale.line)} y2={y(scale.line)} className="map-ch-line" />
        <text x={L + 4} y={y(scale.line) - 5} className="map-ch-linelbl" textAnchor="start">{scale.lineLabel}</text>
        {marker != null && <>
          <line x1={x(marker)} x2={x(marker)} y1={T} y2={H - B} className="map-ch-mark" />
          <text x={x(marker) - 4} y={T + 9} className="map-ch-tick" textAnchor="end">Forecast made</text>
        </>}
        {slots.map((s, i) => (s.label ? <text key={i} x={x(i)} y={H - 6} className={'map-ch-tick' + (s.strong ? ' b' : '')} textAnchor={i === 0 && n > 1 ? 'start' : 'middle'}>{s.label}</text> : null))}
        {hi != null && <line x1={x(hi)} x2={x(hi)} y1={T} y2={H - B} className="map-ch-cross" />}
        {series.map(s => (
          <g key={s.id} className={'map-s-' + s.cls}>
            {s.line !== false && <path d={pathOf(s.pts)} className="ln" />}
            {s.pts.filter(p => p.v != null && (s.dots || p.i === hi)).map(p => (
              <circle key={p.i} cx={x(p.i)} cy={y(p.v)} r={s.dots === 'hollow' ? 4 : 3.5} className={s.dots === 'hollow' ? 'hol' : 'dot'} />
            ))}
          </g>
        ))}
      </svg>
      {hi != null && hiVals.length > 0 && (
        <div className="map-ch-tip" style={{ left: tipLeft }}>
          <b>{slots[hi].tip || slots[hi].label}</b>
          {hiVals.map(({ s, p }) => <span key={s.id}><i className={'sw map-s-' + s.cls} />{s.label} <em className="num">{fmtV(p.v, scale)}</em></span>)}
        </div>
      )}
    </div>
  )
}

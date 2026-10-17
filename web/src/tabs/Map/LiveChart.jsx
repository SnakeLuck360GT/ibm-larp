import { useLayoutEffect, useRef, useState } from 'react'
import { fmtV } from './scales.js'

const HS = ['24', '48', '72']

// The live 72-hour CAMS curve for one state: hourly PM2.5, the three 24-h windows the map is coloured by
// (with each window's mean), the 35 µg/m³ standard and a "now" marker. Hover for the hourly value.
export default function LiveChart({ hourly, means, scale, horizon, issuedAt, setHorizon, name, height = 150, measured }) {
  const ref = useRef(null)
  const [w, setW] = useState(320)
  const [hi, setHi] = useState(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(220, e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const H = height, L = 30, R = 22, T = 16, B = 20
  const n = hourly.length
  const t0 = Date.parse(issuedAt)
  const vals = hourly.filter(v => v != null)
  const top = Math.max(scale.line * 1.3, ...vals, measured ? measured.v : 0) * 1.06
  const step = top > 120 ? 50 : top > 60 ? 20 : 10
  const yMax = Math.ceil(top / step) * step
  const x = hr => L + (hr / 72) * (w - L - R)
  const y = v => T + (1 - v / yMax) * (H - T - B)
  const ticks = []
  for (let t = 0; t <= yMax; t += step) ticks.push(t)
  const path = hourly.map((v, i) => (v == null ? '' : `${i && hourly[i - 1] != null ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`)).join('')
  const nowH = (Date.now() - t0) / 3600e3
  const showNow = Number.isFinite(nowH) && nowH >= 0.5 && nowH <= 71
  const at = hr => {
    try {
      return new Date(t0 + hr * 3600e3).toLocaleString('en-GB', { timeZone: 'Asia/Kuala_Lumpur', weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
    } catch (e) { return `+${hr} h` }
  }
  const onMove = e => {
    const r = ref.current.getBoundingClientRect()
    const i = Math.round(((e.clientX - r.left - L) / (w - L - R)) * 72)
    setHi(i < 0 || i > n - 1 ? null : i)
  }
  const peak = vals.length ? Math.max(...vals) : null
  const peakI = hourly.indexOf(peak)
  const aria = `${name}: hourly PM2.5 forecast for the next 72 hours, from ${fmtV(hourly[0], scale)} now to ${fmtV(hourly[n - 1], scale)} µg/m³; peak ${fmtV(peak, scale)} at +${peakI} h. ` +
    HS.map(h => `${+h - 24} to ${h} h mean ${fmtV(means[h], scale)}`).join(', ') + '.' + (measured ? ` Measured now about ${fmtV(measured.v, scale)} µg/m³.` : '')

  return (
    <div className="map-chart map-lc" ref={ref} onMouseMove={onMove} onMouseLeave={() => setHi(null)}>
      <svg width={w} height={H} role="img" aria-label={aria}>
        {HS.map(h => (
          <rect key={h} x={x(+h - 24)} y={T} width={x(+h) - x(+h - 24)} height={H - T - B}
            className={'map-lc-win' + (h === horizon ? ' on' : '')} onClick={() => setHorizon && setHorizon(h)} />
        ))}
        <rect x={L} y={T} width={w - L - R} height={Math.max(0, y(scale.line) - T)} className="map-ch-bad" pointerEvents="none" />
        {ticks.map(t => (
          <g key={t} pointerEvents="none">
            <line x1={L} x2={w - R} y1={y(t)} y2={y(t)} className="map-ch-grid" />
            <text x={L - 6} y={y(t)} className="map-ch-tick" textAnchor="end" dominantBaseline="central">{t}</text>
          </g>
        ))}
        {[24, 48].map(hr => <line key={hr} x1={x(hr)} x2={x(hr)} y1={T} y2={H - B} className="map-lc-sep" pointerEvents="none" />)}
        <line x1={L} x2={w - R} y1={y(scale.line)} y2={y(scale.line)} className="map-ch-line" pointerEvents="none" />
        <text x={w - R + 4} y={y(scale.line)} className="map-ch-linelbl" textAnchor="start" dominantBaseline="central" pointerEvents="none">35</text>
        {HS.map(h => (
          <text key={h} x={(x(+h - 24) + x(+h)) / 2} y={T - 5} textAnchor="middle" className={'map-ch-tick' + (h === horizon ? ' b' : '')} pointerEvents="none">
            {(x(24) - x(0) >= 108 ? `${+h - 24}–${h} h · avg ` : 'avg ') + fmtV(means[h], scale)}
          </text>
        ))}
        {HS.map(h => means[h] != null && (
          <line key={h} x1={x(+h - 24) + 2} x2={x(+h) - 2} y1={y(means[h])} y2={y(means[h])} className={'map-lc-mean' + (h === horizon ? ' on' : '')} pointerEvents="none" />
        ))}
        <path d={path} className="map-lc-ln" pointerEvents="none" />
        {[0, 24, 48, 72].map(hr => (
          <text key={hr} x={x(hr)} y={H - 6} className={'map-ch-tick' + (String(hr) === horizon ? ' b' : '')} textAnchor={hr === 0 ? 'start' : hr === 72 ? 'end' : 'middle'} pointerEvents="none">
            {hr === 0 ? 'Fetched' : `+${hr} h`}
          </text>
        ))}
        {showNow && <g pointerEvents="none">
          <line x1={x(nowH)} x2={x(nowH)} y1={T} y2={H - B} className="map-lc-now" />
          <text x={x(nowH) + 3} y={H - B - 4} className="map-lc-nowlbl">Now</text>
        </g>}
        {measured && <g pointerEvents="none" className="map-lc-meas">
          <circle cx={x(measured.h)} cy={y(measured.v)} r={5} />
          <text x={x(measured.h) + 8} y={y(measured.v)} dominantBaseline="central" textAnchor="start">Measured ≈{fmtV(measured.v, scale)}</text>
        </g>}
        {hi != null && hourly[hi] != null && <g pointerEvents="none">
          <line x1={x(hi)} x2={x(hi)} y1={T} y2={H - B} className="map-ch-cross" />
          <circle cx={x(hi)} cy={y(hourly[hi])} r={3.5} className="map-lc-dot" />
        </g>}
      </svg>
      {hi != null && hourly[hi] != null && (
        <div className="map-ch-tip" style={{ left: Math.max(80, Math.min(w - 80, x(hi))), top: T - 2 }}>
          <b>{at(hi)} MYT · +{hi} h</b>
          <span>Hourly PM2.5 <em className="num">{fmtV(hourly[hi], scale)} µg/m³</em></span>
          <span className="map-tip-sub">{hourly[hi] > scale.line ? 'Above' : 'Below'} 35 (hourly)</span>
        </div>
      )}
    </div>
  )
}

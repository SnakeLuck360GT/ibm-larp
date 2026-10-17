import { useLayoutEffect, useRef } from 'react'
import { ARR_RANK, REDUCE, arrivalFor, fmtV } from './scales.js'

const HS = ['24', '48', '72']
const SHORT = { ns: 'N. Sembilan', pg: 'Penang' } // keeps the name column narrow

/* Forecast at a glance: every state x every horizon, coloured by the current mode's bands. */
export default function Matrix({ rows, scale, horizon, setHorizon, sel, onSelect, sort, setSort, dateOf, live, onNow }) {
  const peak = r => Math.max(...HS.map(h => r.v[h] ?? -1))
  const sorted = [...rows].sort((a, b) => sort === 'arrival'
    ? ARR_RANK(a.arrival) - ARR_RANK(b.arrival) || (b.v[horizon] ?? -1) - (a.v[horizon] ?? -1)
    : (b.v[horizon] ?? -1) - (a.v[horizon] ?? -1) || peak(b) - peak(a))
  const list = useRef(null)
  const prev = useRef({})
  // FLIP: rows glide to their new rank when the day, horizon or sort changes.
  useLayoutEffect(() => {
    const el = list.current
    if (!el) return
    const now = {}
    el.querySelectorAll('[data-k]').forEach(li => { now[li.dataset.k] = li.offsetTop })
    if (!REDUCE) {
      el.querySelectorAll('[data-k]').forEach(li => {
        const p = prev.current[li.dataset.k]
        if (p == null || p === now[li.dataset.k]) return
        li.animate([{ transform: `translateY(${p - now[li.dataset.k]}px)` }, { transform: 'none' }], { duration: 420, easing: 'cubic-bezier(.2,.75,.2,1)' })
      })
    }
    prev.current = now
  })
  const hi = HS.indexOf(horizon)
  const count = h => rows.filter(r => scale.bad(r.v[h])).length

  return (
    <section className="card map-mx" aria-label="Forecast by state and horizon">
      <div className="map-mx-h">
        <div>
          <h3>Forecast by state</h3>
        </div>
        <div className="seg map-seg sm" role="group" aria-label="Sort states">
          <button type="button" aria-pressed={sort !== 'arrival'} onClick={() => setSort('worst')}>Worst</button>
          <button type="button" aria-pressed={sort === 'arrival'} onClick={() => setSort('arrival')}>Arrival</button>
        </div>
      </div>
      <p className="map-mx-sub hint">{live
        ? <>µg/m³ · Now: measured · +h: CAMS 24-h mean</>
        : <>API · Arrives: first day at 101+</>}</p>
      <div className={'map-mx-grid' + (live ? ' has-now' : '')} style={{ '--i': hi }}>
        <div className="map-mx-hl" aria-hidden="true" />
        <div className="map-mx-row map-mx-head">
          <span className="map-mx-sn">State</span>
          {live && (
            <button type="button" className={'map-mx-ch meas' + (horizon === 'now' ? ' on' : '')} aria-pressed={horizon === 'now'} disabled={!onNow}
              onClick={() => onNow && onNow()} title="Measured now at stations (WAQI). ≈ converted from US AQI."
              aria-label={`Show measured PM2.5 now on the map: ${count('now')} states above 35 µg/m³`}>
              <b>Now</b><small>≈ meas.</small>
            </button>
          )}
          {HS.map(h => (
            <button key={h} type="button" className={'map-mx-ch' + (h === horizon ? ' on' : '')} aria-pressed={h === horizon}
              onClick={() => setHorizon && setHorizon(h)} aria-label={`Show +${h} hours on the map: ${count(h)} states ${live ? 'above 35 µg/m³' : 'unhealthy'}`}>
              <b>+{h} h</b><small>{dateOf(+h)}</small>
            </button>
          ))}
          <span className="map-mx-ah">Arrives</span>
        </div>
        <ol className="map-mx-body" ref={list}>
          {sorted.map(r => (
            <li key={r.key} data-k={r.key}>
              <button type="button" className={'map-mx-row map-mx-r' + (r.key === sel ? ' on' : '')} aria-pressed={r.key === sel} onClick={() => onSelect(r.key)}
                aria-label={`${r.name}: ${live ? `measured now ${r.meas ? `about ${fmtV(r.v.now, scale)}` : 'unavailable'}, ` : ''}${HS.map(h => `+${h} h ${fmtV(r.v[h], scale)}`).join(', ')} ${scale.unit}. ${live ? 'Above 35' : 'Unhealthy'}: ${arrivalFor(r.arrival, scale, true)}.`}>
                <span className="map-mx-sn"><b title={r.name}>{SHORT[r.key] || r.name}</b></span>
                {live && (() => {
                  const v = r.v.now, b = scale.bands[scale.band(v)]
                  return <span className="map-mx-c meas num" title={r.meas ? `≈ converted from US AQI ${r.meas.pmAqi ?? r.meas.aqi} · ${r.meas.station}` : 'No station reading'}
                    style={{ background: b ? b.c : '#eee', color: b ? b.ink : '#111' }}>{fmtV(v, scale)}</span>
                })()}
                {HS.map(h => {
                  const v = r.v[h], b = scale.bands[scale.band(v)]
                  return <span key={h} className="map-mx-c num" style={{ background: b ? b.c : '#eee', color: b ? b.ink : '#111' }}>{fmtV(v, scale)}</span>
                })}
                <span className={'map-mx-a' + (r.arrival == null ? ' none' : (live ? r.arrival === 24 : r.arrival === 0) ? ' now' : '')}>{r.arrival == null ? '–' : arrivalFor(r.arrival, scale)}</span>
              </button>
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}

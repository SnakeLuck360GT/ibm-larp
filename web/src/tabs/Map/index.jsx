import { useEffect, useMemo, useRef, useState } from 'react'
import StateMap, { VIEWS } from './StateMap.jsx'
import ForecastChart from './ForecastChart.jsx'
import LiveChart from './LiveChart.jsx'
import Matrix from './Matrix.jsx'
import {
  API_SCALE, PM_SCALE, NAME2KEY, NAMES, REDUCE,
  addDays, arrivalFor, fmtDay, fmtMYT, fmtShort, fmtV,
} from './scales.js'
import './map.css'

const HS = ['24', '48', '72']
const SHORT = { ns: 'N. Sembilan', kl: 'KL', pg: 'Penang' }
const getJSON = url => fetch(url, { cache: 'no-store' }).then(r => (r.ok ? r.json() : Promise.reject(new Error(`${url}: ${r.status}`))))

function useData(baseUrl) {
  const [d, setD] = useState({ status: 'loading' })
  useEffect(() => {
    let live = true
    const opt = p => p.catch(() => null)
    Promise.all([
      getJSON(`${baseUrl}data/forecast_timeline.json`),
      opt(getJSON(`${baseUrl}data/forecast.json`)),
      opt(getJSON(`${baseUrl}data/forecast_metrics.json`)),
      opt(getJSON(`${baseUrl}data/states.json`)),
      opt(getJSON(`${baseUrl}data/forecast_live.json`)),
      opt(getJSON(`${baseUrl}data/waqi_now.json`)),
    ]).then(([timeline, replay, metrics, states, hourly, waqi]) => live && setD({ status: 'ok', timeline, replay, metrics, states, hourly, waqi }))
      .catch(e => live && setD({ status: 'error', error: String(e.message || e) }))
    return () => { live = false }
  }, [baseUrl])
  return d
}

// The replay day forecast.json was exported for (the team's REPLAY_DATE).
function replayDay(days, timeline, replay) {
  if (replay && replay.length) {
    const sig = rows => rows.map(r => `${r.zone}:${r.api_today}:${r.api['24']}`).sort().join('|')
    const want = sig(replay)
    const hit = days.find(d => sig(timeline[d]) === want)
    if (hit) return hit
  }
  return days[0]
}

// Live arrival: end hour of the first 24-h window whose mean is above 35 µg/m³ (24 = the next 24 h).
function arrivalLive(pm) {
  for (const h of HS) if (pm[h] != null && PM_SCALE.bad(pm[h])) return +h
  return null
}

// WAQI station reading for a state (null when the file or the state is missing).
const num = v => (typeof v === 'number' && Number.isFinite(v) ? v : null)
function waqiFor(waqi, key) {
  const st = waqi && waqi.states
  if (!st) return null
  const name = Object.keys(st).find(n => NAME2KEY[n] === key)
  const m = name && st[name]
  if (!m || m.error || num(m.pm25_ugm3) == null) return null
  return { ...m, pm: num(m.pm25_ugm3), aqi: num(m.aqi), pmAqi: num(m.pm25_aqi) }
}
function ago(iso) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  const min = Math.round((Date.now() - t) / 60e3)
  if (min < 1) return 'just now'
  if (min < 60) return `${min} min ago`
  if (min < 48 * 60) return `${Math.round(min / 60)} h ago`
  return `${Math.round(min / 1440)} d ago`
}
const fmtTimeMYT = iso => {
  try { return new Date(iso).toLocaleTimeString('en-GB', { timeZone: 'Asia/Kuala_Lumpur', hour: '2-digit', minute: '2-digit', hour12: false }) + ' MYT' } catch (e) { return '' }
}

export default function MapTab({ baseUrl = './', horizon = '24', setHorizon, stKey, select }) {
  const data = useData(baseUrl)
  const [mode, setMode] = useState('live')
  const [view, setView] = useState(() => (window.matchMedia && matchMedia('(max-width: 560px)').matches ? 'pen' : 'my'))
  const [dayIdx, setDayIdx] = useState(null)
  const [playing, setPlaying] = useState(false)
  const [sort, setSort] = useState('worst')
  const [layer, setLayer] = useState('fc') // live map colour: 'fc' = CAMS window, 'now' = WAQI measured
  useEffect(() => { setLayer('fc') }, [horizon])
  const sel = stKey || 'sl'
  const detailRef = useRef(null)

  const days = useMemo(() => (data.status === 'ok' ? Object.keys(data.timeline).sort() : []), [data])
  const defaultDay = useMemo(() => (days.length ? replayDay(days, data.timeline, data.replay) : null), [days])
  const di = dayIdx == null ? Math.max(0, days.indexOf(defaultDay)) : dayIdx
  const day = days[di]

  // Replay: step one day at a time through the Sep 2019 window.
  useEffect(() => {
    if (!playing) return
    const t = setInterval(() => {
      setDayIdx(i => {
        const cur = i == null ? di : i
        if (cur >= days.length - 1) { setPlaying(false); return cur }
        return cur + 1
      })
    }, REDUCE ? 1400 : 900)
    return () => clearInterval(t)
  }, [playing, days.length])

  // Live needs states.json; without it the tab falls back to the replay.
  const live = mode === 'live' && !!data.states
  const scale = live ? PM_SCALE : API_SCALE

  const rows = useMemo(() => {
    if (data.status !== 'ok') return []
    if (live) {
      return data.states.states.map(s => {
        const key = NAME2KEY[s.state]
        const m = waqiFor(data.waqi, key)
        return { key, name: NAMES[key] || s.state, today: null, v: { ...s.pm25, now: m ? m.pm : null }, meas: m, arrival: arrivalLive(s.pm25) }
      }).filter(r => r.key)
    }
    return (data.timeline[day] || []).map(r => {
      const key = NAME2KEY[r.zone]
      return { key, name: NAMES[key] || r.zone, zone: r.zone, today: r.api_today, v: r.api, arrival: r.arrival_hours }
    }).filter(r => r.key)
  }, [data, live, day])
  const byKey = useMemo(() => Object.fromEntries(rows.map(r => [r.key, r])), [rows])
  const hourly = useMemo(() => {
    const h = data.status === 'ok' && data.hourly
    if (!h || !h.states) return null
    return Object.fromEntries(Object.entries(h.states).map(([n, v]) => [NAME2KEY[n], v]).filter(([k]) => k))
  }, [data])

  if (data.status === 'loading') return <div className="map-root"><div className="card map-msg">Loading the haze forecast…</div></div>
  if (data.status === 'error') return (
    <div className="map-root"><div className="card map-msg" role="alert">
      <b>Couldn't load the forecast.</b> {data.error}. Run <code>python -m pipeline.physical.models.export_forecast</code> in <code>hazeguard/</code> and copy the JSON into <code>data/out/</code>.
    </div></div>
  )

  // Live windows are counted from the hour the CAMS forecast was fetched (hour 0 of forecast_live.json).
  const genAt = data.states && data.states.generated_at
  const t0 = (data.hourly && data.hourly.issued_at) || genAt
  const targetLabel = h => (live ? fmtLiveDay(t0, h) : fmtDay(addDays(day, +h / 24), { year: 'numeric' }))
  const shortDate = h => (live ? fmtLiveDay(t0, h, true) : fmtShort(addDays(day, h / 24)))
  const choose = k => {
    select && select(k)
    if (window.matchMedia && matchMedia('(max-width: 900px)').matches && detailRef.current) {
      detailRef.current.scrollIntoView({ behavior: REDUCE ? 'auto' : 'smooth', block: 'start' })
    }
  }
  const hasMeas = live && rows.some(r => r.meas)
  const showNow = hasMeas && layer === 'now'
  const mapH = showNow ? 'now' : horizon
  const pickH = h => { setLayer('fc'); setHorizon && setHorizon(h) }
  const klangRow = byKey.sl // Klang, the demo's port town, is in Selangor
  const klangArr = klangRow ? klangRow.arrival : null
  const klangText = klangArr == null ? 'Clear for 72 h'
    : live ? `Above 35: ${arrivalFor(klangArr, scale).replace('Next 24 h', 'next 24 h')}`
      : klangArr === 0 ? 'Unhealthy now' : `Unhealthy ${arrivalFor(klangArr, scale)}`

  return (
    <div className={'map-root' + (live ? ' is-live' : '')}>
      <section className="card map-main" aria-label="Haze forecast map">
        <div className="map-bar">
          <div className="seg map-seg" role="group" aria-label="Data source">
            <button type="button" aria-pressed={live} disabled={!data.states} onClick={() => { setMode('live'); setPlaying(false) }}>
              <span className="map-seg-t"><i className="map-live" aria-hidden="true" />Live now</span><span className="map-seg-s">CAMS PM2.5 · µg/m³</span>
            </button>
            <button type="button" aria-pressed={!live} onClick={() => setMode('replay')}>
              <span className="map-seg-t">Sep 2019 replay</span><span className="map-seg-s">XGBoost haze model · API</span>
            </button>
          </div>
          <div className="seg map-seg map-zoom" role="group" aria-label="Zoom">
            {VIEWS.map(v => <button key={v.k} type="button" aria-pressed={view === v.k} onClick={() => setView(v.k)}>{v.label}</button>)}
          </div>
        </div>

        <div className="map-read" aria-live="polite">
          <div className="map-when">
            <b className="num">{showNow ? 'Now' : `+${horizon} h`}</b>
            <span>
              {showNow ? <><strong>Measured PM2.5 ≈ · station readings</strong><br />WAQI · fetched {fmtMYT(data.waqi.fetched_at)}</> : <>
              <strong>{live ? `${+horizon - 24}–${horizon} h ahead · to ${shortDate(+horizon)}` : targetLabel(horizon)}</strong><br />
              {live ? `CAMS · fetched ${fmtMYT(t0)}` : `XGBoost · made ${fmtDay(day, { year: 'numeric' })}`}</>}
            </span>
          </div>
          {klangRow && (
            <button type="button" className="map-klang" onClick={() => { setView('klang'); choose('sl') }} aria-label="Zoom to Klang, Selangor">
              <span className="label">Klang, Selangor</span>
              <b>{klangText}</b>
            </button>
          )}
        </div>

        <Trajectory rows={rows} scale={scale} horizon={mapH} setHorizon={pickH} live={live} dateOf={shortDate}
          meas={hasMeas} onNow={() => setLayer('now')} />

        <StateMap rows={rows} scale={scale} horizon={mapH} sel={sel} onSelect={choose} view={view} dateLabel={showNow ? 'measured now' : targetLabel(horizon)} />

        {!live && (
          <Scrubber days={days} timeline={data.timeline} di={di} horizon={horizon} defaultDay={defaultDay}
            playing={playing} onPlay={() => { if (di >= days.length - 1) setDayIdx(0); setPlaying(p => !p) }}
            onDay={i => { setPlaying(false); setDayIdx(i) }} />
        )}

        <Legend scale={scale} measured={showNow} credit={live && !!data.waqi} />
        <About live={live} metrics={data.metrics} t0={t0} waqi={live ? data.waqi : null} />
      </section>

      <Matrix rows={rows} scale={scale} horizon={mapH} setHorizon={pickH} sel={sel} onSelect={choose}
        sort={sort} setSort={setSort} dateOf={shortDate} live={live} onNow={hasMeas ? () => setLayer('now') : null} />

      <div ref={detailRef} className="map-detailwrap">
        <Detail r={byKey[sel]} rkey={sel} scale={scale} horizon={horizon} setHorizon={setHorizon} live={live} day={day} days={days}
          timeline={data.timeline} metrics={data.metrics} t0={t0} hourly={hourly && hourly[sel]} waqiOk={!!data.waqi}
          dateOf={h => (live ? fmtLiveDay(t0, h) : fmtDay(addDays(day, h / 24)))} />
        {!live && <ModelStrip metrics={data.metrics} horizon={horizon} />}
      </div>
    </div>
  )
}

function fmtLiveDay(iso, h, short) {
  try {
    const d = new Date(Date.parse(iso) + h * 3600e3)
    return d.toLocaleDateString('en-GB', { timeZone: 'Asia/Kuala_Lumpur', day: 'numeric', month: 'short', ...(short ? {} : { weekday: 'short', year: 'numeric' }) })
  } catch (e) { return `+${h} h` }
}

/* Is the haze getting worse or better over the next 72 h? States over the line + the peak, per horizon. */
function Trajectory({ rows, scale, horizon, setHorizon, live, dateOf, meas, onNow }) {
  const colOf = h => {
    const vs = rows.map(r => ({ r, v: r.v[h] })).filter(o => o.v != null).sort((a, b) => b.v - a.v)
    return { h, n: vs.filter(o => scale.bad(o.v)).length, top: vs[0], vs }
  }
  const cols = HS.map(colOf)
  const nowCol = meas ? colOf('now') : null
  const [a, , c] = cols
  const pk = o => (o.top ? o.top.v : 0)
  const trend = c.n !== a.n ? (c.n > a.n ? 'up' : 'dn') : Math.abs(pk(c) - pk(a)) > pk(a) * 0.1 ? (pk(c) > pk(a) ? 'up' : 'dn') : 'flat'
  const word = { up: 'Worsening', dn: 'Easing', flat: 'Holding steady' }[trend]
  return (
    <div className="map-traj" role="group" aria-label="Forecast trajectory">
      <div className="map-traj-h">
        <span className="label">{live ? <>States above the 35 µg/m³ standard</> : <>States unhealthy (API 101+)</>}</span>
        <span className={'map-traj-tr ' + trend}>{word} over 72 h</span>
      </div>
      <div className={'map-traj-cells' + (nowCol ? ' has-now' : '')}>
        {nowCol && (
          <button type="button" className={'map-traj-c meas' + (horizon === 'now' ? ' on' : '')} aria-pressed={horizon === 'now'} onClick={onNow}
            title="Measured at stations (WAQI), converted from US AQI"
            aria-label={`Measured now: ${nowCol.n} of ${nowCol.vs.length} states above 35 µg/m³; highest ${nowCol.top ? `${fmtV(nowCol.top.v, scale)} µg/m³ in ${nowCol.top.r.name}` : 'n/a'}. Show on map.`}>
            <span className="map-traj-k"><span><b>Now</b> <small>measured</small></span></span>
            <span className="map-traj-m">
              <span className="map-traj-n num">{nowCol.n}<small>/{nowCol.vs.length}</small></span>
              <span className="map-traj-pk"><span>Peak <b className="num">≈{nowCol.top ? fmtV(nowCol.top.v, scale) : '–'}</b></span><span>{nowCol.top && (SHORT[nowCol.top.r.key] || nowCol.top.r.name)}</span></span>
            </span>
            <span className="map-traj-strip" aria-hidden="true">
              {nowCol.vs.map(({ r, v }) => <i key={r.key} style={{ background: (scale.bands[scale.band(v)] || {}).c }} />)}
            </span>
          </button>
        )}
        {cols.map((o, i) => {
          const d = i ? o.n - cols[i - 1].n : null
          return (
            <button key={o.h} type="button" className={'map-traj-c' + (o.h === horizon ? ' on' : '')} aria-pressed={o.h === horizon}
              onClick={() => setHorizon && setHorizon(o.h)}
              aria-label={`+${o.h} hours: ${o.n} of ${rows.length} states ${live ? 'above 35 µg/m³' : 'unhealthy'}; peak ${o.top ? `${fmtV(o.top.v, scale)} ${scale.unit} in ${o.top.r.name}` : 'n/a'}`}>
              <span className="map-traj-k"><span><b>+{o.h} h</b> <small>{dateOf(+o.h)}</small></span>
                {d != null && <em className={d > 0 ? 'up' : d < 0 ? 'dn' : ''} title="Change from the previous window">{d > 0 ? `+${d}` : d < 0 ? `−${-d}` : '±0'}</em>}
              </span>
              <span className="map-traj-m">
                <span className="map-traj-n num">{o.n}<small>/{rows.length}</small></span>
                <span className="map-traj-pk"><span>Peak <b className="num">{o.top ? fmtV(o.top.v, scale) : '–'}</b></span><span>{o.top && (SHORT[o.top.r.key] || o.top.r.name)}</span></span>
              </span>
              <span className="map-traj-strip" aria-hidden="true">
                {o.vs.map(({ r, v }) => <i key={r.key} style={{ background: (scale.bands[scale.band(v)] || {}).c }} />)}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

/* Every method note and caveat lives here, collapsed by default. */
function About({ live, metrics, t0, waqi }) {
  const credits = []
  if (waqi && waqi.states) {
    const seen = new Set()
    Object.values(waqi.states).forEach(m => (m.attributions || []).forEach(a => {
      if (a && a.name && !seen.has(a.name)) { seen.add(a.name); credits.push(a) }
    }))
  }
  const stations = waqi && waqi.states ? Object.entries(waqi.states).filter(([, m]) => m && m.station) : []
  const st = (metrics && metrics.mae_by_state_all_horizons) || {}
  const wins = Object.values(st).filter(s => s.xgb_with_fire < s.arima).length
  const total = Object.keys(st).length
  return (
    <details className="map-about">
      <summary><span>About this forecast</span><span className="map-tag">{live ? <><i className="map-live" aria-hidden="true" />Copernicus CAMS · not our model</> : 'Our XGBoost model · 2019 replay'}</span></summary>
      <ul>
        {live ? <>
          <li>Live numbers are the Copernicus CAMS PM2.5 forecast (about 45 km grid) at each state capital, via Open-Meteo, fetched {fmtMYT(t0)} and re-pulled hourly by the pipeline. Hour 0 is the fetch hour.</li>
          <li>Map and table values are the mean of each 24-h window (0–24, 24–48, 48–72 h). The chart shows the hourly values behind them.</li>
          <li>Unit is µg/m³ of PM2.5, not the Air Pollutant Index, so the colours use their own bands: WHO 2021 guideline 15, Malaysian standard (MAAQS 2020) 35, 2015 interim limit 75. The standard is a 24-h mean, so a single hour above 35 is not a breach.</li>
          <li>Arrives = the first 24-h window whose mean is above 35 µg/m³.</li>
          <li>Our own XGBoost haze model forecasts the API index and is scored in the Sep 2019 replay.</li>
          <li>Measured = live station readings from the World Air Quality Index Project (aqicn.org), DOE Malaysia stations, one station nearest each state capital{waqi && waqi.fetched_at ? `, fetched ${fmtMYT(waqi.fetched_at)}` : ''}. These are observations, not a forecast; CAMS values are forecasts.</li>
          <li>WAQI reports PM2.5 as a US AQI sub-index. The µg/m³ shown (≈) is converted back with US EPA breakpoints, so it is approximate. The raw US AQI is shown beside it in the state panel.</li>
          <li>A station reading is one point at one hour; CAMS is a ~45 km grid cell, so the two can differ.</li>
          {!waqi && <li>Measured readings are unavailable right now (waqi_now.json not loaded).</li>}
          {credits.length > 0 && <li>Data sources: {credits.map((a, i) => <span key={a.name}>{i ? ' · ' : ''}{a.url ? <a href={a.url} target="_blank" rel="noopener noreferrer">{a.name}</a> : a.name}</span>)}</li>}
          {stations.length > 0 && <li>Stations: {stations.map(([n, m], i) => <span key={n}>{i ? ' · ' : ''}{n}: {m.station_url ? <a href={m.station_url} target="_blank" rel="noopener noreferrer">{m.station}</a> : m.station}</span>)}</li>}
        </> : <>
          <li>XGBoost haze model, 17 inputs: past API, wind, rain, season and NASA FIRMS fire hotspots. Trained on 2013 to 28 Aug 2019, so the Sep 2019 haze was never in its training data.</li>
          <li>Baseline: ARIMA on each state's API history.{total > 0 && <> Average error over {metrics.rows_per_model?.toLocaleString('en-MY')} state-days, 1 Sep to 6 Oct 2019; XGBoost beat ARIMA in {wins} of {total} states.</>}</li>
          <li>Values are the Malaysian Air Pollutant Index (DOE bands). Arrives = first forecast day at API 101 or above; "Now" means today is already unhealthy.</li>
          <li>Press play to step through the forecasts made each day; the bars count states forecast unhealthy at the selected horizon.</li>
        </>}
      </ul>
    </details>
  )
}

/* Day scrubber: bars = states forecast unhealthy at the current horizon, made on that day. */
function Scrubber({ days, timeline, di, horizon, defaultDay, playing, onPlay, onDay }) {
  const counts = days.map(d => timeline[d].filter(r => r.api[horizon] >= 101).length)
  const max = Math.max(1, ...counts)
  const n = days.length
  return (
    <div className="map-scrub">
      <button type="button" className="map-play" onClick={onPlay} aria-label={playing ? 'Pause replay' : 'Play the Sep 2019 haze day by day'}>
        {playing
          ? <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="2" width="3.5" height="12" rx="1" /><rect x="9.5" y="2" width="3.5" height="12" rx="1" /></svg>
          : <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.2v11.6c0 .6.7 1 1.2.6l8.6-5.8c.4-.3.4-.9 0-1.2L5.2 1.6C4.7 1.2 4 1.6 4 2.2z" /></svg>}
      </button>
      <div className="map-strip">
        <div className="map-bars" aria-hidden="true">
          {counts.map((c, i) => (
            <i key={days[i]} className={(i === di ? 'on' : '') + (days[i] === defaultDay ? ' demo' : '')}
              style={{ height: `${Math.max(6, (c / max) * 100)}%` }} />
          ))}
        </div>
        <input type="range" className="map-range" min={0} max={n - 1} step={1} value={di}
          onChange={e => onDay(+e.target.value)}
          aria-label="Forecast day, September to October 2019"
          aria-valuetext={`${fmtDay(days[di], { year: 'numeric' })}: ${counts[di]} states forecast unhealthy at +${horizon} hours`} />
        <div className="map-ticks" aria-hidden="true">
          {days.map((d, i) => (i % 7 === 0 || (i === n - 1 && i % 7 > 4)) && (
            <span key={d} className={i % 14 ? 'odd' : ''} style={{ left: `${((i + 0.5) / n) * 100}%` }}>{fmtShort(d)}</span>
          ))}
        </div>
      </div>
      <div className="map-scrub-read">
        <span className="label">States unhealthy at +{horizon} h</span>
        <b className="num">{counts[di]}</b>
      </div>
    </div>
  )
}

function Legend({ scale, measured, credit }) {
  return (
    <div className="map-legend" role="note" aria-label={`Colour key: ${scale.unitLong}`}>
      <span className="map-legend-t">{scale.id === 'api' ? 'Air Pollutant Index (API)' : measured ? 'PM2.5, µg/m³, measured ≈' : 'PM2.5, µg/m³, 24-h mean'}</span>
      {scale.bands.map(b => (
        <span key={b.label} className="map-key"><i style={{ background: b.c }} /><b title={b.label}>{b.short || b.label}</b><span className="num">{b.range}</span></span>
      ))}
      {credit && (
        <span className="map-credit">Measured: <a href="https://aqicn.org/" target="_blank" rel="noopener noreferrer">World Air Quality Index Project (aqicn.org)</a> · DOE Malaysia stations</span>
      )}
    </div>
  )
}

/* The replay model, in one line: what it is, what it saw, how it scored against ARIMA. */
function ModelStrip({ metrics, horizon }) {
  if (!metrics || !metrics.mae || !metrics.mae.arima || !metrics.mae.xgb_with_fire) return null
  const st = metrics.mae_by_state_all_horizons || {}
  const wins = Object.values(st).filter(s => s.xgb_with_fire < s.arima).length
  const total = Object.keys(st).length
  return (
    <section className="card map-model" aria-label="Replay model accuracy">
      <div className="map-model-t">
        <b>Replay model: XGBoost vs ARIMA</b>
        <span>Avg error, API points · lower is better{total > 0 && <> · won {wins}/{total} states</>}</span>
      </div>
      <ul className="map-model-mae" aria-label="Average forecast error in API points, lower is better">
        {HS.map(h => {
          const x = metrics.mae.xgb_with_fire[h + 'h'], a = metrics.mae.arima[h + 'h']
          const pct = Math.round((1 - x / a) * 1000) / 10
          return (
            <li key={h} className={h === horizon ? 'on' : ''}>
              <span className="label">+{h} h error</span>
              <span className="num"><b>{x.toFixed(2)}</b> <span className="vs">vs {a.toFixed(2)}</span></span>
              <em className={pct > 0 ? 'up' : 'dn'}>{pct > 0 ? `XGBoost ${pct}% better` : `ARIMA ${Math.abs(pct)}% better`}</em>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

/* Selected state: forecast, arrival, chart, and (replay) how the model did there. */
function Detail({ r, rkey, scale, horizon, setHorizon, live, day, days, timeline, metrics, t0, hourly, dateOf, waqiOk }) {
  if (!r) return <section className="card map-detail"><div className="map-d-sec hint">No forecast for {NAMES[rkey] || 'this state'}.</div></section>
  const v = r.v[horizon], bi = scale.band(v), b = scale.bands[bi]
  const arrH = r.arrival
  const steps = live
    ? [...(hourly ? [{ k: 'now', lbl: 'Now (hourly)', v: hourly[0] }] : []), ...HS.map(h => ({ k: h, lbl: `${+h - 24}–${h} h`, v: r.v[h] }))]
    : [{ k: 'now', lbl: 'Today', v: r.today }, ...HS.map(h => ({ k: h, lbl: `+${h} h`, v: r.v[h] }))]

  let chart = null
  if (live && hourly) {
    // drawn by LiveChart below
  } else if (live) {
    const slots = HS.map(h => ({ label: `+${h} h`, tip: `+${h} h · ${dateOf(h)}`, strong: h === horizon }))
    chart = {
      slots, marker: null,
      series: [{ id: 'cams', label: 'CAMS PM2.5, 24-h window mean', cls: 'xgb', dots: 'solid', pts: HS.map((h, i) => ({ i, v: r.v[h] })) }],
      aria: `${r.name} PM2.5 forecast: ${HS.map(h => `+${h} h ${fmtV(r.v[h], scale)} µg/m³`).join(', ')}.`,
    }
  } else {
    const di = days.indexOf(day)
    const start = di - 6
    const slots = [], obs = [], act = [], xgb = [{ i: 6, v: r.today }], ari = [{ i: 6, v: r.today }]
    const zoneRows = d => (timeline[d] || []).find(x => x.zone === r.zone)
    for (let s = 0; s < 10; s++) {
      const d = addDays(day, s - 6)
      const strong = s === 6 || String((s - 6) * 24) === horizon
      slots.push({ label: s === 6 ? 'Today' : s > 6 ? `+${(s - 6) * 24}h` : (s === 0 || s === 3 ? fmtShort(d) : ''), tip: fmtDay(d), strong })
      const row = zoneRows(d)
      if (s <= 6 && row && start + s >= 0) obs.push({ i: s, v: row.api_today })
      if (s > 6) act.push({ i: s, v: row ? row.api_today : null })
    }
    const ar = metrics && metrics.arima_forecasts && metrics.arima_forecasts[day] && metrics.arima_forecasts[day][r.zone]
    HS.forEach((h, j) => {
      xgb.push({ i: 7 + j, v: r.v[h] })
      ari.push({ i: 7 + j, v: ar ? (ar[h] ?? null) : null })
    })
    const series = [
      { id: 'obs', label: 'Observed', cls: 'obs', pts: obs },
      ...(ar ? [{ id: 'ari', label: 'ARIMA', cls: 'ari', pts: ari }] : []),
      { id: 'xgb', label: 'XGBoost', cls: 'xgb', dots: 'solid', pts: xgb },
      { id: 'act', label: 'Actual', cls: 'act', line: false, dots: 'hollow', pts: act },
    ]
    chart = {
      slots, marker: 6, series,
      aria: `${r.name}: observed API up to ${fmtDay(day)} then XGBoost forecast ${HS.map(h => `+${h} h ${r.v[h]}`).join(', ')}` +
        (ar ? `; ARIMA ${HS.map(h => `+${h} h ${ar[h] ?? 'n/a'}`).join(', ')}` : '') +
        `; actual ${act.map((p, j) => `+${(j + 1) * 24} h ${p.v ?? 'n/a'}`).join(', ')}.`,
    }
  }

  // Measured now (WAQI) and the CAMS hourly forecast for the same hour.
  const m = live ? r.meas : null
  let mH = null, camsAt = null
  if (m && hourly) {
    const h = (Date.parse(m.time) - Date.parse(t0)) / 3600e3
    if (Number.isFinite(h) && h > -3 && h <= hourly.length - 1) {
      mH = Math.max(0, h)
      camsAt = num(hourly[Math.round(mH)])
    }
  }
  const mb = m && scale.bands[scale.band(m.pm)]
  const ms = !live && metrics && metrics.mae_by_state_all_horizons && metrics.mae_by_state_all_horizons[r.zone]
  const peakV = Math.max(...HS.map(h => r.v[h] ?? 0))
  return (
    <section className="card map-detail" aria-label={`${r.name} forecast`}>
      <div className="map-d-head">
        <div className="map-d-name">
          <span className="label">{live ? 'Live PM2.5 forecast · CAMS' : 'Haze forecast · Sep 2019 replay'}</span>
          <h3>{r.name}</h3>
        </div>
        <div className="map-d-big">
          <b className="num">{fmtV(v, scale)}</b>
          <span><span className="map-unit">{scale.unit}</span><br />{live ? `${+horizon - 24}–${horizon} h avg` : `at +${horizon} h`}</span>
          {b && <span className="map-pill" style={{ background: b.c, color: b.ink }}>{b.label}</span>}
        </div>
      </div>
      <div className={'map-arr' + ((live ? arrH === 24 : arrH === 0) ? ' now' : arrH == null ? ' none' : '')}>
        <span className="label">{live ? 'Above 35 µg/m³' : 'Unhealthy air arrives'}</span>
        <b>{live ? arrivalFor(arrH, scale, true) : arrH === 0 ? 'Already unhealthy today' : arrH == null ? 'Not within 72 h' : `In ${arrH} hours`}</b>
        <span className="hint">{arrH == null
          ? `Peak window: ${fmtV(peakV, scale)} ${scale.unit}`
          : live ? `to ${dateOf(arrH)}` : arrH === 0 ? `Today's API ${r.today}` : dateOf(arrH)}</span>
      </div>
      {live && (
        <div className="map-meas" aria-label="Measured now">
          <span className="label">Measured now</span>
          {m ? <>
            <b className="num" title="Converted from the US AQI PM2.5 sub-index; approximate">
              <i style={{ background: mb && mb.c }} />≈{fmtV(m.pm, scale)}<small> µg/m³</small>
            </b>
            <span className="map-meas-aqi num" title="US AQI as reported by WAQI">US AQI {m.aqi ?? '–'}{m.pmAqi != null && m.pmAqi !== m.aqi ? ` · PM2.5 ${m.pmAqi}` : ''}</span>
            <span className="map-meas-st">
              {m.station_url ? <a href={m.station_url} target="_blank" rel="noopener noreferrer">{m.station}</a> : m.station}
              <span className="hint"> · {fmtTimeMYT(m.time)} · {ago(m.time)}</span>
            </span>
            {camsAt != null && <span className="map-meas-gap hint">CAMS forecast <b className="num">{fmtV(camsAt, scale)}</b> · measured <b className="num">≈{fmtV(m.pm, scale)}</b></span>}
          </> : <b className="num">–</b>}
          {!m && <span className="hint">{waqiOk ? 'No station reading' : 'Unavailable'}</span>}
        </div>
      )}
      <div className="map-steps" role="list" aria-label="Forecast by window">
        {steps.map(s => {
          const sb = scale.bands[scale.band(s.v)]
          return (
            <div role="listitem" key={s.k} className={s.k === horizon ? 'on' : ''}>
              <small>{s.lbl}</small>
              <b className="num"><i style={{ background: sb && sb.c }} />{fmtV(s.v, scale)}</b>
            </div>
          )
        })}
      </div>
      <div className="map-d-sec">
        {live && hourly ? <>
          <LiveChart hourly={hourly} means={r.v} scale={scale} horizon={horizon} setHorizon={setHorizon} issuedAt={t0} name={r.name} height={176}
            measured={m && mH != null ? { h: mH, v: m.pm } : null} />
          <div className="map-ch-key">
            <span><i className="sw map-s-cams" />CAMS hourly PM2.5</span>
            {m && mH != null && <span><i className="sw map-s-meas" />Measured ≈ (WAQI)</span>}
            <span><i className="sw map-s-mean" />24-h window mean (map colour)</span>
            <span><i className="sw map-s-std" />35 µg/m³ standard</span>
          </div>
        </> : <>
          <ForecastChart slots={chart.slots} series={chart.series} scale={scale} marker={chart.marker} ariaLabel={chart.aria} height={122} />
          <div className="map-ch-key">
            {chart.series.map(s => <span key={s.id}><i className={'sw map-s-' + s.cls} />{s.label}</span>)}
          </div>
        </>}
      </div>
      {ms && <StateScore ms={ms} name={r.name} />}
    </section>
  )
}

function StateScore({ ms, name }) {
  const x = ms.xgb_with_fire, a = ms.arima
  const max = Math.max(x, a)
  const better = x < a
  const pct = Math.abs(Math.round((1 - x / a) * 100))
  return (
    <div className="map-d-sec map-score">
      <p><b>{better ? 'XGBoost' : 'ARIMA'} {pct ? `${pct}% closer` : 'slightly closer'}</b> in {name}
        <span className="hint"> · avg error, API</span></p>
      <div className="map-sbars">
        {[['XGBoost', x, 'xgb'], ['ARIMA', a, 'ari']].map(([l, val, c]) => (
          <div className="map-sbar" key={c} title="Average error, API points, all horizons. Lower is better.">
            <span>{l}</span>
            <span className="map-sbar-t"><i className={'map-s-' + c} style={{ width: `${(val / max) * 100}%` }} /></span>
            <b className="num">{val.toFixed(1)}</b>
          </div>
        ))}
      </div>
    </div>
  )
}

// Smoke source attribution (Sep 2019 haze), from data/attribution.json (pipeline/build_attribution.py).
import { useMemo } from 'react'
import { KEY, NAME, dshort, nf, pct, short } from './format.js'

const TOP = 6
const SHORTP = p => p.replace('Bangka-Belitung Islands', 'Bangka-Belitung').replace('Malaysia (domestic)', 'Malaysia (own fires)')

function topRows(rows) {
  if (!rows || !rows.length) return []
  const head = rows.slice(0, TOP)
  const rest = rows.slice(TOP).reduce((a, r) => a + r.share, 0)
  return rest > 0.0005 ? [...head, { province: 'Other', share: rest }] : head
}

export function AttributionMap({ att, sel, onSelect }) {
  const bm = att.basemap
  const P = (la, lo) => [((lo - bm.L0) * bm.K).toFixed(1), ((bm.A1 - la) * bm.K).toFixed(1)]
  const selName = sel && NAME[sel]
  const replay = att.event.replay_day
  const shareBy = useMemo(() => {
    const rows = selName && att.by_state[selName] ? att.by_state[selName] : att.sources
    return Object.fromEntries(rows.map(r => [r.province, r.share]))
  }, [att, selName])
  const paths = useMemo(() => {
    const t = att.trajectories.filter(x => (selName ? x.state === selName : x.day === replay))
    return t.map(x => ({ ...x, d: 'M' + x.path.map(([la, lo]) => P(la, lo).join(',')).join('L') }))
  }, [att, selName])
  const cells = selName ? (att.cells.by_state[selName] || []) : att.cells.national
  const cmax = Math.max(1, ...cells.map(c => c[2]))
  const caps = att.trajectories.filter(x => x.day === replay)
  const label = selName ? `${short(selName)}: 72 h back-paths, every day; ${dshort(replay)} in bold` : `All states: 72 h back-paths on ${dshort(replay)}`
  return (
    <div className="ledger-att-map">
      <svg viewBox={`0 0 ${bm.w} ${bm.h}`} role="img" aria-label={`${label}. Orange dots: fires the air passed.`}>
        <defs><clipPath id="ledger-att-clip"><rect width={bm.w} height={bm.h} /></clipPath></defs>
        <g clipPath="url(#ledger-att-clip)">
          <rect width={bm.w} height={bm.h} className="ledger-att-sea" />
          <path d={bm.other} className="ledger-att-oth" />
          {Object.entries(bm.provinces).map(([p, d]) => {
            const s = shareBy[p] || 0
            return <path key={p} d={d} className="ledger-att-prov" style={s > 0.005 ? { fill: `rgba(217,84,30,${0.1 + Math.min(0.5, s * 0.9)})` } : undefined}><title>{`${p}${s ? ': ' + pct(s, 0) : ''}`}</title></path>
          })}
          <path d={bm.my} className="ledger-att-my" />
          {paths.map((x, i) => {
            const hi = !selName || x.day === replay
            return <path key={i} d={x.d} className={`ledger-att-tr${hi ? ' hi' : ''}`} />
          })}
          {cells.map(([la, lo, f], i) => {
            const [x, y] = P(la, lo)
            return <circle key={i} cx={x} cy={y} r={1.6 + 6 * Math.sqrt(f / cmax)} className="ledger-att-fire" />
          })}
          {caps.map(c => {
            const [x, y] = P(c.path[0][0], c.path[0][1])
            const k = KEY[c.state]
            const on = c.state === selName
            return (
              <circle key={c.state} cx={x} cy={y} r={on ? 6 : 4} className={`ledger-att-cap${on ? ' on' : ''}`}
                role="button" tabIndex={0} aria-label={short(c.state)} onClick={() => onSelect(on ? null : k)}
                onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect(on ? null : k))}>
                <title>{short(c.state)}</title>
              </circle>
            )
          })}
        </g>
      </svg>
      <div className="ledger-legend ledger-att-key">
        <span><i className="ledger-att-k-tr" />{selName ? `Back-paths, ${dshort(replay)} bold` : `Back-paths, ${dshort(replay)}`}</span>
        <span><i className="ledger-att-k-fire" />Fires passed (size = FRP)</span>
        <span><i className="ledger-att-k-prov" />Province share</span>
      </div>
    </div>
  )
}

function Bars({ rows, tone }) {
  const max = Math.max(...rows.map(r => r.share), 0.01)
  return (
    <div className="ledger-att-bars" role="list">
      {rows.map(r => (
        <div key={r.province} className="ledger-att-r" role="listitem" aria-label={`${r.province}: ${pct(r.share, 0)}`}>
          <span className="ledger-att-n" title={r.province}>{SHORTP(r.province)}</span>
          <span className="ledger-att-t"><i className={tone} style={{ width: (r.share / max) * 100 + '%' }} /></span>
          <span className="num ledger-att-v">{r.share < 0.01 && r.share > 0 ? '<1%' : pct(r.share, 0)}</span>
        </div>
      ))}
    </div>
  )
}

function DayBars({ att, selName }) {
  const dates = att.state_daily_frp.dates
  const vals = selName ? att.state_daily_frp.by_state[selName] : att.daily.map(d => d.frp)
  const max = Math.max(...vals, 1)
  const ri = dates.indexOf(att.event.replay_day)
  return (
    <div className="ledger-att-days">
      <div className="ledger-att-days-b" role="img" aria-label={`Matched fire intensity per day, ${dshort(dates[0])} to ${dshort(dates[dates.length - 1])}`}>
        {vals.map((v, i) => <i key={i} className={i === ri ? 'on' : undefined} style={{ height: Math.max(1, (v / max) * 100) + '%' }} title={`${dshort(dates[i])}: ${nf(Math.round(v))} MW`} />)}
      </div>
      <div className="ledger-spark-ax ledger-att-days-ax"><span>{dshort(dates[0])}</span>
        {ri > 0 && <span className="on" style={{ left: ((ri + 0.5) / dates.length) * 100 + '%' }}>{dshort(dates[ri])}</span>}
        <span>{dshort(dates[dates.length - 1])}</span></div>
    </div>
  )
}

export function AttributionSection({ att, sel, pick }) {
  const selName = sel && NAME[sel] && att.by_state[NAME[sel]] ? NAME[sel] : null
  const nat = topRows(att.sources)
  const st = selName ? topRows(att.by_state[selName]) : null
  const states = Object.keys(att.by_state).sort((a, b) => short(a).localeCompare(short(b)))
  return (
    <div className="ledger-att">
      <AttributionMap att={att} sel={selName ? sel : null} onSelect={pick} />
      <div className="ledger-att-side">
        <label className="ledger-att-pick">
          <span className="label">State</span>
          <select value={selName ? sel : ''} onChange={e => pick(e.target.value || null)}>
            <option value="">All states</option>
            {states.map(s => <option key={s} value={KEY[s]}>{short(s)}</option>)}
          </select>
        </label>
        <div className={`ledger-att-cols${st ? ' two' : ''}`}>
          <div>
            <div className="label">Malaysia, all states</div>
            <Bars rows={nat} tone="nat" />
          </div>
          {st && (
            <div>
              <div className="label">{short(selName)}</div>
              <Bars rows={st} tone="st" />
            </div>
          )}
        </div>
        <dl className="ledger-kv ledger-kv-3">
          <div><dt>Hotspots matched</dt><dd className="num">{nf(att.stats.hotspots_matched)}</dd></div>
          <div><dt>Back-paths</dt><dd className="num">{nf(att.stats.trajectories)}</dd></div>
          <div><dt>Matched FRP, summed</dt><dd className="num">{nf(Math.round(att.stats.frp_matched_mw))} MW</dd></div>
        </dl>
        <div>
          <div className="label">Fire passed per day{selName ? `, ${short(selName)}` : ''}</div>
          <DayBars att={att} selName={selName} />
        </div>
      </div>
    </div>
  )
}

export function AttributionMethod({ att }) {
  const p = att.params
  return (
    <>
      <h4>Smoke sources: method ({att.event.name})</h4>
      <p className="hint">{att.method}</p>
      <div className="ledger-tbl-wrap">
        <table className="ledger-tbl">
          <tbody>
            <tr><td><b>Event window</b></td><td>{att.event.start} to {att.event.end} (arrivals); map day {att.event.replay_day}</td></tr>
            <tr><td><b>Wind</b></td><td>{att.wind_level}; {p.wind_grid_deg}° grid, bilinear in space, linear in time</td></tr>
            <tr><td><b>Back-paths</b></td><td>{p.back_hours} h back in {p.step_h} h steps from each state capital; arrivals {p.arrivals_utc.map(h => String(h).padStart(2, '0')).join('/')} UTC; {nf(att.stats.trajectories)} paths, {nf(att.stats.truncated_left_grid)} stopped early at the grid edge. Map shows the {String(p.stored_path_arrival_utc).padStart(2, '0')}:00 UTC path, a point every {p.stored_path_every_h} h</td></tr>
            <tr><td><b>Fire match</b></td><td>Hotspot within {p.radius_km} km and ±{p.window_h} h of a path point, once per path, weighted by FRP. {nf(att.stats.hotspots_in_window)} hotspots in window, {nf(att.stats.hotspots_matched)} matched</td></tr>
            <tr><td><b>National share</b></td><td>{p.national_weighting}</td></tr>
          </tbody>
        </table>
      </div>
      <h4>Smoke sources: caveats</h4>
      <ul className="ledger-list hint">{att.caveats.map(c => <li key={c}>{c}</li>)}</ul>
      <h4>Smoke sources: data</h4>
      <ul className="ledger-list hint">{att.data_sources.map(d => <li key={d.name}>{d.name} · {/^https?:/.test(d.url) ? <a href={d.url} target="_blank" rel="noreferrer">{d.url}</a> : <span className="mono">{d.url}</span>} · {d.licence}</li>)}</ul>
    </>
  )
}

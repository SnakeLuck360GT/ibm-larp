import { Suspense, lazy, useEffect, useLayoutEffect, useRef, useState } from 'react'

// Tabs read their JSON from ${BASE_URL}data/ (dist/data/ when built, <repo>/data/out live in dev).
const REDUCE = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)

const MapTab = lazy(() => import('./tabs/Map/index.jsx'))
const RoutingTab = lazy(() => import('./tabs/Routing/index.jsx'))
const LedgerTab = lazy(() => import('./tabs/Ledger/index.jsx'))
const TABS = [
  { k: 'map', label: 'Command map', head: ['Command map', 'When does the haze arrive? Forecast air quality by state for the next 72 hours.'] },
  // Routing writes its own headline from the live ranking.
  { k: 'routing', label: 'Resource routing', head: null, ownWindow: true },
  { k: 'ledger', label: 'Cross-border ledger', head: ['Cross-border ledger', 'Where the smoke comes from and what it costs.'] },
]

// The plan's shared forecast window: one control in the top bar drives the Map and Ledger tabs.
// Routing ranks on the full 0–72 h plan or a chosen window inside its own scenario controls.
const HORIZONS = ['24', '48', '72']
function HorizonControl({ value, onChange }) {
  return (
    <div className="seg hz" role="group" aria-label="Forecast window">
      {HORIZONS.map(h => (
        <button key={h} type="button" aria-pressed={value === h} onClick={() => onChange(h)}
          style={value === h ? { background: 'var(--card)', color: 'var(--ink)', boxShadow: '0 1px 3px rgba(11,16,51,.12)' } : undefined}>+{h}h</button>
      ))}
    </div>
  )
}

function useHash(valid, def) {
  const read = () => { const h = (location.hash || '').slice(1); return valid.includes(h) ? h : def }
  const [v, setV] = useState(read)
  useEffect(() => {
    const f = () => setV(read())
    addEventListener('hashchange', f)
    return () => removeEventListener('hashchange', f)
  }, [])
  return [v, k => { setV(k); try { history.replaceState(null, '', '#' + k) } catch (e) { /* file:// */ } }]
}

function Tabs({ tab, go }) {
  const ref = useRef(null)
  const [ind, setInd] = useState({ left: 0, width: 0 })
  const measure = () => {
    const el = ref.current && ref.current.querySelector('[aria-selected="true"]')
    if (el) setInd({ left: el.offsetLeft + 10, width: el.offsetWidth - 20 })
  }
  useLayoutEffect(measure, [tab])
  useEffect(() => {
    addEventListener('resize', measure)
    document.fonts && document.fonts.ready.then(measure)
    return () => removeEventListener('resize', measure)
  }, [])
  return (
    <nav className="tabs" role="tablist" aria-label="Sections" ref={ref}>
      {TABS.map(({ k, label }) => (
        <button key={k} className="tab" role="tab" aria-selected={tab === k} onClick={() => go(k)}>{label}</button>
      ))}
      <span className="ind" style={{ left: ind.left + 'px', width: ind.width + 'px' }} />
    </nav>
  )
}

export default function App() {
  const [tab, setTab] = useHash(TABS.map(t => t.k), TABS[0].k)
  const [stKey, setStKey] = useState(null) // selected state, shared across tabs
  const [horizon, setHorizon] = useState('24')
  const go = t => { setTab(t); try { scrollTo({ top: 0, behavior: REDUCE ? 'auto' : 'smooth' }) } catch (e) { /* old browsers */ } }
  const shared = { baseUrl: import.meta.env.BASE_URL, horizon, setHorizon, stKey, select: setStKey }
  const cur = TABS.find(t => t.k === tab)

  return (
    <>
      <header className="top">
        <div className="top-in">
          <div className="brand"><img className="logo" src={`${import.meta.env.BASE_URL}logo.svg`} alt="HazeGuard" width="113" height="34" /></div>
          <Tabs tab={tab} go={go} />
          {!cur.ownWindow && (
            <div className="meta"><span className="hint" style={{ marginRight: 2 }}>Forecast</span><HorizonControl value={horizon} onChange={setHorizon} /></div>
          )}
        </div>
      </header>
      <div className="shell one" key={tab}>
        {cur.head && <div className="head enter"><h2>{cur.head[0]}</h2><p>{cur.head[1]}</p></div>}
        <main className="enter">
          <Suspense fallback={<div className="card" style={{ padding: 32 }}>Loading…</div>}>
            {tab === 'map' && <MapTab {...shared} />}
            {tab === 'routing' && <RoutingTab {...shared} />}
            {tab === 'ledger' && <LedgerTab {...shared} />}
          </Suspense>
        </main>
      </div>
    </>
  )
}

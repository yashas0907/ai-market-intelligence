import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getWatchlist, removeFromWatchlist, getCompany, getFundamentals } from '../api.js'
import { fmt, pct, utcStamp, Spinner, ErrorBox } from '../components.jsx'

export default function Watchlist() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [snapshots, setSnapshots] = useState({})

  const load = async () => {
    setLoading(true)
    try {
      const w = await getWatchlist()
      setItems(w.items || [])
      const snaps = {}
      for (const item of w.items || []) {
        try {
          const f = await getFundamentals(item.symbol)
          if (f && !f.error && f.derived?.latest) {
            const l = f.derived.latest
            snaps[item.symbol] = {
              revenue: l.revenue ? fmt(l.revenue.value / 1e9, 1) + 'B' : '—',
              growth: l.revenue_growth_yoy?.value != null ? pct(l.revenue_growth_yoy.value) : '—',
              period: l.revenue?.period || '—'
            }
          }
        } catch {}
      }
      setSnapshots(snaps)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  const remove = async (symbol) => {
    try { await removeFromWatchlist(symbol); await load() }
    catch (e) { setError(e.message) }
  }

  return (
    <div className="container">
      <h1 style={{ fontSize: 22, marginTop: 24 }}>Watchlist</h1>
      <div className="freshness" style={{ marginBottom: 10 }}>
        Saved companies with their latest stored metrics. Add companies from the <Link to="/" style={{ color: 'var(--accent)' }}>dashboard</Link>. No real-time alerts — metrics refresh when you view them.
      </div>
      <ErrorBox error={error} />
      {loading && <Spinner label="Loading watchlist metrics…" />}
      {!loading && (
        <div className="panel">
          {items.length === 0 && <div className="freshness">Watchlist is empty. Search a company on the dashboard and click “+ Watchlist”.</div>}
          {items.map(item => (
            <div key={item.symbol} className="claim" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 14 }}>
              <div>
                <div className="stmt"><Link to={`/?s=${item.symbol}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>{item.symbol}</Link> — {item.company_name}</div>
                <div className="ev">
                  added {utcStamp(item.added_at)} · revenue {snapshots[item.symbol]?.revenue || '—'} ({snapshots[item.symbol]?.period}) · growth {snapshots[item.symbol]?.growth || '—'}
                </div>
              </div>
              <button className="ghost" onClick={() => remove(item.symbol)}>Remove</button>
            </div>
          ))}
        </div>
      )}
      <div className="disclaimer">⚠️ Educational tool — not financial advice. Stored metrics are snapshots from their retrieval times, not live data.</div>
    </div>
  )
}

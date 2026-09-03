import { useState } from 'react'
import { Link } from 'react-router-dom'
import { searchCompanies, compareCompanies } from '../api.js'
import { fmt, pct, utcStamp, SentimentBadge, Spinner, ErrorBox } from '../components.jsx'

export default function Compare() {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState([])
  const [picked, setPicked] = useState([])
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const search = async (q) => {
    if (q.length < 2) return
    try {
      const h = await searchCompanies(q)
      setHits(h)
    } catch (e) { setError(e.message) }
  }

  const pick = (sym, name) => {
    if (picked.length >= 3) return
    if (picked.find(p => p.symbol === sym)) return
    setPicked([...picked, { symbol: sym, name }])
    setQuery(''); setHits([])
  }

  const run = async () => {
    setLoading(true); setError(''); setResult(null)
    try {
      const r = await compareCompanies(picked.map(p => p.symbol))
      setResult(r)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  const symbols = result?.symbols || []

  return (
    <div className="container">
      <h1 style={{ fontSize: 22, marginTop: 24 }}>Company Comparison</h1>
      <div className="freshness" style={{ marginBottom: 16 }}>
        Same deterministic calculations applied to both companies over identical windows. Missing metrics are reported as unavailable — never estimated. Differences are observations, not rankings.
      </div>

      <div className="searchbar">
        <input value={query} placeholder="Add company to comparison (max 3)…"
          onChange={e => { setQuery(e.target.value); search(e.target.value) }} />
        <button onClick={run} disabled={picked.length < 2 || loading}>Compare</button>
      </div>
      {hits.length > 0 && (
        <div className="dropdown" style={{ position: 'relative', marginTop: 4 }}>
          {hits.map(h => (
            <div key={h.symbol} className="item" onClick={() => pick(h.symbol, h.name)}>
              <span className="sym">{h.symbol}</span><span className="name">{h.name}</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
        {picked.map(p => (
          <span key={p.symbol} className="badge neutral" style={{ padding: '8px 14px', fontSize: 13 }}>
            {p.symbol} — {p.name}
            <button className="ghost" style={{ marginLeft: 10, padding: '2px 8px' }}
              onClick={() => setPicked(picked.filter(x => x.symbol !== p.symbol))}>×</button>
          </span>
        ))}
      </div>

      <ErrorBox error={error} />
      {loading && <Spinner label="Running identical pipelines for both companies…" />}

      {result && (
        <div className="panel fade-in" style={{ overflowX: 'auto' }}>
          <h3>SIDE-BY-SIDE COMPARISON</h3>
          <table className="comparison-table">
            <thead>
              <tr><th>Metric</th>{symbols.map(s => <th key={s}>{s}</th>)}</tr>
            </thead>
            <tbody>
              {Object.entries(result.metrics || {}).map(([key, m]) => (
                <tr key={key}>
                  <td>{m.label}</td>
                  {symbols.map(s => {
                    const v = m.values[s]
                    let display = '—'
                    if (v !== null && v !== undefined) {
                      if (key === 'net_profit_margin' || key === 'roe') display = pct(v * 100)
                      else if (key.includes('pct') || key.includes('growth')) display = pct(v, 1)
                      else display = typeof v === 'number' ? fmt(v) : String(v)
                    }
                    return <td key={s} className={v < 0 ? 'neg' : ''}>{display}</td>
                  })}
                </tr>
              ))}
              <tr>
                <td>News sentiment</td>
                {symbols.map(s => <td key={s}><SentimentBadge label={result.metrics?.sentiment_label?.values[s]} /></td>)}
              </tr>
            </tbody>
          </table>
          <div className="grid grid-2" style={{ marginTop: 20 }}>
            {symbols.map(s => (
              <div key={s} className="metric">
                <div className="label">RISK FACTORS — {s}</div>
                <div style={{ marginTop: 8 }}>
                  {(result.risks?.[s] || []).map((r, i) => <div key={i} className="freshness">⚠️ {r}</div>)}
                  {(!result.risks?.[s] || !result.risks[s].length) && <div className="freshness">No computed risk factors flagged.</div>}
                </div>
              </div>
            ))}
          </div>
          <div className="freshness" style={{ marginTop: 16 }}>{result.methodology}</div>
          <div className="freshness" style={{ marginTop: 4 }}>
            Retrieved: {symbols.map((s, i) => `${s} at ${utcStamp(result.retrieved_at?.[i])}`).join(' · ')}
          </div>
        </div>
      )}

      <div className="disclaimer">⚠️ {result?.disclaimer || 'Educational comparison of computed metrics — not rankings or recommendations.'}</div>
    </div>
  )
}

import { Link, NavLink } from 'react-router-dom'

export function Nav() {
  return (
    <nav className="topnav">
      <Link to="/" className="brand"><span className="dot" /> Market Intelligence</Link>
      <NavLink to="/" end>Dashboard</NavLink>
      <NavLink to="/compare">Compare</NavLink>
      <NavLink to="/watchlist">Watchlist</NavLink>
    </nav>
  )
}

export function fmt(v, digits = 2) {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return v.toLocaleString(undefined, { maximumFractionDigits: digits })
  return String(v)
}

export function pct(v, digits = 2) {
  if (v === null || v === undefined) return '—'
  const s = v > 0 ? '+' : ''
  return `${s}${fmt(v, digits)}%`
}

export function timeAgo(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const mins = Math.floor((Date.now() - d.getTime()) / 60000)
  if (mins < 60) return `${mins}m ago`
  const h = Math.floor(mins / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export function utcStamp(iso) {
  if (!iso) return ''
  return new Date(iso).toUTCString().slice(5, 25) + ' UTC'
}

export function SentimentBadge({ label }) {
  return <span className={`badge ${label || 'neutral'}`}>{(label || 'n/a').toUpperCase()}</span>
}

export function SeverityBadge({ level }) {
  return <span className={`badge ${level}`}>{level?.toUpperCase()}</span>
}

export function VerificationBadge({ v }) {
  return <span className={`badge ${v || 'INSUFFICIENT_EVIDENCE'}`}>{(v || 'UNVERIFIED').replace(/_/g, ' ')}</span>
}

export function Freshness({ list }) {
  if (!list || !list.length) return null
  return (
    <div className="panel">
      <h3>DATA FRESHNESS</h3>
      {list.map((f, i) => (
        <div key={i} className="freshness" style={{ padding: '4px 0' }}>
          <span className="pos">●</span> {f.source} — retrieved {utcStamp(f.retrieved_at)}
          {f.note ? ` · ${f.note}` : ''}
        </div>
      ))}
    </div>
  )
}

export function Spinner({ label }) {
  return <div className="freshness fade-in" style={{ padding: 20 }}>⏳ {label || 'Loading…'}</div>
}

export function ErrorBox({ error }) {
  if (!error) return null
  return <div className="disclaimer">⚠️ {error}</div>
}

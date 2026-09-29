const BASE = import.meta.env.VITE_API_BASE || '/api'
const TIMEOUT_MS = 60000

export function researchStreamUrl(jobId) {
  return `${BASE}/research/${jobId}/stream`
}

export function quoteStreamUrl(symbol) {
  return `${BASE}/company/${encodeURIComponent(symbol)}/quote/stream`
}

function f(url, opts = {}) {
  return window.fetch(url, { ...opts, signal: AbortSignal.timeout(TIMEOUT_MS) })
}

async function handle(r) {
  if (!r.ok) {
    let detail = r.statusText
    try { detail = (await r.json()).detail || detail } catch {}
    if (r.status === 503 || r.status === 502) detail = `${detail} — free-tier backend may be waking up; retry in ~30s`
    throw new Error(detail)
  }
  return r.json()
}

export async function searchCompanies(q) {
  return f(`${BASE}/companies/search?q=${encodeURIComponent(q)}&limit=10`).then(handle)
}

export async function getCompany(symbol) {
  return f(`${BASE}/company/${encodeURIComponent(symbol)}`).then(handle)
}

export async function getMarket(symbol, range = '1y') {
  return f(`${BASE}/company/${encodeURIComponent(symbol)}/market?range=${range}`).then(handle)
}

export async function getTechnical(symbol, range = '1y') {
  return f(`${BASE}/company/${encodeURIComponent(symbol)}/technical?range=${range}`).then(handle)
}

export async function getFundamentals(symbol) {
  return f(`${BASE}/company/${encodeURIComponent(symbol)}/fundamentals`).then(handle)
}

export async function getNews(symbol) {
  return f(`${BASE}/company/${encodeURIComponent(symbol)}/news?limit=12`).then(handle)
}

export async function getQuote(symbol) {
  return f(`${BASE}/company/${encodeURIComponent(symbol)}/quote`).then(handle)
}

export async function startResearch(symbol, depth = 'standard') {
  return f(`${BASE}/research?symbol=${encodeURIComponent(symbol)}&depth=${depth}`, { method: 'POST' }).then(handle)
}

export async function getResearch(jobId) {
  return f(`${BASE}/research/${jobId}`).then(handle)
}

export async function compareCompanies(symbols) {
  return f(`${BASE}/compare`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbols })
  }).then(handle)
}

export async function getWatchlist() {
  return f(`${BASE}/watchlist`).then(handle)
}

export async function addToWatchlist(symbol) {
  return f(`${BASE}/watchlist`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol })
  }).then(handle)
}

export async function removeFromWatchlist(symbol) {
  return f(`${BASE}/watchlist/${encodeURIComponent(symbol)}`, { method: 'DELETE' }).then(handle)
}

export async function uploadDocument(file, symbol) {
  const form = new FormData()
  form.append('file', file)
  return f(`${BASE}/documents/upload?company_symbol=${encodeURIComponent(symbol)}`, { method: 'POST', body: form }).then(handle)
}

export async function searchDocuments(q, symbol) {
  const p = new URLSearchParams({ q })
  if (symbol) p.set('company_symbol', symbol)
  return f(`${BASE}/documents/search?${p}`).then(handle)
}

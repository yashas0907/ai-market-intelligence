const BASE = '/api'

async function handle(r) {
  if (!r.ok) {
    let detail = r.statusText
    try { detail = (await r.json()).detail || detail } catch {}
    throw new Error(detail)
  }
  return r.json()
}

export async function searchCompanies(q) {
  return fetch(`${BASE}/companies/search?q=${encodeURIComponent(q)}&limit=10`).then(handle)
}

export async function getCompany(symbol) {
  return fetch(`${BASE}/company/${encodeURIComponent(symbol)}`).then(handle)
}

export async function getMarket(symbol, range = '1y') {
  return fetch(`${BASE}/company/${encodeURIComponent(symbol)}/market?range=${range}`).then(handle)
}

export async function getTechnical(symbol, range = '1y') {
  return fetch(`${BASE}/company/${encodeURIComponent(symbol)}/technical?range=${range}`).then(handle)
}

export async function getFundamentals(symbol) {
  return fetch(`${BASE}/company/${encodeURIComponent(symbol)}/fundamentals`).then(handle)
}

export async function getNews(symbol) {
  return fetch(`${BASE}/company/${encodeURIComponent(symbol)}/news`).then(handle)
}

export async function startResearch(symbol, depth = 'standard') {
  return fetch(`${BASE}/research?symbol=${encodeURIComponent(symbol)}&depth=${depth}`, { method: 'POST' }).then(handle)
}

export async function getResearch(jobId) {
  return fetch(`${BASE}/research/${jobId}`).then(handle)
}

export async function compareCompanies(symbols) {
  return fetch(`${BASE}/compare`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbols })
  }).then(handle)
}

export async function getWatchlist() {
  return fetch(`${BASE}/watchlist`).then(handle)
}

export async function addToWatchlist(symbol) {
  return fetch(`${BASE}/watchlist`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol })
  }).then(handle)
}

export async function removeFromWatchlist(symbol) {
  return fetch(`${BASE}/watchlist/${encodeURIComponent(symbol)}`, { method: 'DELETE' }).then(handle)
}

export async function uploadDocument(file, symbol) {
  const form = new FormData()
  form.append('file', file)
  return fetch(`${BASE}/documents/upload?company_symbol=${encodeURIComponent(symbol)}`, { method: 'POST', body: form }).then(handle)
}

export async function searchDocuments(q, symbol) {
  const p = new URLSearchParams({ q })
  if (symbol) p.set('company_symbol', symbol)
  return fetch(`${BASE}/documents/search?${p}`).then(handle)
}

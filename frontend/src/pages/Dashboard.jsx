import { useEffect, useRef, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AreaChart, Area, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  Legend, BarChart, Bar, CartesianGrid, ReferenceLine
} from 'recharts'
import {
  searchCompanies, getCompany, getMarket, getTechnical, getFundamentals,
  getNews, startResearch, getResearch, addToWatchlist, getQuote, researchStreamUrl
} from '../api.js'
import {
  fmt, pct, timeAgo, utcStamp, SentimentBadge, SeverityBadge,
  VerificationBadge, Freshness, Skeleton, LiveDot, ErrorBox
} from '../components.jsx'

const RANGES = ['1mo', '3mo', '6mo', '1y', '2y', '5y']

export default function Dashboard() {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [symbol, setSymbol] = useState('')
  const [company, setCompany] = useState(null)
  const [market, setMarket] = useState(null)
  const [range, setRange] = useState('1y')
  const [technical, setTechnical] = useState(null)
  const [fundamentals, setFundamentals] = useState(null)
  const [news, setNews] = useState(null)
  const [report, setReport] = useState(null)
  const [jobStatus, setJobStatus] = useState(null)
  const [depth, setDepth] = useState('standard')
  const [loading, setLoading] = useState({})
  const [error, setError] = useState('')
  const [watchMsg, setWatchMsg] = useState('')
  const [tab, setTab] = useState('overview')
  const [quote, setQuote] = useState(null)
  const pollRef = useRef(null)
  const quoteRef = useRef(null)
  const esRef = useRef(null)
  const [searchParams] = useSearchParams()

  const one = (k, fn) => async (...a) => {
    setLoading(l => ({ ...l, [k]: true }))
    setError('')
    try { return await fn(...a) }
    catch (e) { setError(`${k}: ${e.message}`); return null }
    finally { setLoading(l => ({ ...l, [k]: false }))
    }
  }

  const doSearch = useCallback(
    one('search', async (q) => {
      const hits = await searchCompanies(q)
      setHits(hits)
      setShowDropdown(hits.length > 0)
    }), [])

  useEffect(() => {
    if (query.length < 2) { setShowDropdown(false); return }
    const t = setTimeout(() => doSearch(query), 300)
    return () => clearTimeout(t)
  }, [query])

  const selectSymbol = async (sym) => {
    setShowDropdown(false)
    setQuery(sym)
    setSymbol(sym)
    setReport(null); setJobStatus(null)
    setTab('overview')
    const c = await one('company', getCompany)(sym)
    if (c) setCompany(c)
    const m = await one('market', getMarket)(sym, range)
    if (m) setMarket(m)
  }

  // live quote ticker: 60s auto-refresh while a symbol is selected (honestly labeled)
  useEffect(() => {
    if (!symbol) { setQuote(null); return }
    const refresh = () => getQuote(symbol).then(q => q && setQuote(q)).catch(() => {})
    refresh()
    quoteRef.current = setInterval(refresh, 60000)
    return () => clearInterval(quoteRef.current)
  }, [symbol])

  // deep-link support: /?s=SYMBOL (used by watchlist links)
  useEffect(() => {
    const s = searchParams.get('s')
    if (s && /^[A-Za-z0-9.\-^]{1,12}$/.test(s)) {
      selectSymbol(s.toUpperCase())
    }
  }, [])

  useEffect(() => {
    if (!symbol) return
    one('market', getMarket)(symbol, range).then(m => m && setMarket(m))
  }, [range])

  useEffect(() => {
    if (!symbol) return
    one('technical', getTechnical)(symbol, range).then(t => t && setTechnical(t))
    one('fundamentals', getFundamentals)(symbol).then(f => f && setFundamentals(f))
    one('news', getNews)(symbol).then(n => n && setNews(n))
  }, [symbol, range])

  const runResearch = async () => {
    setWatchMsg('')
    const r = await one('research', startResearch)(symbol, depth)
    if (r) {
      setReport(r.deduplicated ? r.report : null)
      setTab('report')
      if (!r.deduplicated) {
        clearInterval(pollRef.current)
        try {
          const es = new EventSource(researchStreamUrl(r.job_id))
          esRef.current = es
          es.addEventListener('progress', (ev) => {
            const st = JSON.parse(ev.data)
            setJobStatus(prev => ({ ...(prev || { job_id: r.job_id, symbol: r.symbol || symbol }), ...st }))
          })
          es.addEventListener('done', (ev) => {
            const fin = JSON.parse(ev.data)
            es.close(); esRef.current = null
            if (fin.status === 'completed' && fin.report) {
              setJobStatus(prev => ({ ...(prev || {}), status: 'completed', progress: 100 }))
              setReport(fin.report)
            } else {
              setError(`research failed: ${fin.error || 'unknown'}`)
            }
          })
          es.addEventListener('error', () => {
            es.close(); esRef.current = null
            fallbackPoll(r.job_id)
          })
        } catch {
          fallbackPoll(r.job_id)
        }
      }
    }
  }

  const fallbackPoll = (id) => {
    pollRef.current = setInterval(async () => {
      const st = await getResearch(id).catch(() => null)
      if (!st) return
      setJobStatus(st)
      if (st.status === 'completed') {
        clearInterval(pollRef.current)
        setReport(st.report)
      } else if (st.status === 'failed') {
        clearInterval(pollRef.current)
        setError(`research failed: ${st.error || 'unknown'}`)
      }
    }, 1500)
  }

  useEffect(() => () => { clearInterval(pollRef.current); if (esRef.current) esRef.current.close() }, [])

  const addWatch = async () => {
    try {
      const r = await addToWatchlist(symbol)
      setWatchMsg(`✓ ${r.symbol} added to watchlist`)
    } catch (e) { setWatchMsg(`⚠️ ${e.message}`) }
  }

  if (!symbol) return (
    <SearchLanding query={query} setQuery={setQuery} hits={hits} showDropdown={showDropdown} doSearch={doSearch} onSelect={selectSymbol} error={error} />
  )

  const closes = market?.points?.map(p => ({ ts: new Date(p.ts).getTime(), close: p.close, volume: p.volume })) || []

  return (
    <div className="container fade-in">
      <div className="searchbar">
        <div className="results">
          <input value={query} placeholder="Search company (e.g. AAPL, Microsoft)"
            onChange={e => { setQuery(e.target.value); setShowDropdown(false) }}
            onFocus={() => hits.length > 0 && query.length >= 2 && setShowDropdown(true)} />
          {showDropdown && (
            <div className="dropdown">
              {hits.map(h => (
                <div key={h.symbol} className="item" onMouseDown={() => selectSymbol(h.symbol)}>
                  <span className="sym">{h.symbol}</span>
                  <span className="name">{h.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <select value={depth} onChange={e => setDepth(e.target.value)} style={{ width: 140 }}>
          <option value="quick">Quick</option>
          <option value="standard">Standard</option>
          <option value="deep">Deep</option>
        </select>
        <button onClick={runResearch} disabled={!symbol}>Generate Research Report</button>
        <button className="ghost" onClick={addWatch}>+ Watchlist</button>
      </div>
      {watchMsg && <div className="freshness">{watchMsg}</div>}
      <ErrorBox error={error} />

      <Tabs tab={tab} setTab={setTab} />
      {loading.company && <div className="panel"><h3>COMPANY PROFILE</h3><Skeleton lines={2} boxes={1} /></div>}

      {tab === 'overview' && (
        <>
          <OverviewHead company={company} market={market} fundamentals={fundamentals} quote={quote} />
          <div className="panel">
            <h3>PRICE — {symbol} <span className="freshness">({range} daily, split-adjusted · retrieved {utcStamp(market?.retrieved_at)})</span></h3>
            <RangeBar range={range} setRange={setRange} />
            {loading.market ? <div className="panel"><h3>PRICE</h3><Skeleton lines={0} boxes={2} /></div> : (
              <ResponsiveContainer width="100%" height={320}>
                <AreaChart data={closes}>
                  <defs>
                    <linearGradient id="pg" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#38bdf8" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="#23324f" strokeDasharray="3 3" />
                  <XAxis dataKey="ts" tickFormatter={t => new Date(t).toLocaleDateString(undefined, { month: 'short' })} stroke="#8aa0c0" fontSize={11} />
                  <YAxis domain={['auto', 'auto']} stroke="#8aa0c0" fontSize={11} />
                  <Tooltip labelFormatter={t => new Date(t).toDateString()} formatter={v => [fmt(v), 'Close']} />
                  <Area type="monotone" dataKey="close" stroke="#38bdf8" fill="url(#pg)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="grid grid-2">
            <SentimentPanel news={news} />
            <EventsPanel news={news} />
          </div>
        </>
      )}

      {tab === 'technical' && <TechnicalTab technical={technical} loading={loading.technical} range={range} />}
      {tab === 'fundamentals' && <FundamentalsTab fundamentals={fundamentals} loading={loading.fundamentals} />}
      {tab === 'news' && <NewsTab news={news} loading={loading.news} />}
      {tab === 'report' && <ReportTab report={report} jobStatus={jobStatus} loading={loading.research} />}

      <div className="disclaimer">
        ⚠️ {report?.disclaimer || 'EDUCATIONAL/RESEARCH TOOL — NOT FINANCIAL ADVICE. All data from public sources as of the retrieval times shown. No buy/sell recommendations. No guaranteed outcomes.'}
      </div>
    </div>
  )
}

function RangeBar({ range, setRange }) {
  return (
    <div style={{ display: 'flex', gap: 6, margin: '8px 0 14px' }}>
      {RANGES.map(r => (
        <button key={r} className="ghost" style={r === range ? { borderColor: '#38bdf8', color: '#38bdf8' } : {}}
          onClick={() => setRange(r)}>{r}</button>
      ))}
    </div>
  )
}

function Tabs({ tab, setTab }) {
  const tabs = [['overview', 'Overview'], ['technical', 'Technical'], ['fundamentals', 'Fundamentals'], ['news', 'News & Sentiment'], ['report', 'AI Research Report']]
  return (
    <div style={{ display: 'flex', gap: 6, marginTop: 18 }}>
      {tabs.map(([k, label]) => (
        <button key={k} className="ghost" style={tab === k ? { borderColor: '#38bdf8', color: '#38bdf8' } : {}}
          onClick={() => setTab(k)}>{label}</button>
      ))}
    </div>
  )
}

function Metric({ label, value, sub, tone }) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className={`value ${tone || ''}`}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}

function OverviewHead({ company, market, fundamentals, quote }) {
  const closes = market?.points?.map(p => p.close).filter(Boolean) || []
  const chg = closes.length >= 2 ? (closes[closes.length - 1] - closes[0]) / closes[0] * 100 : null
  const latest = fundamentals?.derived?.latest || {}
  const ratios = latest.ratios || {}
  return (
    <>
      <div className="panel" style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 14 }}>
        <div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>{company?.name || '…'}</div>
          <div className="freshness" style={{ marginTop: 4 }}>
            {company?.symbol} · {company?.exchange || '—'} · {company?.sector || '—'} · {company?.industry || '—'}
            {company?.cik && <> · <a href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${company.cik}`} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>SEC CIK {company.cik}</a></>}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          {quote?.price != null && (
            <div style={{ fontSize: 22, fontWeight: 700 }}>
              {quote.price.toFixed(2)} <span style={{ fontSize: 14 }} className={quote.change_pct >= 0 ? 'pos' : 'neg'}>
                {quote.change_pct >= 0 ? '▲' : '▼'} {pct(quote.change_pct)}
              </span>
            </div>
          )}
          {quote && (
            <div className="freshness">
              <LiveDot />{quote.currency} · auto-refresh 60s · delayed ≤15 min
            </div>
          )}
        </div>
      </div>
      <div className="grid grid-4">
        <Metric label="Period Change" value={pct(chg)} tone={chg >= 0 ? 'pos' : 'neg'} sub={`${market?.count || 0} trading days`} />
        <Metric label="Revenue (FY)" value={latest.revenue ? fmt(latest.revenue.value / 1e9, 1) + 'B' : '—'} sub={latest.revenue ? `period ${latest.revenue.period}` : 'no SEC data'} />
        <Metric label="Rev Growth YoY" value={latest.revenue_growth_yoy?.value != null ? pct(latest.revenue_growth_yoy.value) : '—'} tone={latest.revenue_growth_yoy?.value > 0 ? 'pos' : 'neg'} sub="SEC 10-K" />
        <Metric label="Net Margin" value={ratios.net_profit_margin?.value != null ? pct(ratios.net_profit_margin.value * 100) : '—'} sub="net income / revenue" />
      </div>
    </>
  )
}

function SentimentPanel({ news }) {
  const arts = news?.articles || []
  const pos = arts.filter(a => a.sentiment === 'positive').length
  const neg = arts.filter(a => a.sentiment === 'negative').length
  const neu = arts.length - pos - neg
  const total = arts.length || 1
  return (
    <div className="panel">
      <h3>NEWS SENTIMENT <span className="freshness">({arts.length} recent articles · retrieved {utcStamp(news?.retrieved_at)})</span></h3>
      <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', height: 26, borderRadius: 6, overflow: 'hidden' }}>
            <div style={{ width: `${pos / total * 100}%`, background: 'var(--pos)' }} />
            <div style={{ width: `${neu / total * 100}%`, background: '#3b4a66' }} />
            <div style={{ width: `${neg / total * 100}%`, background: 'var(--neg)' }} />
          </div>
          <div className="freshness" style={{ marginTop: 6 }}>
            <span className="pos">● {pos} positive</span> · <span className="warn">● {neu} neutral</span> · <span className="neg">● {neg} negative</span>
          </div>
        </div>
      </div>
      <div className="freshness" style={{ marginTop: 10 }}>Lexicon-based sentiment over Google News snippets — an analytical signal with limitations; not a return predictor.</div>
    </div>
  )
}

function EventsPanel({ news }) {
  const arts = news?.articles || []
  const eventTypes = {}
  for (const a of arts) {
    const t = a.title.toLowerCase()
    if (t.includes('earnings') || t.includes('results')) eventTypes.earnings = (eventTypes.earnings || 0) + 1
    if (t.includes('acqui') || t.includes('merger')) eventTypes['M&A'] = (eventTypes['M&A'] || 0) + 1
    if (t.includes('lawsuit') || t.includes('regulat') || t.includes('probe')) eventTypes.regulatory = (eventTypes.regulatory || 0) + 1
    if (t.includes('launch') || t.includes('announc') || t.includes('unveil')) eventTypes.product = (eventTypes.product || 0) + 1
    if (t.includes('ceo') || t.includes('executive')) eventTypes.leadership = (eventTypes.leadership || 0) + 1
  }
  const keys = Object.keys(eventTypes)
  return (
    <div className="panel">
      <h3>EVENT SIGNALS IN RECENT NEWS <span className="freshness">(keyword-based, heuristic)</span></h3>
      {keys.length === 0 ? <div className="freshness">No event-type signals in the current news sample.</div> : keys.map(k => (
        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
          <span>{k}</span><span className="badge neutral">{eventTypes[k]} article(s)</span>
        </div>
      ))}
    </div>
  )
}

function TechnicalTab({ technical, loading, range }) {
  if (loading) return <div className="panel"><h3>TECHNICAL INDICATORS</h3><Skeleton lines={2} boxes={2} /></div>
  if (!technical) return <div className="panel">Technical data unavailable.</div>
  const s = technical.series || {}
  const data = (s.ts || []).map((ts, i) => ({
    ts: new Date(ts).getTime(), close: s.close?.[i], sma20: s.sma20?.[i], sma50: s.sma50?.[i],
    rsi: s.rsi14?.[i], bb_upper: s.bb_upper?.[i], bb_lower: s.bb_lower?.[i],
    macd: s.macd?.[i], macd_signal: s.macd_signal?.[i]
  }))
  return (
    <>
      <div className="grid grid-4">
        <Metric label="RSI(14)" value={fmt(technical.latest?.rsi14, 1)} sub={technical.latest?.rsi14 >= 70 ? 'overbought zone' : technical.latest?.rsi14 <= 30 ? 'oversold zone' : 'neutral zone'} />
        <Metric label="MACD" value={fmt(technical.latest?.macd, 3)} sub={`signal ${fmt(technical.latest?.macd_signal, 3)}`} />
        <Metric label="Ann. Volatility" value={technical.statistics?.annualized_volatility_pct ? fmt(technical.statistics.annualized_volatility_pct, 1) + '%' : '—'} sub="stdev of daily returns × √252" />
        <Metric label="Max Drawdown" value={technical.statistics?.max_drawdown_pct ? fmt(technical.statistics.max_drawdown_pct, 1) + '%' : '—'} sub="peak-to-trough, window" />
      </div>
      <div className="panel">
        <h3>PRICE + MOVING AVERAGES + BOLLINGER</h3>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={data}>
            <CartesianGrid stroke="#23324f" strokeDasharray="3 3" />
            <XAxis dataKey="ts" tickFormatter={t => new Date(t).toLocaleDateString(undefined, { month: 'short' })} stroke="#8aa0c0" fontSize={11} />
            <YAxis domain={['auto', 'auto']} stroke="#8aa0c0" fontSize={11} />
            <Tooltip labelFormatter={t => new Date(t).toDateString()} />
            <Legend />
            <Line type="monotone" dataKey="close" stroke="#e2e8f0" dot={false} strokeWidth={1.5} name="Close" />
            <Line type="monotone" dataKey="sma20" stroke="#38bdf8" dot={false} strokeWidth={1.2} name="SMA20" />
            <Line type="monotone" dataKey="sma50" stroke="#fbbf24" dot={false} strokeWidth={1.2} name="SMA50" />
            <Line type="monotone" dataKey="bb_upper" stroke="#3b4a66" dot={false} name="BB upper" />
            <Line type="monotone" dataKey="bb_lower" stroke="#3b4a66" dot={false} name="BB lower" />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="grid grid-2">
        <div className="panel">
          <h3>RSI(14)</h3>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data}>
              <CartesianGrid stroke="#23324f" strokeDasharray="3 3" />
              <XAxis dataKey="ts" tickFormatter={t => new Date(t).toLocaleDateString(undefined, { month: 'short' })} stroke="#8aa0c0" fontSize={11} />
              <YAxis domain={[0, 100]} stroke="#8aa0c0" fontSize={11} />
              <Tooltip labelFormatter={t => new Date(t).toDateString()} />
              <ReferenceLine y={70} stroke="#f87171" strokeDasharray="4 4" />
              <ReferenceLine y={30} stroke="#34d399" strokeDasharray="4 4" />
              <Line type="monotone" dataKey="rsi" stroke="#a78bfa" dot={false} strokeWidth={1.5} name="RSI" />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="panel">
          <h3>MACD</h3>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data}>
              <CartesianGrid stroke="#23324f" strokeDasharray="3 3" />
              <XAxis dataKey="ts" tickFormatter={t => new Date(t).toLocaleDateString(undefined, { month: 'short' })} stroke="#8aa0c0" fontSize={11} />
              <YAxis stroke="#8aa0c0" fontSize={11} />
              <Tooltip labelFormatter={t => new Date(t).toDateString()} />
              <Line type="monotone" dataKey="macd" stroke="#38bdf8" dot={false} strokeWidth={1.3} name="MACD" />
              <Line type="monotone" dataKey="macd_signal" stroke="#fbbf24" dot={false} strokeWidth={1.3} name="Signal" />
              <ReferenceLine y={0} stroke="#3b4a66" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="panel">
        <h3>SIGNALS (historical observations)</h3>
        {(technical.signals || []).map((s2, i) => (
          <div key={i} className="claim"><span className="stmt">{s2.indicator}: {s2.reading}</span><div className="ev">{s2.note}</div></div>
        ))}
      </div>
    </>
  )
}

function FundamentalsTab({ fundamentals, loading }) {
  if (loading) return <div className="panel"><h3>FUNDAMENTALS</h3><Skeleton lines={2} boxes={1} /></div>
  if (!fundamentals) return <div className="panel">Fundamentals unavailable.</div>
  if (fundamentals.error) return <div className="panel"><h3>FUNDAMENTALS</h3><div className="freshness">⚠️ {fundamentals.error}</div></div>
  const d = fundamentals.derived || {}
  const latest = d.latest || {}
  const ratios = latest.ratios || {}
  const trend = d.trend || {}
  const revTrend = (trend.revenue || []).map(t => ({ year: String(t.year), value: t.value / 1e9 }))
  return (
    <>
      <div className="grid grid-4">
        <Metric label={`Revenue FY${latest.latest_fiscal_year || ''}`} value={latest.revenue ? fmt(latest.revenue.value / 1e9, 1) + 'B' : '—'} sub={latest.revenue ? `period ${latest.revenue.period}` : 'unavailable'} />
        <Metric label="Net Income" value={latest.net_income ? fmt(latest.net_income.value / 1e9, 1) + 'B' : '—'} sub={latest.net_income ? `period ${latest.net_income.period}` : 'unavailable'} />
        <Metric label="Diluted EPS" value={latest.eps_diluted ? fmt(latest.eps_diluted.value) : '—'} sub={latest.eps_diluted ? `period ${latest.eps_diluted.period}` : 'unavailable'} />
        <Metric label="Op. Cash Flow" value={latest.operating_cash_flow ? fmt(latest.operating_cash_flow.value / 1e9, 1) + 'B' : '—'} sub="proxy for FCF input" />
      </div>
      <div className="grid grid-4">
        <Metric label="ROE" value={ratios.roe?.value != null ? pct(ratios.roe.value * 100) : '—'} sub={ratios.roe?.note} />
        <Metric label="Net Margin" value={ratios.net_profit_margin?.value != null ? pct(ratios.net_profit_margin.value * 100) : '—'} sub={ratios.net_profit_margin?.note} />
        <Metric label="Debt/Equity" value={ratios.debt_to_equity?.value != null ? fmt(ratios.debt_to_equity.value) : '—'} sub={ratios.debt_to_equity?.note} />
        <Metric label="R&D Intensity" value={ratios.rd_intensity?.value != null ? pct(ratios.rd_intensity.value * 100) : '—'} sub={ratios.rd_intensity?.note} />
      </div>
      {revTrend.length > 1 && (
        <div className="panel">
          <h3>REVENUE TREND (SEC 10-K, USD billions)</h3>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={revTrend}>
              <CartesianGrid stroke="#23324f" strokeDasharray="3 3" />
              <XAxis dataKey="year" stroke="#8aa0c0" fontSize={11} />
              <YAxis stroke="#8aa0c0" fontSize={11} />
              <Tooltip />
              <Bar dataKey="value" fill="#38bdf8" radius={[4, 4, 0, 0]} name="Revenue ($B)" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="panel">
        <h3>SOURCE</h3>
        <div className="freshness">Annual facts from SEC EDGAR XBRL (10-K filings), retrieved {utcStamp(fundamentals.retrieved_at)}. Missing metrics are shown as unavailable — never estimated.</div>
      </div>
    </>
  )
}

function NewsTab({ news, loading }) {
  if (loading) return <div className="panel"><h3>RECENT NEWS</h3><Skeleton lines={5} /></div>
  if (!news) return <div className="panel">News unavailable.</div>
  return (
    <div className="panel">
      <h3>RECENT NEWS <span className="freshness">(Google News RSS · retrieved {utcStamp(news.retrieved_at)})</span></h3>
      {(news.articles || []).map((a, i) => (
        <div key={i} className="article">
          <div className="title"><a href={a.url} target="_blank" rel="noreferrer">{a.title}</a></div>
          <div className="meta">
            {a.source} · {timeAgo(a.published_at)} · <SentimentBadge label={a.sentiment} />
            {a.sentiment_score != null && <span> (score {fmt(a.sentiment_score, 2)})</span>}
          </div>
        </div>
      ))}
      {(!news.articles || news.articles.length === 0) && <div className="freshness">No articles found in feed.</div>}
    </div>
  )
}

function ReportTab({ report, jobStatus, loading }) {
  if (loading && !report) return <div className="panel"><h3>AI RESEARCH REPORT</h3><div className="freshness">Starting research pipeline…</div><Skeleton lines={4} boxes={1} /></div>
  if (!report && jobStatus) return <ProgressTrace status={jobStatus} />
  if (!report) return (
    <div className="panel">
      <h3>AI RESEARCH REPORT</h3>
      <div className="freshness">Click “Generate Research Report” to run the full evidence pipeline: data collection → specialized agents → fact-checking → synthesis.</div>
    </div>
  )
  return <ReportView report={report} />
}

function ProgressTrace({ status }) {
  const stageMap = {}
  for (const s of status.stages || []) stageMap[s.stage] = s
  const order = ['collect:profile', 'collect:market', 'agents:analysis', 'agents:fact_check', 'agents:synthesis', 'persist']
  return (
    <div className="panel">
      <h3>RESEARCH PIPELINE — {status.symbol}</h3>
      <div className="progressbar"><div className="fill" style={{ width: `${status.progress || 5}%` }} /></div>
      <div className="progress-trace">
        {order.map(k => {
          const done = k !== status.stage && k in stageMap
          const active = k === status.stage
          return (
            <div key={k} className={`step ${done ? 'done' : ''} ${active ? 'active' : ''}`}>
              {done ? '✓' : active ? '●' : '○'} {k.replace(/[:_]/g, ' ').replace('agents', '').trim() || k}
            </div>
          )
        })}
        <div className={`step ${status.status === 'completed' ? 'done' : status.status === 'failed' ? 'negative' : 'active'}`}>
          {status.status === 'completed' ? '✓' : status.status === 'failed' ? '✗' : '●'} status: {status.status} ({status.progress || 0}%)
        </div>
      </div>
    </div>
  )
}

function ReportView({ report }) {
  const [showClaims, setShowClaims] = useState(false)
  return (
    <div className="report fade-in">
      <div className="panel">
        <h3>RESEARCH REPORT — {report.company_name} ({report.symbol})</h3>
        <div className="freshness">generated {utcStamp(report.generated_at)}</div>
        <h2>Executive Summary</h2>
        <p>{report.executive_summary}</p>
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <h3>BULL CASE — evidence supporting positive interpretation</h3>
          {(report.bull_case || []).map((b, i) => (
            <div key={i} className="claim"><div className="stmt pos">+ {b.argument}</div><div className="ev">evidence: {b.evidence}</div></div>
          ))}
          {(!report.bull_case || !report.bull_case.length) && <div className="freshness">No positive-evidence items met the bar.</div>}
        </div>
        <div className="panel">
          <h3>BEAR CASE — evidence supporting negative interpretation</h3>
          {(report.bear_case || []).map((b, i) => (
            <div key={i} className="claim"><div className="stmt neg">− {b.argument}</div><div className="ev">evidence: {b.evidence}</div></div>
          ))}
          {(!report.bear_case || !report.bear_case.length) && <div className="freshness">No negative-evidence items met the bar.</div>}
        </div>
      </div>

      {(report.contradictions || []).length > 0 && (
        <div className="panel">
          <h3>CONTRADICTIONS DETECTED</h3>
          {report.contradictions.map((c, i) => (
            <div key={i} className="risk-item medium">
              <div className="stmt">{c.description}</div>
              <div className="ev">{(c.evidence || []).join(' | ')}</div>
              <div className="ev">resolution: {c.resolution}</div>
            </div>
          ))}
        </div>
      )}

      <div className="panel">
        <h3>RISKS (evidence-backed)</h3>
        {(report.risks || []).map((r, i) => (
          <div key={i} className={`risk-item ${r.severity}`}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
              <span className="stmt">{r.risk}</span>
              <SeverityBadge level={r.severity} />
            </div>
            <div className="ev">category: {r.category} · evidence: {r.evidence} · basis: {r.basis}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <h3>KEY UNKNOWN / INSUFFICIENT EVIDENCE</h3>
          <ul className="unknowns">{(report.key_unknowns || []).map((u, i) => <li key={i}>{u}</li>)}</ul>
        </div>
        <div className="panel">
          <h3>AGENT RUNS</h3>
          {Object.entries(report.agent_runs || {}).map(([name, r]) => (
            <div key={name} className="claim">
              <div className="stmt">{name}: {r.status} ({r.duration_ms}ms)</div>
              <div className="ev">{r.summary?.slice(0, 220)}{r.summary?.length > 220 ? '…' : ''}</div>
            </div>
          ))}
        </div>
      </div>

      <Freshness list={report.data_freshness} />

      <div className="panel">
        <h3>SOURCES & CLAIM VERIFICATION <button className="ghost" onClick={() => setShowClaims(!showClaims)}>{showClaims ? 'Hide' : 'Show'} claim trace ({report.claims?.length || 0} claims)</button></h3>
        <div className="freshness" style={{ marginBottom: 10 }}>Every claim below was verified against the evidence layer: claim → evidence → source → retrieval time.</div>
        {showClaims && (report.claims || []).map((c, i) => (
          <div key={i} className="claim">
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
              <span className="stmt">{c.statement}</span>
              <VerificationBadge v={c.verification} />
            </div>
            {(c.evidence || []).map((e, j) => (
              <div key={j} className="ev">
                ↳ {e.source.name} · retrieved {utcStamp(e.source.retrieved_at)}{e.source.url && <> · <a href={e.source.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>source</a></>}
              </div>
            ))}
          </div>
        ))}
        <div style={{ marginTop: 14 }}>
          <h3>ALL SOURCE REFERENCES ({report.sources?.length || 0})</h3>
          {Array.from(new Set((report.sources || []).map(s => s.name))).map((name, i) => (
            <div key={i} className="freshness">● {name}</div>
          ))}
        </div>
      </div>
    </div>
  )
}

function SearchLanding({ query, setQuery, hits, showDropdown, doSearch, onSelect, error }) {
  return (
    <div className="container">
      <div className="hero">
        <div className="hero-title">AI Market Intelligence<br />& Research Platform</div>
        <div className="hero-sub">
          Evidence-backed research reports for any publicly traded company — market data, SEC fundamentals,
          technicals, news sentiment, risk analysis and cited AI synthesis. Every claim traceable to its source.
        </div>
      </div>
      <div className="searchbar" style={{ maxWidth: 560, margin: '0 auto' }}>
        <div className="results">
          <input value={query} autoFocus placeholder="Search a company — AAPL, MSFT, RELIANCE.NS, TCS.NS…"
            onChange={e => setQuery(e.target.value)} />
          {showDropdown && (
            <div className="dropdown">
              {hits.map(h => (
                <div key={h.symbol} className="item" onMouseDown={() => onSelect(h.symbol)}>
                  <span className="sym">{h.symbol}</span>
                  <span className="name">{h.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <ErrorBox error={error} />
      <div className="grid grid-3" style={{ maxWidth: 960, margin: '50px auto 0' }}>
        <div className="metric feature-card">
          <div className="icon">📊</div>
          <div className="title">Deterministic Analytics</div>
          <div className="desc">Every number computed from real data — never generated, never estimated.</div>
        </div>
        <div className="metric feature-card">
          <div className="icon">🔗</div>
          <div className="title">Evidence Provenance</div>
          <div className="desc">Claim → evidence → source → retrieval time. Audit every statement.</div>
        </div>
        <div className="metric feature-card">
          <div className="icon">🤖</div>
          <div className="title">Multi-Agent + RAG</div>
          <div className="desc">Specialized agents, fact-checked claims, real-time SSE progress.</div>
        </div>
      </div>
      <div className="disclaimer" style={{ maxWidth: 960, marginLeft: 'auto', marginRight: 'auto' }}>
        ⚠️ EDUCATIONAL/RESEARCH TOOL — NOT FINANCIAL ADVICE. Aggregates public data and presents analytical signals with uncertainty. No buy/sell recommendations or guaranteed outcomes.
      </div>
    </div>
  )
}

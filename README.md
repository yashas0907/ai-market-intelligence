# AI Market Intelligence & Research Platform

[![CI](https://github.com/yashas0907/ai-market-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/yashas0907/ai-market-intelligence/actions/workflows/ci.yml)

An evidence-backed market research platform: pick any publicly traded company (US or international — NSE/BSE/NASDAQ/NYSE) and receive a structured intelligence report combining live quotes, market data, SEC fundamentals, technical indicators, news sentiment, risk analysis, and AI synthesis — **with every claim traceable to its source**. Real-time research progress streams to the dashboard via Server-Sent Events.

**By Yashas P Phatak.**

> **Educational/research tool. NOT financial advice.** No buy/sell recommendations, no price predictions, no guaranteed outcomes. Every number comes from real data at its stated retrieval time; missing data is reported as unavailable, never invented.

---

## Problem statement

Retail-facing "AI stock pickers" typically do `ticker → LLM → opinion`, producing confident-sounding text with fabricated numbers. This project demonstrates the correct architecture for financial AI research: deterministic data pipelines first, an evidence layer that grounds every claim, specialized agents that must call tools for facts, a fact-checker that re-verifies numeric claims, and synthesis that cannot override sources. The output is a research report a reader can audit claim-by-claim.

## Architecture

```
                     COMPANY (user selection)
                              │
                      DATA COLLECTION (collectors)
      ┌───────────────────────┼───────────────────────┐
      ▼                       ▼                       ▼
 Yahoo chart API        SEC EDGAR (XBRL)        Google News RSS
 (daily OHLCV)          (10-K annual facts)     (article metadata)
      │                       │                       │
 VALIDATION → NORMALIZATION → STORAGE (SQLite/SQLAlchemy) → FEATURE ENGINEERING
      │                       │                       │
      ▼                       ▼                       ▼
 Technical Analysis      Fundamental Analysis     Sentiment + Event Detection
 (SMA/EMA/RSI/MACD/      (margins, ROE, D/E,      (lexicon engine +
  Bollinger/ATR/vol)      growth trends)            rule-based events)
      └───────────────────────┼───────────────────────┘
                              ▼
                     EVIDENCE LAYER (claim → evidence → source → retrieval time)
                              ▼
                     SPECIALIZED AGENTS (7)
   MarketData · Technical · Fundamental · News · Risk · FactChecker · Synthesizer
                              ▼
                 RISK / CONTRADICTION DETECTION
                              ▼
                  CITED RESEARCH REPORT + DASHBOARD
```

**Agent orchestration** — explicit state machine (`WorkflowState` dataclass), not a prompt loop:

| Stage | What happens | Failure behavior |
|---|---|---|
| `collect:profile` | resolve ticker → CIK → name (SEC first, Yahoo fallback) | recorded, continues |
| `collect:market` | 1y daily OHLCV via tool | recorded, market-dependent agents degrade |
| `agents:analysis` | 5 analysis agents run concurrently (bounded semaphore, per-agent DB session) | each failure isolated |
| `agents:fact_check` | every claim verified against its evidence; numeric claims cross-checked | misclassified claims flagged |
| `agents:synthesis` | bull/bear/unknowns from verified state only; LLM (if configured) writes prose from data | falls back to deterministic summary |
| `persist` | report, claims, evidence rows, agent runs stored | transactional |

Progress is streamed to the UI as **operational events only** (✓ market data collected · ● synthesizing report) — never chain-of-thought.

**RAG layer** — for source-grounded retrieval over filings/news/uploads:
parse → sanitize (untrusted-content rules) → sentence-aware chunking → metadata (symbol/doc_type/date) → embeddings → cosine retrieval with metadata prefilter → evidence.

## Data sources

See [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) — SEC EDGAR (authoritative, key-less), Yahoo Finance public chart API (unofficial, documented), Google News RSS (metadata + snippets only). Each source documents limitations, rate limits, and attribution. Every API response carries `retrieved_at`.

## Anti-hallucination guarantees

1. **Tools, not memories** — agents obtain facts exclusively through the typed tool registry (`app/tools/registry.py`); each tool validates inputs, logs calls, and returns typed results.
2. **Evidence chain** — every claim links evidence items; every evidence item links a source with retrieval time and confidence.
3. **Numeric fact-check** — the FactCheckerAgent re-verifies numbers inside claims against evidence payloads (`SUPPORTED / PARTIALLY_SUPPORTED / INSUFFICIENT_EVIDENCE`).
4. **Missing ≠ invented** — `safe_div` and explicit `None`s propagate; UI renders "unavailable".
5. **Contradictions shown, not resolved by fiat** — e.g., upward SMA trend vs negative sentiment is reported as a group with both sides.
6. **LLM is explainer, never calculator** — heuristic local provider (default) only rearranges facts from the data layer; external providers are sandboxed behind a system prompt that treats all external text as untrusted data.

## ML component — volatility regime classification

Rather than a fake "predict tomorrow's price" model, the platform classifies **volatility regimes** (LOW/NORMAL/ELEVATED/HIGH) from trailing features. Full methodology: [docs/METHODOLOGY.md](docs/METHODOLOGY.md). Key integrity rules (test-enforced):

- Labels = forward 20-day realized vol bucketed by **train-only** quartile thresholds.
- Chronological 60/20/20 split; no shuffling ever.
- Feature windows proven disjoint from label windows.
- Model selected on validation, reported once on test.

Measured (5y daily): AAPL GBM test acc 0.405 (majority baseline 0.522 — reported honestly); MSFT logreg 0.401 vs baseline 0.356.

## API

```
GET  /api/companies/search?q=          company search (SEC map + Yahoo fallback)
GET  /api/company/{symbol}             profile (name, sector, CIK, sources)
GET  /api/company/{symbol}/quote       live quote (60s cache; delayed ≤15 min — stamped)
GET  /api/company/{symbol}/market      daily OHLCV + retrieval time
GET  /api/company/{symbol}/fundamentals  SEC XBRL metrics + derived ratios
GET  /api/company/{symbol}/news        articles + sentiment (metadata only)
GET  /api/company/{symbol}/technical   indicators, signals, statistics, series
POST /api/research?symbol=&depth=      start async research job (quick/standard/deep)
GET  /api/research/{job_id}            status polling (fallback)
GET  /api/research/{job_id}/stream     REAL-TIME progress via Server-Sent Events
POST /api/compare                      A vs B (+C) side-by-side metrics
GET  /api/watchlist  POST  DELETE      watchlist CRUD
POST /api/documents/upload             RAG upload (.txt/.md/.pdf ≤10MB)
GET  /api/documents/search             vector retrieval with metadata filter
GET  /api/metrics                      observability counters/timers/events
GET  /api/sources/health               data-source health
GET  /api/health                       liveness
```

Interactive docs at `/api/docs` (Swagger). Rate limited (60 req/min default via slowapi).

## Database

SQLAlchemy 2.0 async + SQLite (aiosqlite). Entities and relations:

```
Company 1─n MarketData (unique: company+ts+freq, upsert on conflict)
Company 1─n FundamentalMetric (unique: company+key+period)
Company 1─n NewsArticle (unique: company+url_hash)
ResearchSession 1─1 ResearchReportDB (JSON report)
ResearchSession 1─n AgentRun
ResearchSession 1─n EvidenceRecord (claim ↔ source provenance)
DocumentChunk (RAG store: metadata + embedding)
WatchlistItem, ModelMetadataDB (MLOps lineage)
```

## Caching & cost control

TTL cache (in-process): market 15 min · fundamentals 12 h · news 30 min · company 24 h. Research dedup: identical (symbol, depth) report returned for 1 h. Bounded: news articles (10/20/30 by depth), max sources per report, LLM token caps, fixed agent set — no uncontrolled loops.

## Security

- Untrusted content (news, uploads, anything fetched) sanitized before storage/prompts; instruction-like text is flagged and wrapped as data, never executed.
- Symbol inputs regex-validated (`^[A-Z0-9.\-^]{1,12}$`).
- Uploads: extension allowlist, 10 MB cap, text extracted (pypdf for PDFs).
- Rate limiting, structured errors (no stack traces to clients), secrets only via env (`.env.example` provided; `.env` git-ignored).

## Observability

Structlog JSON logs; in-process metrics registry (source fetches/errors by status, cache hits/misses, tool calls, agent runs, LLM calls/tokens, HTTP latency) exposed at `/api/metrics`; every research job persists stage timeline + durations.

## Evaluation & testing

Real measurements only — [docs/METHODOLOGY.md](docs/METHODOLOGY.md):

| Component | Measured result |
|---|---|
| Sentiment (30 hand-labeled headlines) | accuracy 0.933; per-class F1 0.90–0.95 |
| RAG retrieval | precision@1 = 1.0 (4/4); metadata filter correct |
| Agentic run (real data, deterministic LLM) | 18/18 claims with evidence; unsupported-claim rate 0.0 |
| Agentic run (real data, real LLM via Groq) | 14/14 claims SUPPORTED; unsupported-claim rate 0.0 |
| ML regime classifier | see table above (baseline comparisons included) |
| User journey (deployed, 25 timed checks) | 25/25 PASS |
| Multi-company stress (8 tickers incl. IFRS filer) | 8/8 PASS |

**Tests: 89 passing** — data validation (symbols, timestamps, sanitization), calculations (indicators vs hand-computed values), ML leakage (4 dedicated temporal-integrity tests), agents (state, fact-checker classifications, failure recovery), API (ASGI-level, mocked sources, upload security), RAG (chunking, filtering, relevance).

```bash
cd backend && python -m pytest tests -q
```

## Project structure

```
ai-market-intelligence/
├── backend/
│   ├── app/
│   │   ├── api/            FastAPI routers (companies, watchlist, compare, documents, metrics)
│   │   ├── agents/         state, evidence utils, 7 specialized agents
│   │   ├── analytics/      technical indicators, sentiment, fundamental derivation
│   │   ├── core/           config (pydantic-settings), db, observability
│   │   ├── data/           validation, collectors (cache+persist), source clients
│   │   ├── models/         SQLAlchemy ORM entities
│   │   ├── retrieval/      embeddings, vector store (chunking+ingest+retrieve)
│   │   ├── schemas/        pydantic contracts
│   │   ├── services/       LLM provider abstraction, background jobs
│   │   ├── tools/          typed tool registry
│   │   ├── workflows/      orchestrator state machine
│   │   └── main.py
│   └── tests/             89 tests
├── ml/                     features, training, evaluation, inference, artifacts
├── evaluation/             sentiment/RAG/agentic evaluation framework
├── frontend/               React 18 + Vite + Recharts dashboard
├── scripts/                train + register pipeline
├── docs/                   DATA_SOURCES.md, METHODOLOGY.md
├── Dockerfile · docker-compose.yml · .env.example
```

## Setup

### Local (dev)

```bash
# backend
cd backend
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000          # http://localhost:8000/api/docs

# frontend
cd frontend
npm install && npm run dev                         # http://localhost:5173

# runs fully key-less with LLM_PROVIDER=heuristic (deterministic local synthesizer).
# Optional real LLM (free via Groq): see .env.example — set LLM_PROVIDER/LLM_API_KEY.
```

### Docker

```bash
docker compose up --build          # backend :8000, frontend :5173
```

### Deploy (free tier)

Render backend + Vercel frontend — full guide in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

### Verify

```bash
cd backend && python -m pytest tests -q             # 89 tests
python evaluation/run_evaluation.py                 # measured metrics
python scripts/train_and_register.py AAPL MSFT      # ML pipeline
```

## Usage

**Live demo:** frontend at [ai-market-intelligence-six.vercel.app](https://ai-market-intelligence-six.vercel.app) · backend API at [ai-market-intelligence-h07s.onrender.com/api/docs](https://ai-market-intelligence-h07s.onrender.com/api/docs) (free tier — sleeps after ~15 min idle; first request takes ~30s).

1. Search a company (e.g., "AAPL", "Microsoft", "RELIANCE.NS") — Overview loads profile, live quote, price chart, sentiment, event signals.
2. Tabs: Technical (indicators + charts + signals), Fundamentals (SEC FY metrics + trends), News (linked articles with per-article sentiment).
3. **Generate Research Report** → live pipeline trace → full report: executive summary, bull/bear cases with evidence, contradictions, risk dashboard, key unknowns, claim-verification trace, data freshness, source list.
4. Compare mode: A vs B on growth/profitability/leverage/volatility/sentiment with stated methodology.
5. Watchlist: save symbols with latest stored metrics.
6. Upload research documents (txt/md/pdf) → they enter the RAG store for retrieval.

## Limitations (honest list)

- Yahoo chart endpoint is unofficial — the platform is source-pluggable (`BaseClient`) but currently single-sourced for prices.
- Fundamentals: annual (FY) SEC facts only; IFRS filers not consumed; quarterly granularity not implemented.
- News: metadata + snippets only (licensing) — sentiment operates on short text.
- LLM provider optional; the default heuristic synthesizer produces formulaic (but honest) prose.
- SQLite — fine for the educational scope; swap `DATABASE_URL` for Postgres in production.
- ML regime model is a methodology demonstration; reported metrics are modest and stated as such.
- Comparison reports differences, not rankings; no valuation multiples (P/E etc. require market cap feed — deliberately omitted rather than approximated).

## Disclaimer

This software is an educational/research tool. It aggregates public data and presents analytical signals with uncertainty. It does **not** provide investment advice, trading signals, or predictions of future prices, and it makes no guarantees. Always consult a licensed professional before making financial decisions.

## Future improvements

- PostgreSQL + persistent vector store (pgvector) for multi-user scale.
- Quarterly XBRL frames + balance-sheet detail; IFRS taxonomy support.
- Async task queue (Celery/RQ/Arq) replacing in-process jobs for horizontal scale.
- Additional price sources with automatic cross-source contradiction detection.
- Fine-tuned finance-sentiment model with a larger labeled set; multilingual.
- Per-report caching with ETags; scheduled watchlist refresh jobs.
- LLM-as-judge + RAGAS-style faithfulness metrics in CI.

# Interview Prep — Deep-Dive Q&A for Every Component

Everything here is TRUE of this codebase — every answer maps to real files, real tests, and real measured numbers. Read `docs/BEGINNER_GUIDE.md` first for the plain-language overview.

---

## 1. Architecture & orchestration

**Q: How are the agents orchestrated?**
A: An explicit state machine (`WorkflowState` dataclass in `app/agents/state.py`) driven by `Orchestrator` (`app/workflows/orchestrator.py`). Fixed pipeline: collect → 4 data agents concurrently → risk agent (sequential — it reads the others' outputs) → fact-checker → synthesizer → persist. I chose a custom state machine over LangGraph deliberately: explicit control, zero extra dependency, and every state transition is testable. Agents exchange typed, bounded structures — never giant text blobs.

**Q: Why does the risk agent run separately?**
A: Real bug found by stress testing: when it ran concurrently with the data agents, it read empty state (the SEC fetch is slow) and produced false "no fundamentals" risks while MISSING real ones (e.g., TSLA's revenue decline). Two-phase orchestration fixed it — a great example of why concurrency requires explicit data dependencies.

---

## 2. Data pipeline

**Q: Walk me through the data flow.**
A: Collectors (`app/data/collectors.py`) → validation/normalization (`app/data/validation.py` — symbol regex, timestamp→UTC, HTML stripping, prompt-injection sanitization, dedup) → TTL cache → SQLite persistence (SQLAlchemy 2.0 async, WAL mode) → feature engineering (analytics + ML) → analysis.

**Q: What sources and why?**
A: SEC EDGAR (authoritative, keyless, 10 req/s with contact UA) for fundamentals via XBRL companyfacts; Yahoo Finance public chart API for daily OHLCV (unofficial — documented limitation, source-pluggable behind `BaseClient`); Google News RSS for news metadata + snippets only (licensing — no full-article scraping). Stooq was rejected during design (JS browser challenge). All documented in `docs/DATA_SOURCES.md`.

**Q: How do you handle XBRL tag aliases?**
A: Companies switch tags over time — e.g., Apple reports revenue as `RevenueFromContractWithCustomerExcludingAssessedTax` for recent years but older facts live under `Revenues`. My collector picks the alias with the most RECENT annual fact per concept (alias-priority selection). Also handles both fact shapes: duration facts (revenue/income, 330-380 day spans) vs instant facts (assets/equity, no start date).

**Q: How do you handle missing data?**
A: Never imputed. `safe_div` returns None on missing/zero inputs; the UI renders "unavailable"; foreign filers (IFRS — e.g., SAP) produce an honest "no SEC XBRL data" note. Verified by tests (`test_missing_data_not_fabricated`).

---

## 3. Temporal integrity (the non-negotiable)

**Q: How is time-series leakage prevented?**
A: Five rules, all enforced by dedicated tests (`backend/tests/test_ml_leakage.py`):
1. Feature rows are identical with or without future data present (verified by truncation test)
2. Rolling windows are trailing-only (verified against manual computation)
3. Labels use strictly future returns (t+1…t+20) — proven disjoint from feature windows
4. Chronological 60/20/20 split with the order asserted in code (`train.max() < val.min() < test.min()`) — never shuffled
5. Bucket thresholds and imputation fitted on TRAIN only

**Q: What is the ML task and why that one?**
A: Volatility regime classification (LOW/NORMAL/ELEVATED/HIGH) — forward 20-day realized vol bucketed by train-set quartiles. I deliberately avoided a fake "predict tomorrow's price" model. A regime classifier has a clean target, demonstrable leakage prevention, and honest evaluation. Measured results (5y daily): AAPL gradient boosting test accuracy 0.405 vs 0.522 majority baseline — it LOSES on accuracy, reported transparently; MSFT logistic regression 0.401 vs 0.356 baseline — it wins. Real numbers, not cherry-picked.

---

## 4. Technical + fundamental analytics

**Q: How are indicators computed?**
A: Pure Python, deterministic, unit-tested against hand-computed values (`test_calculations.py`): SMA (running sum, O(n)), EMA (α=2/(n+1), SMA-seeded), RSI(14) (Wilder smoothing), MACD (EMA12−EMA26, signal EMA9), Bollinger (SMA20 ± 2σ), ATR (Wilder true range). The LLM only EXPLAINS the computed numbers — it never calculates.

**Q: How are fundamentals derived?**
A: From SEC XBRL annual facts: net margin = net income/revenue, ROE = NI/equity, D/E = liabilities/equity, R&D intensity = R&D/revenue, YoY growth rates — each carries its fiscal period. Verified: Apple FY2025 revenue $416.2B (period 2025-09-27) straight from SEC filings.

---

## 5. Sentiment & events

**Q: How does sentiment work?**
A: Finance-tuned lexicon (~120 weighted terms) with negation window (3 tokens) and boosters/downtoners; score normalized to [−1,1]; label thresholds ±0.15. Measured: 93.3% accuracy on 30 hand-labeled finance headlines (per-class F1 0.90-0.95). Chosen over a transformer for determinism, zero cost, and testability — with limitations stated in the UI (snippet-only, English, no irony detection, not a return predictor).

**Q: How does event detection work?**
A: Rule-based keyword patterns over titles, categorized (earnings, M&A, leadership, product, regulatory, guidance) with a verification flag: "verified" = explicitly stated in a source title; "inferred" = pattern-only. False positives were caught by tests (e.g., "announces" no longer triggers the product category alone).

---

## 6. RAG & evidence

**Q: Describe the RAG architecture.**
A: Parse → sanitize (untrusted-content rules) → sentence-aware chunking (~120 words, 20-word overlap) → metadata (symbol, doc_type, source, date, URL) → hashed TF-IDF embeddings (256-dim, deterministic, keyless) → cosine retrieval with metadata prefilter (company filter mandatory — prevents cross-company contamination). Measured: precision@1 = 1.0 on labeled query→doc pairs; filter excludes other companies' docs (verified by test). Local embeddings are a documented trade-off: reproducible and keyless; OpenAI embeddings supported via config for quality.

**Q: How is source provenance maintained?**
A: Claim → evidence → source → retrieval time, persisted as rows (`EvidenceRecord`) and rendered in the UI's claim-verification trace. Every claim carries verification status (SUPPORTED / PARTIALLY_SUPPORTED / INSUFFICIENT_EVIDENCE). Measured: 0.0 unsupported-claim rate on real runs, in BOTH deterministic and real-LLM modes.

**Q: How do you handle contradictions?**
A: They're shown, not resolved by fiat — e.g., upward SMA trend vs negative news sentiment is reported as a contradiction group with both sides and an explanation (different lookbacks). Revenue-vs-earnings divergence >50% is flagged. Tests verify the contradiction groups.

---

## 7. Caching, performance & the great bug story

**Q: Explain your caching strategy.**
A: Three layers: (1) in-process TTL cache (market 1h — daily data, fundamentals 12h, news 30min, company 24h), (2) stale-while-revalidate — fundamentals serve persisted DB rows instantly and refresh in the background, (3) research dedup — identical (symbol, depth) reports served for 1h. Every response carries `retrieved_at` — stale data is never presented as live.

**Q: Tell me about a hard bug you found and fixed.**
A: Three good ones:
1. **Cache-hit-skips-persistence bug**: the TTL cache hit, but the 250-row DB upsert loop re-ran on EVERY request (~2s on throttled CPU). Cache-hit fast path now skips persistence entirely — repeat views went 2.7s → 0.5s.
2. **"Database is locked"**: parallel requests each held uncommitted SQLite write transactions across network fetches; WAL busy_timeout can't help stale-snapshot writers. Fix: commit immediately after every write section (invariant: no uncommitted writes cross a lock release), a write-serialization lock, WAL + busy_timeout=30s. Verified: the exact 5-parallel-request scenario went from 3 failures to zero.
3. **Black-screen crash**: the quote banner referenced React state not passed to its component — ReferenceError unmounted the whole tree, and `vite build` doesn't catch undefined variables. Fix: pass the prop + enforce ESLint no-undef in CI so the bug class can never ship again.

---

## 8. Security

**Q: How do you handle untrusted content and prompt injection?**
A: All external content (news, uploads) sanitized before storage/prompts (`sanitize_untrusted` — HTML stripped, control chars removed, instruction-like text flagged and wrapped as data). The system prompt states the assistant role and instructs treating DATA sections as untrusted. LLM output never overrides source evidence — the synthesizer references verified state only. Symbol inputs regex-validated; uploads allowlist + size cap; rate limiting (60 req/min); secrets via env only.

---

## 9. Testing & evaluation

**Q: What's tested?**
A: 95 tests: data validation (symbols, timestamps, sanitization), calculations (indicators vs hand-computed values), ML leakage (5 temporal-integrity tests), agents (state, fact-checker classifications, failure recovery, report contract: all sections present + no advice language), API (ASGI-level with mocked sources, CRUD, upload security, SSE generator), RAG (chunking, filtering, relevance).

**Q: How do you test agents without hitting APIs?**
A: Fake source clients + a deterministic HeuristicLLM (template-based, zero fabrication) injected via dependency seams; the full workflow runs offline in tests. Real-LLM mode is validated separately (measured 0.0 unsupported-claim rate).

**Q: What's evaluated?**
A: Real measurements only (`evaluation/run_evaluation.py`): sentiment 93.3% (30 hand-labeled), RAG precision@1 1.0, agentic unsupported-claim rate 0.0, ML baseline comparisons, deployed user-journey 25/25 timed checks, 8-ticker stress test including an IFRS filer.

---

## 10. Deployment & ops

**Q: How is it deployed?**
A: Free tier: Render (backend, uvicorn, health check) + Vercel (frontend static build, `VITE_API_BASE` baked at build time), CORS locked to the frontend origin, GitHub Actions CI (lint + tests + build on every push). Auto-deploy on push.

**Q: What are the honest limitations?**
A: Render free tier sleeps (~30s wake); SQLite single-writer (serialized in-process; Postgres for multi-worker); Yahoo endpoint unofficial; fundamentals annual-only (IFRS not consumed); news snippets only; LLM optional (deterministic fallback); research is LLM-bound (~3-12s by depth).

---

## Numbers to quote

| Metric | Value |
|---|---|
| Tests | 95 (CI-enforced) |
| Unsupported-claim rate | 0.0 (both LLM modes) |
| Sentiment accuracy | 93.3% (30 hand-labeled) |
| RAG precision@1 | 1.0 |
| Research latency (deployed) | quick ~3s · standard ~12s |
| Repeat-view latency | ~0.3s (SWR + cache) |
| LLM calls per standard research | ~8, token-capped |

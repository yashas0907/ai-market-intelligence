# Methodology & Evaluation

## 1. Deterministic calculations (no LLM math)

All quantitative outputs are computed in plain Python and unit-tested:

| Calculation | Definition | Tests |
|---|---|---|
| price change % | `(last − first) / first` over window | `test_calculations.py` |
| daily returns | `cₜ/cₜ₋₁ − 1` | ✓ |
| annualized volatility | `σ(daily returns) × √252 × 100` | ✓ |
| max drawdown | `min(cₜ/running_max − 1) × 100` | ✓ |
| SMA / EMA | rolling mean; EMA with α=2/(n+1), SMA-seeded | ✓ |
| RSI(14) | Wilder smoothing of gains/losses | ✓ |
| MACD | EMA12−EMA26, signal=EMA9 of MACD line | ✓ |
| Bollinger | SMA20 ± 2·σ(20) | ✓ |
| ATR(14) | Wilder-smoothed true range | ✓ |
| fundamentals ratios | net margin, ROE, D/E, R&D intensity — via `safe_div` returning `None` on missing inputs | ✓ |

The LLM (when configured) only **explains** computed numbers; it never produces them.

## 2. Sentiment engine

Finance-tuned lexicon (~120 weighted terms), negation window (3 tokens), boosters/downtoners.
Label rule: score > +0.15 → positive; < −0.15 → negative; else neutral.
Score normalized via `±(1 − 1/(1+|Σw|/3))` ∈ [−1, 1].

**Measured** on 30 hand-labeled finance headlines (10/10/10 pos/neu/neg):

```
accuracy 0.9333
per-class F1: positive 0.952, neutral 0.900, negative 0.947
```

Limitations (stated in the UI): lexicon method, no irony detection, snippet-only input, English only. Sentiment is an analytical signal — **not** claimed to predict returns.

## 3. RAG layer

Pipeline: parse → sanitize (untrusted-input rules) → sentence-aware chunking (~120 words, 20-word overlap) → metadata (symbol, doc_type, source, date, URL) → hashed TF-IDF embeddings (256-dim, deterministic, no key needed) → cosine retrieval with metadata prefilter.

**Measured** (labeled query→doc pairs):
- retrieval precision@1: **1.0 (4/4)**
- metadata filter correctly excludes other companies' documents: **true**

Local embeddings are intentionally simple and reproducible; OpenAI embeddings are supported via config for quality, local mode guarantees key-less reproducibility.

## 4. Agentic workflow — measured on real runs

From an actual AAPL research run in the DB (heuristic LLM provider):

```
total_claims            18
claims_with_evidence    18
unsupported_claim_rate  0.0
verification            17 SUPPORTED, 1 PARTIALLY_SUPPORTED
agents_ok               7/7
disclaimer_present      true
```

## 5. ML component — volatility regime classification

- **Target**: forward 20-day annualized realized volatility, bucketed LOW/NORMAL/ELEVATED/HIGH by **train-set** quartiles (q25/q75/q90).
- **Features** (all trailing windows): 1d/5d returns, 10/20/60d realized vol, 10d range ratio, 60d drawdown, volume z-score, volume trend.
- **Split**: strictly chronological 60/20/20 (train < validation < test in time; never shuffled).
- **Selection**: validation macro-F1; **reported once** on the test split.

**Measured** (5y daily data):

| Symbol | Selected model | Test accuracy | Majority baseline | Test macro-F1 |
|--------|---------------|---------------|-------------------|----------------|
| AAPL | gradient boosting | 0.4049 | 0.5223 | 0.2075 |
| MSFT | logistic regression | 0.4008 | 0.3563 | — |

Honest interpretation: regime classification is a genuinely hard 4-class temporal problem; AAPL's model **underperforms** the majority baseline on accuracy (reported transparently), MSFT beats it. These are real measurements, not cherry-picked. The component's value is demonstrating rigorous temporal methodology (leakage tests included in the suite), not maximizing accuracy.

### Leakage prevention (enforced by tests)

1. Feature rows identical with/without future data present (`test_no_future_leakage_in_features`).
2. Rolling windows verified trailing-only against manual computation.
3. Label window (t+1…t+20) proven disjoint from feature window (t−19…t).
4. Split chronology asserted (`train.max() < val.min() <= val.max() < test.min()`).
5. Bucket thresholds fitted on train, applied to val/test.
6. Imputation fitted inside the pipeline on train only.

## 6. Data quality monitoring

- Every collector records `retrieved_at`; the UI shows per-source freshness.
- Duplicate rows: market data deduped by (company, ts, freq) upsert; news by URL hash.
- Source failures: retried with backoff (2 retries, 1.5×), then surfaced as agent errors and as Data Quality Risks in the report — never silently swallowed.

## 7. Re-run evaluation yourself

```bash
python evaluation/run_evaluation.py              # sentiment + RAG + agentic
python evaluation/run_evaluation.py --sentiment-only
python scripts/train_and_register.py AAPL MSFT   # retrains + writes summaries
```

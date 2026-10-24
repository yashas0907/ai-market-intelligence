# Evaluation Results Snapshot

Measured on the final code (v1.0.0, 2026-09-29) — regenerate anytime with `python evaluation/run_evaluation.py`.

| Component | Metric | Result |
|---|---|---|
| Sentiment engine | Accuracy (30 hand-labeled finance headlines) | **93.33%** |
| Sentiment engine | Per-class F1 (positive / neutral / negative) | 0.952 / 0.900 / 0.947 |
| RAG retrieval | Precision@1 (4 labeled query→doc pairs) | **1.0** |
| RAG retrieval | Metadata filter excludes other companies | ✓ |
| Agentic workflow | Total claims (AAPL quick run, real sources) | 15 |
| Agentic workflow | Claims with evidence | **15/15** |
| Agentic workflow | **Unsupported-claim rate** | **0.0** |
| Agentic workflow | Verification distribution | 14 SUPPORTED, 1 PARTIALLY_SUPPORTED |
| Agentic workflow | Disclaimer present | ✓ |
| ML regime classifier (AAPL) | Test accuracy vs majority baseline | 0.405 vs 0.522 (reported honestly) |
| ML regime classifier (MSFT) | Test accuracy vs majority baseline | 0.401 vs 0.356 |
| Deployed user journey | 25 timed acceptance checks | 25/25 PASS |
| Multi-company stress | 8 tickers incl. IFRS foreign filer | 8/8 PASS |

Notes:
- Sentiment sample is hand-labeled by the author (10 pos / 10 neu / 10 neg) — small but real.
- The AAPL regime model underperforms its majority baseline; this is reported transparently rather than hidden (methodology demonstration, see docs/METHODOLOGY.md).
- Unsupported-claim rate = fraction of report claims with zero backing evidence. The architecture (evidence layer + fact-checker) keeps this at 0.0 in both deterministic and real-LLM modes.

# Beginner's Guide — Your Project, Start to Finish

This is YOUR project. This guide explains everything: what it is, how to run it, use it, update it, and manage it — written for a beginner.

---

## 1. What is this project? (plain language)

You built a **research platform for stock-market analysis**:

1. You type a company name (e.g., "Apple", "Reliance", "TCS").
2. The system collects REAL data from public sources: stock prices (Yahoo Finance), company financials from official SEC filings, and recent news headlines.
3. It computes everything with normal code (no AI guessing): price changes, volatility, moving averages, RSI, MACD, profit margins, debt ratios.
4. It analyzes news sentiment with a word-lexicon engine (deterministic, no API).
5. Seven specialized "agents" (small programs) each analyze one aspect — market, technicals, fundamentals, news, risk — then a fact-checker re-verifies every claim against the data, and a synthesizer writes the final report.
6. Every claim in the report is traceable: claim → evidence → source → retrieval time. Missing data is shown as "unavailable" — NEVER invented.

**It is NOT financial advice.** It's an educational/research tool (this is stated everywhere — keep it that way).

---

## 2. Where everything lives

| Thing | Where |
|---|---|
| Code (your repo) | `C:\Users\phata\OneDrive\Documents\Default Project\ai-market-intelligence` |
| GitHub | https://github.com/yashas0907/ai-market-intelligence |
| Live website (frontend) | https://ai-market-intelligence-six.vercel.app |
| Live API (backend) | https://ai-market-intelligence-h07s.onrender.com/api/docs |
| Your Groq key | `backend\.env` (local) + Render dashboard (never in the repo) |

---

## 3. Run it on YOUR computer (local setup)

**One-time setup:**

```
Step 1:  Open PowerShell in the project folder:
         cd "C:\Users\phata\OneDrive\Documents\Default Project\ai-market-intelligence"

Step 2:  Backend — create Python environment (first time only):
         cd backend
         python -m venv .venv
         .venv\Scripts\activate
         pip install -r requirements.txt -r requirements-dev.txt

Step 3:  Frontend — install JavaScript packages (first time only):
         cd ..\frontend
         npm install
```

**Every time you want to run it:**

```
Terminal 1 (backend):   cd backend
                        .venv\Scripts\activate
                        uvicorn app.main:app --port 8000

Terminal 2 (frontend):  cd frontend
                        npm run dev
```

Then open **http://localhost:5173** in your browser. That's it — it works without any API key (the built-in local synthesizer writes the reports). Your Groq key is already in `backend\.env`, so the local version uses the real LLM too.

---

## 4. How to USE it (user flow)

1. **Search** — type a company (AAPL, MSFT, RELIANCE.NS, TCS.NS…). A dropdown appears; click the right one.
   - US tickers resolve instantly; Indian stocks appear with `.NS` (National Stock Exchange) or `.BO` (Bombay).
2. **Overview tab** — company profile, live quote (auto-refreshes every 60s), price chart, sentiment summary, event signals.
3. **Technical tab** — moving averages, Bollinger bands, RSI, MACD charts + plain-language signals.
4. **Fundamentals tab** — revenue, net income, EPS, margins, ROE, debt/equity — from real SEC 10-K filings, each with its fiscal period.
5. **News tab** — recent linked articles with sentiment badges.
6. **Generate Research Report** — pick depth (Quick ≈ 7s / Standard ≈ 12s / Deep) → watch the live progress trace → get the full cited report: executive summary, bull case, bear case, risks, contradictions, key unknowns, claim-verification trace, sources.
7. **Compare** — pick 2-3 companies → side-by-side metrics with the methodology stated.
8. **Watchlist** — add companies to save them (background refresher keeps their data fresh every 15 min).

---

## 5. How to UPDATE the project (the normal developer flow)

Whenever you (or anyone) changes code:

```
Step 1:  Make your changes to the files.

Step 2:  Test before pushing:
         cd backend
         .venv\Scripts\activate
         python -m pytest tests -q          (should say: 91 passed)
         python -m ruff check app --select E9,F,W605   (should say: All checks passed!)

         cd ..\frontend
         npm run lint                        (should exit clean)
         npm run build                       (should say: built in Xs)

Step 3:  Push to GitHub:
         git add -A
         git commit -m "describe what you changed"
         git push

Step 4:  Done — GitHub Actions (CI) re-runs all tests automatically,
         Render re-deploys the backend automatically (~4 min),
         Vercel re-deploys the frontend automatically (~2 min).
```

**Golden rule:** if tests fail, DON'T push. Fix first. (CI will also block/flag broken pushes.)

---

## 6. Recommended: move the backend to Singapore (faster for you)

Your backend is in the US; from India every click has ~0.3-0.5s network delay baked in. Moving it to Singapore makes everything feel 2-3× faster:

```
Step 1:  Render dashboard → "New +" → "Web Service" → connect the same repo.
Step 2:  Settings (same as before): Language Python 3, Root Directory: backend,
         Build: pip install -r requirements.txt,
         Start: uvicorn app.main:app --host 0.0.0.0 --port $PORT, Instance: Free.
Step 3:  "Advanced" → Region → **Singapore**.
Step 4:  Environment variables (same 5 as before):
         LLM_PROVIDER=openai_compatible
         LLM_BASE_URL=https://api.groq.com/openai/v1
         LLM_MODEL=openai/gpt-oss-120b
         LLM_API_KEY=<your Groq key>
         CORS_ORIGINS=["https://ai-market-intelligence-six.vercel.app"]
Step 5:  Create → note the new URL (…onrender.com).
Step 6:  Vercel → your project → Settings → Environment Variables →
         change VITE_API_BASE to <new-render-url>/api → redeploy.
Step 7:  Delete the old US service (optional, avoids confusion).
```

---

## 7. Managing your Groq API key

- The key lives in **two places**: `backend\.env` (local) and the **Render dashboard** (Environment tab). NEVER in the code or the repo.
- It was pasted into a chat once — best practice: regenerate it at **console.groq.com → API Keys** (2 min), then update both places.
- Free tier limits are generous for this project (each research run makes ~1-8 LLM calls).
- If reports ever say the deterministic fallback text ("Summary of verified data points…"), the LLM call failed — check the key/env vars on Render's Logs tab.

---

## 8. Troubleshooting (the common ones)

| Problem | Cause | Fix |
|---|---|---|
| Site shows skeletons for ~30s then loads | Render free tier sleeps after 15 min idle | Normal — wait; or ping the backend URL first |
| "backend may be waking up" error | Same — cold start | Retry in ~30s |
| First view of a company takes ~2-3s | Live fetch per company per cache window | Normal — repeats are fast (~0.5s) |
| Search dropdown keeps old results | Browser cache | Hard refresh: Ctrl + Shift + R |
| Port 8000 already in use (local) | An old uvicorn is running | `Get-NetTCPConnection -LocalPort 8000 \| Select OwningProcess` → `Stop-Process -Id <pid> -Force`, or use another port |
| CI red on GitHub | Tests/lint failed | Click the failing run → read the step → fix locally → push again |
| Report has no LLM prose | Groq key missing/failed | Render → Logs tab → check for LLM errors; verify env vars |

---

## 9. How to explain this project in an interview (60-second version)

> "I built a market-intelligence platform that produces evidence-backed research reports. The core principle: LLMs never calculate or invent — they only explain. All numbers come from real sources: SEC EDGAR XBRL for fundamentals, Yahoo Finance for prices, Google News RSS for news metadata. A multi-layer pipeline validates, normalizes, caches (stale-while-revalidate), and persists the data. Seven specialized agents run on an explicit state machine — a fact-checker re-verifies every numeric claim against its evidence, and the synthesizer can only reference verified state. The ML component classifies volatility regimes with strictly chronological splits and leakage-prevention tests. Everything is tested (91 tests, CI-enforced), observable (metrics endpoint), rate-limited, and deployed free on Render + Vercel with real-time SSE progress streaming."

**Deep-dive talking points:**
- Why SEC EDGAR first (authoritative, keyless) and how XBRL tag aliases are handled (companies switch tags; alias-priority picks the most recent)
- How time-series leakage is prevented (labels use future 20-day returns only; features trailing-only; chronological 60/20/20 split; test-enforced)
- Why a custom state machine over LangGraph (explicit, testable, zero extra dependency)
- How caching works (TTL + SWR + persistence-on-fresh-fetch only — the cache-hit-skips-persistence bug and its fix is a GREAT story)
- How contradictions are shown, not resolved by fiat
- The honest-ML story: reported metrics include where the model LOSES to a majority baseline

---

## 10. Project stats (as of today)

- 91 backend tests + frontend ESLint gate — all passing in CI
- 25-check deployed user-journey test + 8-ticker stress test (in `scripts/`)
- 16 API endpoints + Swagger docs at `/api/docs`
- Evaluation: sentiment 93.3% accuracy (30 hand-labeled headlines) · RAG precision@1 = 1.0 · 0.0 unsupported-claim rate
- Every claim traceable: claim → evidence → source → retrieval time

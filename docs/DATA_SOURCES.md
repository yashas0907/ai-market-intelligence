# Data Sources

All sources verified accessible at build time (2026-09-02). No keys required; no scraping of pages that forbid it.

| # | Source | API / Endpoint | Data | Update freq (source) | Rate limits | Attribution | Limitations |
|---|--------|-----------------|------|---------------------|-------------|-------------|-------------|
| 1 | SEC EDGAR — company tickers | `https://www.sec.gov/files/company_tickers.json` | ticker→CIK map, company names | daily (by SEC) | 10 req/s policy; UA with contact required | U.S. SEC public data | U.S. SEC registrants only |
| 2 | SEC EDGAR — XBRL company facts | `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` | annual/quarterly reported financial facts (revenue, net income, EPS, assets, liabilities, equity, R&D, operating cash flow) | as filings arrive | 10 req/s; UA policy | U.S. SEC | only concepts tagged in us-gaap taxonomy; tag aliases change over time (handled by alias-priority selection); foreign filers may use IFRS (not consumed) |
| 3 | SEC EDGAR — submissions | `https://data.sec.gov/submissions/CIK{cik}.json` | registrant profile (name, exchanges, SIC sector/industry) | as filings arrive | 10 req/s | U.S. SEC | metadata only; SIC codes are coarse industry groupings |
| 4 | Yahoo Finance public chart API | `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}` | daily OHLCV (split-adjusted `adjclose`), currency, exchange | market hours | unofficial; UA required; may throttle | Yahoo Finance | **unofficial endpoint** — no SLA, may break; not licensed for redistribution; some tickers lack adjusted series |
| 5 | Yahoo Finance public search | `https://query1.finance.yahoo.com/v1/finance/search` | company name→symbol resolution (fallback path) | n/a | unofficial | Yahoo Finance | same unofficial-endpoint caveats |
| 6 | Google News RSS | `https://news.google.com/rss/search?q=...` | article **metadata only**: title, source publisher, link, publication time, short snippet | continuous | unauthenticated RSS | Google News + respective publishers | snippet is a truncated excerpt; full text NOT retrieved (licensing); coverage skews toward sources Google indexes; English/US edition |

## Rejected during design

- **Stooq CSV** (`stooq.com/q/d/l/`): returns a JavaScript browser-verification challenge to non-browser clients — unreliable for automated pipelines.
- **Alpha Vantage / FMP / Finnhub**: require API keys with tight free quotas; intentionally avoided so the platform runs key-less for evaluation.
- **Paginated news scraping**: violates publishers' terms; replaced by RSS metadata + snippet-only sentiment (documented limitation).

## Fallback chain

Company resolution: SEC ticker map (authoritative) → Yahoo search (if SEC miss).
Market data: Yahoo chart (only price source — documented as unofficial; platform is source-pluggable behind `BaseClient`).

## Attribution requirements

- SEC data: U.S. Securities and Exchange Commission (public domain / EDGAR).
- Market data: "Market data from Yahoo Finance" wherever charts are shown in production contexts.
- News: publisher name + outbound link displayed with every article (implemented in the UI).

## Honesty rules implemented in code

- Missing fundamentals → reported as `unavailable`, never estimated (`derive_fundamentals` returns `None`).
- Every response carries `retrieved_at` (UTC) and the source name.
- News sentiment operates on **snippets only** — labeled as such everywhere.

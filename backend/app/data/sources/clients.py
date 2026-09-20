from datetime import datetime, timezone
from typing import Any

import feedparser
from app.core.observability import obs
from app.data.sources.base_client import BaseClient, SourceError
from app.data.validation import normalize_symbol, parse_iso_utc, sanitize_untrusted, ts_to_utc
from app.schemas.common import PricePoint


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

_YAHOO_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

_RANGES = {
    ("1d", "1d"), ("5d", "1d"), ("1mo", "1d"), ("3mo", "1d"), ("6mo", "1d"), ("1y", "1d"), ("2y", "1d"), ("5y", "1d"), ("max", "1d"),
}


class YahooMarketClient(BaseClient):
    """Daily OHLCV market data via Yahoo Finance public chart API.

    Notes:
      - Unofficial public endpoint; may require UA spoofing; not for redistribution.
      - Prices are split-adjusted (adjclose); currency from meta.
    """

    source_name = "yahoo_chart"

    def __init__(self):
        super().__init__(user_agent=_YAHOO_UA)

    async def get_chart(self, symbol: str, range_: str = "1y", interval: str = "1d") -> dict[str, Any]:
        symbol = normalize_symbol(symbol)
        if (range_, interval) not in _RANGES:
            raise ValueError(f"unsupported range/interval: {range_}/{interval}")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        data = await self._fetch_json(url, params={"range": range_, "interval": interval, "events": "div,split"})
        try:
            result = data["chart"]["result"][0]
            err = data["chart"].get("error")
        except (KeyError, IndexError, TypeError):
            err = data.get("chart", {}).get("error") or "malformed response"
            raise SourceError(self.source_name, f"no chart result for {symbol}: {str(err)[:150]}")
        ts = result.get("timestamp") or []
        quote = (result.get("indicators") or {}).get("quote", [{}])[0]
        adj = ((result.get("indicators") or {}).get("adjclose") or [{}])[0]
        adjcloses = adj.get("adjclose") if isinstance(adj, dict) else None
        points: list[PricePoint] = []
        for i, t in enumerate(ts):
            def g(field: str, idx: int = i) -> float | None:
                vals = quote.get(field) or []
                v = vals[idx] if idx < len(vals) else None
                return float(v) if v is not None else None

            ac = None
            if isinstance(adjcloses, list) and i < len(adjcloses) and adjcloses[i] is not None:
                ac = float(adjcloses[i])
            close = g("close")
            if close is None:
                continue
            points.append(
                PricePoint(
                    ts=ts_to_utc(t),
                    open=g("open"),
                    high=g("high"),
                    low=g("low"),
                    close=close,
                    adjclose=ac if ac is not None else close,
                    volume=float(v) if (v := g("volume")) is not None else None,
                )
            )
        points = [p for p in points if p.ts is not None]
        if not points:
            raise SourceError(self.source_name, f"no valid points for {symbol}")
        meta = result.get("meta", {})
        return {
            "symbol": symbol,
            "currency": meta.get("currency"),
            "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
            "instrument_type": meta.get("instrumentType"),
            "regular_market_price": meta.get("regularMarketPrice"),
            "regular_market_time": ts_to_utc(meta.get("regularMarketTime")),
            "chart_previous_close": meta.get("chartPreviousClose"),
            "points": points,
            "retrieved_at": utcnow(),
        }

    def health_check(self) -> dict[str, Any]:
        return {"source": self.source_name, "ok": True}


class YahooSearchClient(BaseClient):
    """Company search via Yahoo Finance public search endpoint (unofficial)."""

    source_name = "yahoo_search"

    def __init__(self):
        super().__init__(user_agent=_YAHOO_UA)

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = sanitize_untrusted(query, max_len=100).replace("\n", " ")
        if not q:
            return []
        url = "https://query1.finance.yahoo.com/v1/finance/search"
        data = await self._fetch_json(url, params={"q": q, "quotes_count": min(limit, 20), "news_count": 0})
        hits: list[dict[str, Any]] = []
        for item in (data.get("quotes") or [])[:limit]:
            symbol = (item.get("symbol") or "").upper()
            if not symbol:
                continue
            hits.append(
                {
                    "symbol": symbol,
                    "name": (item.get("shortname") or item.get("longname") or symbol).strip()[:200],
                    "exchange": item.get("exchDisp") or item.get("exchange"),
                    "type": item.get("quoteType"),
                    "score": 1.0,
                }
            )
        return hits

    def health_check(self) -> dict[str, Any]:
        return {"source": self.source_name, "ok": True}


class SecClient(BaseClient):
    """SEC EDGAR — official, open data. Company facts via XBRL frames/companyfacts."""

    source_name = "sec_edgar"

    async def get_ticker_map(self) -> list[dict[str, Any]]:
        url = "https://www.sec.gov/files/company_tickers.json"
        data = await self._fetch_json(url)
        rows = []
        for _, rec in data.items():
            rows.append(
                {
                    "cik": int(rec["cik_str"]),
                    "cik_padded": str(rec["cik_str"]).zfill(10),
                    "symbol": rec["ticker"].upper(),
                    "name": rec["title"][:200],
                }
            )
        return rows

    async def get_companyfacts(self, cik_padded: str) -> dict[str, Any]:
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"
        return await self._fetch_json(url)

    async def get_submissions(self, cik_padded: str) -> dict[str, Any]:
        url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
        return await self._fetch_json(url)

    def health_check(self) -> dict[str, Any]:
        return {"source": self.source_name, "ok": True}


class GoogleNewsRssClient(BaseClient):
    """News metadata via Google News RSS (public search feeds).

    We keep only: title, source, URL, publication time, retrieved time, snippet.
    Snippet text comes from the feed itself (short promotional excerpt).
    """

    source_name = "google_news_rss"

    async def fetch_feed(self, symbol: str, company_name: str, limit: int = 20) -> list[dict[str, Any]]:
        query = f"{symbol} OR \"{company_name}\" stock"
        url = "https://news.google.com/rss/search"
        params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        raw = await self._fetch_xml(url, params=params)
        obs.incr("source_response", source=self.source_name, status="200")
        parsed = feedparser.parse(raw)
        items = []
        for entry in parsed.entries[: max(1, limit)]:
            title = sanitize_untrusted(getattr(entry, "title", "") or "", max_len=300)
            link = getattr(entry, "link", "") or ""
            source = getattr(getattr(entry, "source", None), "title", None) or "Google News"
            published = parse_iso_utc(getattr(entry, "published", None)) or ts_to_utc(
                getattr(getattr(entry, "published_parsed", None), "tm_hour", None)
            )
            if published is None and hasattr(entry, "published_parsed") and entry.published_parsed:
                import time as _t

                published = ts_to_utc(_t.mktime(entry.published_parsed))
            summary_html = getattr(entry, "summary", "") or ""
            summary = sanitize_untrusted(summary_html, max_len=700)
            if not title or not link:
                continue
            items.append(
                {
                    "title": title,
                    "source": str(source)[:120],
                    "url": link[:1000],
                    "published_at": published,
                    "retrieved_at": utcnow(),
                    "summary": summary,
                }
            )
        return items

    def health_check(self) -> dict[str, Any]:
        return {"source": self.source_name, "ok": True}

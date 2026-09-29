import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import get_settings
from app.core.db import db_write_lock
from app.core.observability import logger, obs
from app.data.sources.clients import (
    GoogleNewsRssClient,
    SecClient,
    YahooMarketClient,
    YahooSearchClient,
)
from app.data.validation import normalize_symbol, parse_iso_utc, sanitize_untrusted, url_hash
from app.models import Company, FundamentalMetric, MarketData, NewsArticle

_yahoo_market = YahooMarketClient()
_yahoo_search = YahooSearchClient()
_sec = SecClient()
_news = GoogleNewsRssClient()

_TTL_CACHE: dict[str, tuple[Any, float]] = {}
_CACHE_LOCK = asyncio.Lock()


def cache_get(key: str) -> Any | None:
    entry = _TTL_CACHE.get(key)
    if not entry:
        return None
    value, expires_at = entry
    if time.time() > expires_at:
        _TTL_CACHE.pop(key, None)
        obs.incr("cache_miss")
        return None
    obs.incr("cache_hit")
    return value


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    _TTL_CACHE[key] = (value, time.time() + ttl_seconds)


def cache_delete_prefix(prefix: str) -> None:
    for k in [k for k in _TTL_CACHE if k.startswith(prefix)]:
        _TTL_CACHE.pop(k, None)


async def cached(key: str, ttl: int, factory):
    hit = cache_get(key)
    if hit is not None:
        return hit
    value = await factory()
    cache_set(key, value, ttl)
    return value


async def get_company_by_symbol(session, symbol: str) -> Company | None:
    symbol = normalize_symbol(symbol)
    result = await session.execute(select(Company).where(Company.symbol == symbol))
    return result.scalar_one_or_none()


async def upsert_company(session, symbol: str, name: str, exchange: str | None = None, sector: str | None = None, industry: str | None = None, country: str | None = None, cik: str | None = None) -> Company:
    symbol = normalize_symbol(symbol)
    company = await get_company_by_symbol(session, symbol)
    if company is None:
        company = Company(symbol=symbol, name=name, exchange=exchange, sector=sector, industry=industry, country=country, cik=cik)
        session.add(company)
    else:
        company.name = name or company.name
        company.exchange = exchange or company.exchange
        company.sector = sector or company.sector
        company.industry = industry or company.industry
        company.country = country or company.country
        company.cik = cik or company.cik
        company.updated_at = datetime.now(timezone.utc)
    await session.flush()
    # COMMIT immediately: an uncommitted INSERT holds the SQLite write lock across
    # await boundaries (network fetches, lock release) — blocking every other writer.
    await session.commit()
    return company


async def get_live_quote(symbol: str) -> dict[str, Any]:
    """Live-ish quote (60s cache). Source may delay quotes up to 15 minutes — labeled."""
    symbol = normalize_symbol(symbol)
    chart = await cached(f"yahoo:quote:{symbol}", 60, lambda: _yahoo_market.get_chart(symbol, "1d"))
    price = chart.get("regular_market_price")
    prev = chart.get("chart_previous_close")
    change = ((price - prev) / prev * 100) if (price is not None and prev) else None
    return {
        "symbol": symbol,
        "price": price,
        "change_pct": round(change, 2) if change is not None else None,
        "currency": chart.get("currency"),
        "exchange": chart.get("exchange"),
        "as_of": chart.get("regular_market_time").isoformat() if chart.get("regular_market_time") else None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "note": "Quote may be delayed up to 15 minutes (source limitation) — stamped with retrieval time.",
    }


async def search_companies(query: str, limit: int = 10) -> list[dict[str, Any]]:
    s = get_settings()
    query = sanitize_untrusted(query, max_len=100)
    if not query:
        return []

    ticker_map = await cached("sec:ticker_map", s.cache_company_ttl, _sec.get_ticker_map)
    ql = query.lower()
    hits: list[dict[str, Any]] = []
    for row in ticker_map:
        name_l = row["name"].lower()
        sym_l = row["symbol"].lower()
        if ql == sym_l:
            score = 3.0
        elif sym_l.startswith(ql):
            score = 2.5
        elif name_l == ql:
            score = 2.4
        elif ql in name_l:
            score = 2.0
        elif name_l.startswith(ql):
            score = 1.5
        else:
            continue
        hits.append({"symbol": row["symbol"], "name": row["name"], "exchange": "SEC", "cik": row["cik_padded"], "score": score})

    # Merge Yahoo search hits ONLY when no SEC hit strongly matches the query
    # (exact/prefix symbol). International listings (.NS/.BO/.L) are absent from
    # the SEC map, so their queries still reach Yahoo — but common US ticker
    # searches skip the extra network round-trip entirely.
    has_strong_sec_match = any(h["score"] >= 2.5 for h in hits)
    if not has_strong_sec_match:
        try:
            yahoo_hits = await _yahoo_search.search(query, limit)
        except Exception:
            yahoo_hits = []
        for h in yahoo_hits:
            if any(x["symbol"] == h["symbol"] for x in hits):
                continue
            sym_l = h["symbol"].lower()
            name_l = (h.get("name") or "").lower()
            if ql == sym_l:
                score = 3.0
            elif sym_l.startswith(ql):
                score = 2.5
            elif ql in name_l:
                score = 2.0
            else:
                score = 1.0
            hits.append({"symbol": h["symbol"], "name": h.get("name") or h["symbol"], "exchange": h.get("exchange"), "cik": None, "score": score})

    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


# SEC SIC division ranges → human-readable sector names
_SIC_DIVISIONS = [
    (100, 999, "Agriculture & Mining"),
    (1000, 1499, "Oil & Gas Extraction"),
    (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"),
    (4000, 4999, "Transportation & Utilities"),
    (5000, 5199, "Wholesale Trade"),
    (5200, 5999, "Retail Trade"),
    (6000, 6799, "Finance & Insurance"),
    (7000, 8999, "Services"),
    (9100, 9729, "Public Administration"),
    (9900, 9999, "Other"),
]


def _sic_division_to_sector(sic_code) -> str | None:
    try:
        code = int(sic_code)
    except (TypeError, ValueError):
        return None
    for lo, hi, name in _SIC_DIVISIONS:
        if lo <= code <= hi:
            return name
    return None


async def resolve_company_profile(session, symbol: str) -> dict[str, Any]:
    """Resolve company profile: try Yahoo quote first, fallback to SEC ticker map + submissions."""
    s = get_settings()
    symbol = normalize_symbol(symbol)

    profile: dict[str, Any] = {"symbol": symbol, "name": symbol, "exchange": None, "sector": None, "industry": None, "country": None, "cik": None, "sources": []}

    ticker_map = await cached("sec:ticker_map", s.cache_company_ttl, _sec.get_ticker_map)
    sec_match = next((r for r in ticker_map if r["symbol"] == symbol), None)
    if sec_match:
        profile["cik"] = sec_match["cik_padded"]
        profile["name"] = sec_match["name"]
        profile["sources"].append("SEC company_tickers.json")

    try:
        chart = await cached(f"yahoo:chart:{symbol}:1mo", s.cache_market_ttl, lambda: _yahoo_market.get_chart(symbol, "1mo"))
        profile["currency"] = chart.get("currency")
        profile["exchange"] = chart.get("exchange")
        if sec_match is None:
            profile["name"] = symbol
    except Exception as exc:
        logger.warning("collector.profile_yahoo_fail", symbol=symbol, error=str(exc)[:150])

    if profile["cik"]:
        try:
            subs = await cached(f"sec:submissions:{profile['cik']}", s.cache_fundamentals_ttl, lambda: _sec.get_submissions(profile["cik"]))
            profile["name"] = subs.get("name") or profile["name"]
            exchanges = subs.get("exchanges") or []
            if exchanges:
                profile["exchange"] = profile["exchange"] or exchanges[0]
            # submissions API: `sic` (numeric code) + `sicDescription`
            # ("Services-Prepackaged Software", "Semiconductors & Related Devices", ...)
            # Map SIC division → sector for consistent UX; keep description as industry.
            sic_desc = subs.get("sicDescription") or ""
            if sic_desc:
                profile["industry"] = sic_desc
                profile["sector"] = _sic_division_to_sector(subs.get("sic"))
            profile["category"] = subs.get("category")
            profile["fiscal_year_end"] = subs.get("fiscalYearEnd")
            profile["description"] = subs.get("description")
            profile["sources"].append("SEC submissions API")
        except Exception as exc:
            logger.warning("collector.profile_sec_fail", symbol=symbol, error=str(exc)[:150])

    async with db_write_lock:
        await upsert_company(
            session,
            symbol=profile["symbol"],
            name=profile["name"],
            exchange=profile["exchange"],
            sector=profile.get("sector"),
            industry=profile.get("industry"),
            country=profile.get("country"),
            cik=profile.get("cik"),
        )
    profile["retrieved_at"] = datetime.now(timezone.utc)
    return profile


async def collect_market_data(session, symbol: str, range_: str = "1y") -> dict[str, Any]:
    s = get_settings()
    symbol = normalize_symbol(symbol)
    cache_key = f"yahoo:chart:{symbol}:{range_}"
    hit = cache_get(cache_key)
    if hit is not None:
        # Fast path: serve the cached chart and SKIP the DB persistence loop
        # (250 individual upserts ≈ 2s on throttled free-tier CPU — must not
        # re-run on every request).
        return hit
    chart = await _yahoo_market.get_chart(symbol, range_)
    async with db_write_lock:
        company = await get_company_by_symbol(session, symbol)
        if company is None:
            company = await upsert_company(session, symbol=symbol, name=symbol)

        rows_written = 0
        seen_ts: set[datetime] = set()
        for p in chart["points"]:
            if p.ts in seen_ts:
                obs.incr("pipeline_duplicate_skipped", source="yahoo")
                continue
            seen_ts.add(p.ts)
            stmt = (
                sqlite_insert(MarketData)
                .values(company_id=company.id, ts=p.ts, frequency="1d", open=p.open, high=p.high, low=p.low, close=p.close, adjclose=p.adjclose, volume=p.volume, source="yahoo", retrieved_at=datetime.now(timezone.utc))
                .on_conflict_do_update(
                    index_elements=["company_id", "ts", "frequency"],
                    set_={"open": p.open, "high": p.high, "low": p.low, "close": p.close, "adjclose": p.adjclose, "volume": p.volume, "retrieved_at": datetime.now(timezone.utc)},
                )
            )
            await session.execute(stmt)
            rows_written += 1
        await session.commit()
    logger.info("collector.market_data", symbol=symbol, rows=rows_written, range=range_)
    result = {"symbol": symbol, "points": chart["points"], "currency": chart.get("currency"), "exchange": chart.get("exchange"), "retrieved_at": chart["retrieved_at"], "rows_written": rows_written}
    cache_set(cache_key, result, s.cache_market_ttl)
    return result


# ---- Fundamentals from SEC XBRL ----

_CONCEPTS = [
    ("revenue", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]),
    ("net_income", ["NetIncomeLoss"]),
    ("eps_diluted", ["EarningsPerShareDiluted"]),
    ("eps_basic", ["EarningsPerShareBasic"]),
    ("total_assets", ["Assets"]),
    ("total_liabilities", ["Liabilities", "LiabilitiesAndStockholdersEquity"]),
    ("stockholders_equity", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    ("cash_and_equivalents", ["CashAndCashEquivalentsAtCarryingValue"]),
    ("operating_income", ["OperatingIncomeLoss"]),
    ("research_development", ["ResearchAndDevelopmentExpense"]),
    ("long_term_debt", ["LongTermDebtNoncurrent", "LongTermDebt"]),
    ("free_cash_flow_proxy", ["NetCashProvidedByUsedInOperatingActivities"]),
]

_GAAP_MAP = {alt: key for key, alts in _CONCEPTS for alt in alts}


def _annual_only(units: dict[str, list[dict[str, Any]]]) -> dict[str, dict[int, dict[str, Any]]]:
    """Latest annual fact per concept per fiscal year.

    Handles BOTH fact shapes in XBRL companyfacts:
    - duration facts (revenue, income): require start+end spanning 330-380 days (a fiscal year)
    - instant facts (assets, liabilities, equity): no 'start'; take the FY-end instant whose
      'end' matches the fiscal-year end (dedup by latest filing)
    """
    out: dict[str, dict[int, dict[str, Any]]] = {}
    for unit, facts in units.items():
        for fact in facts:
            end = parse_iso_utc(fact.get("end"))
            val = fact.get("val")
            fy = fact.get("fy")
            fp = fact.get("fp")
            if end is None or val is None or fp != "FY" or not fy:
                continue
            fy = int(fy)
            start = parse_iso_utc(fact.get("start"))
            if start is not None:
                days = (end - start).days
                if not (330 <= days <= 380):
                    continue
            # instant facts: keep as-is (dated at fiscal year end)
            existing = out.get(unit, {}).get(fy)
            if existing and existing.get("filed") and existing["filed"] > parse_iso_utc(fact.get("filed")):
                continue
            out.setdefault(unit, {})[fy] = {"val": float(val), "end": end, "filed": parse_iso_utc(fact.get("filed")), "form": fact.get("form"), "frame": fact.get("frame")}
    return out


async def collect_fundamentals(session, symbol: str) -> dict[str, Any]:
    """Stale-while-revalidate: TTL cache → persisted DB metrics (if fresh, with
    background refresh) → synchronous SEC fetch. Never fabricates values."""
    s = get_settings()
    symbol = normalize_symbol(symbol)

    cache_key = f"fund:result:{symbol}"
    hit = cache_get(cache_key)
    if hit is not None:
        return hit

    company = await get_company_by_symbol(session, symbol)
    if company is None or not company.cik:
        # resolve the profile in a FRESH session: its writes commit immediately
        # (upsert_company commits) and never sit uncommitted on this request's
        # session across the heavy SEC companyfacts fetch (SQLite write-lock hazard)
        from app.core.db import SessionLocal

        async with SessionLocal() as fresh:
            await resolve_company_profile(fresh, symbol)
        company = await get_company_by_symbol(session, symbol)
    if company is None or not company.cik:
        result = {"symbol": symbol, "metrics": [], "retrieved_at": datetime.now(timezone.utc), "error": "no SEC CIK available for this ticker"}
        cache_set(cache_key, result, 600)
        return result

    rows = (await session.execute(select(FundamentalMetric).where(FundamentalMetric.company_id == company.id))).scalars().all()
    newest = max((r.retrieved_at for r in rows), default=None)
    if rows and newest and (datetime.now(timezone.utc) - newest).total_seconds() < s.cache_fundamentals_ttl:
        metrics = [
            {"metric_key": r.metric_key, "value": r.value, "unit": r.unit, "fiscal_year": r.fiscal_year, "period": r.period, "source": r.source}
            for r in rows
        ]
        result = {"symbol": symbol, "metrics": metrics, "retrieved_at": newest, "error": None}
        cache_set(cache_key, result, s.cache_fundamentals_ttl)
        obs.incr("cache_swr_serve", source="fundamentals")
        asyncio.create_task(_bg_refresh_fundamentals(symbol))
        return result

    return await _fetch_and_store_fundamentals(session, symbol, company, s, cache_key)


async def _bg_refresh_fundamentals(symbol: str) -> None:
    from app.core.db import SessionLocal

    try:
        async with SessionLocal() as s:
            s2 = get_settings()
            company = await get_company_by_symbol(s, symbol)
            if company is None or not company.cik:
                return
            cache_key = f"fund:result:{symbol}"
            await _fetch_and_store_fundamentals(s, symbol, company, s2, cache_key)
            logger.info("collector.fundamentals_bg_refresh", symbol=symbol)
    except Exception as exc:
        logger.warning("collector.fundamentals_bg_fail", symbol=symbol, error=str(exc)[:150])


async def _fetch_and_store_fundamentals(session, symbol: str, company, s, cache_key: str) -> dict[str, Any]:
    facts = await _sec.get_companyfacts(company.cik)
    cache_set(f"sec:companyfacts:{company.cik}", facts, s.cache_fundamentals_ttl)
    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    metrics: list[dict[str, Any]] = []
    retrieved_at = datetime.now(timezone.utc)

    for concept, aliases in _CONCEPTS:
        # pick the alias with the most RECENT annual fact (companies switch XBRL tags over time)
        best_alias = None
        best_latest_end = None
        for alias in aliases:
            if alias not in us_gaap:
                continue
            annual = _annual_only(us_gaap[alias].get("units") or {})
            latest_end = None
            for by_year in annual.values():
                for rec in by_year.values():
                    if latest_end is None or (rec.get("end") and rec["end"] > latest_end):
                        latest_end = rec["end"]
            if latest_end is not None and (best_latest_end is None or latest_end > best_latest_end):
                best_alias = alias
                best_latest_end = latest_end
        if best_alias is None:
            continue
        annual = _annual_only(us_gaap[best_alias].get("units") or {})
        for unit, by_year in annual.items():
            for fy, rec in sorted(by_year.items()):
                metrics.append(
                    {
                        "metric_key": concept,
                        "label_alias": best_alias,
                        "value": rec["val"],
                        "unit": unit,
                        "fiscal_year": fy,
                        "period": rec["end"].strftime("%Y-%m-%d"),
                        "source": "sec_xbrl",
                        "form": rec.get("form"),
                        "filed_at": rec["filed"].isoformat() if rec.get("filed") else None,
                    }
                )

    async with db_write_lock:
        for m in metrics:
            stmt = (
                sqlite_insert(FundamentalMetric)
                .values(
                    company_id=company.id,
                    metric_key=m["metric_key"],
                    period=m["period"],
                    fiscal_year=m["fiscal_year"],
                    value=m["value"],
                    unit=m["unit"],
                    source=m["source"],
                    retrieved_at=retrieved_at,
                )
                .on_conflict_do_update(
                    index_elements=["company_id", "metric_key", "period"],
                    set_={"value": m["value"], "unit": m["unit"], "fiscal_year": m["fiscal_year"], "retrieved_at": retrieved_at},
                )
            )
            await session.execute(stmt)
        await session.commit()

    logger.info("collector.fundamentals", symbol=symbol, metrics=len(metrics))
    result = {"symbol": symbol, "metrics": metrics, "retrieved_at": retrieved_at, "error": None}
    cache_set(cache_key, result, s.cache_fundamentals_ttl)
    return result


async def collect_news(session, symbol: str, company_name: str, limit: int | None = None) -> dict[str, Any]:
    s = get_settings()
    symbol = normalize_symbol(symbol)
    limit = limit or s.research_max_news_articles

    cache_key = f"news:result:{symbol}:{limit}"
    hit = cache_get(cache_key)
    if hit is not None:
        # Fast path: serve cached articles, skip the per-article upsert loop.
        return hit

    feed = await _news.fetch_feed(symbol, company_name, limit)
    async with db_write_lock:
        company = await get_company_by_symbol(session, symbol)
        if company is None:
            company = await upsert_company(session, symbol=symbol, name=company_name or symbol)

        stored: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for item in feed:
            h = url_hash(item["url"])
            if h in seen_urls:
                continue
            seen_urls.add(h)
            published = item.get("published_at") or datetime.now(timezone.utc)
            stmt = (
                sqlite_insert(NewsArticle)
                .values(
                    company_id=company.id,
                    url_hash=h,
                    title=item["title"],
                    source=item["source"],
                    url=item["url"],
                    published_at=published,
                    retrieved_at=item.get("retrieved_at") or datetime.now(timezone.utc),
                    summary=item.get("summary", ""),
                )
                .on_conflict_do_update(
                    index_elements=["company_id", "url_hash"],
                    set_={"title": item["title"], "summary": item.get("summary", ""), "retrieved_at": datetime.now(timezone.utc)},
                )
            )
            await session.execute(stmt)
            stored.append({**item, "published_at": published, "article_id": h})
        await session.commit()

    logger.info("collector.news", symbol=symbol, articles=len(stored))
    result = {"symbol": symbol, "articles": stored, "retrieved_at": datetime.now(timezone.utc)}
    cache_set(cache_key, result, s.cache_news_ttl)
    return result

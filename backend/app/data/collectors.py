import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import get_settings
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
    return company


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
        elif ql in name_l:
            score = 2.0
        elif name_l.startswith(ql):
            score = 1.5
        else:
            continue
        hits.append({"symbol": row["symbol"], "name": row["name"], "exchange": "SEC", "cik": row["cik_padded"], "score": score})
    hits.sort(key=lambda h: -h["score"])
    if hits:
        return hits[:limit]

    yahoo_hits = await _yahoo_search.search(query, limit)
    yahoo_hits.sort(key=lambda h: -h.get("score", 0))
    return yahoo_hits[:limit]


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
            profile["sic_sector"] = subs.get("sicSector")
            profile["sic_industry"] = subs.get("sicIndustry")
            profile["sector"] = subs.get("sicSector") or profile.get("sector")
            profile["industry"] = subs.get("sicIndustry") or profile.get("industry")
            profile["sources"].append("SEC submissions API")
        except Exception as exc:
            logger.warning("collector.profile_sec_fail", symbol=symbol, error=str(exc)[:150])

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
    chart = await cached(
        f"yahoo:chart:{symbol}:{range_}",
        s.cache_market_ttl,
        lambda: _yahoo_market.get_chart(symbol, range_),
    )
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
    logger.info("collector.market_data", symbol=symbol, rows=rows_written, range=range_)
    return {"symbol": symbol, "points": chart["points"], "currency": chart.get("currency"), "exchange": chart.get("exchange"), "retrieved_at": chart["retrieved_at"], "rows_written": rows_written}


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
    s = get_settings()
    symbol = normalize_symbol(symbol)
    company = await get_company_by_symbol(session, symbol)
    if company is None or not company.cik:
        await resolve_company_profile(session, symbol)
        company = await get_company_by_symbol(session, symbol)
    if company is None or not company.cik:
        return {"symbol": symbol, "metrics": [], "retrieved_at": datetime.now(timezone.utc), "error": "no SEC CIK available for this ticker"}

    facts = await cached(f"sec:companyfacts:{company.cik}", s.cache_fundamentals_ttl, lambda: _sec.get_companyfacts(company.cik))
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

    logger.info("collector.fundamentals", symbol=symbol, metrics=len(metrics))
    return {"symbol": symbol, "metrics": metrics, "retrieved_at": retrieved_at, "error": None}


async def collect_news(session, symbol: str, company_name: str, limit: int | None = None) -> dict[str, Any]:
    s = get_settings()
    symbol = normalize_symbol(symbol)
    limit = limit or s.research_max_news_articles

    feed = await cached(
        f"news:feed:{symbol}",
        s.cache_news_ttl,
        lambda: _news.fetch_feed(symbol, company_name, limit),
    )

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

    logger.info("collector.news", symbol=symbol, articles=len(stored))
    return {"symbol": symbol, "articles": stored, "retrieved_at": datetime.now(timezone.utc)}

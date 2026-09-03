"""Comparison mode: A vs B across growth, profitability, leverage, volatility, sentiment.

Methodology: both sides are computed with the SAME deterministic functions over the
SAME time ranges; missing metrics are shown as unavailable (never imputed). No
arbitrary rankings — differences are reported, not scored into a single number.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.sentiment import aggregate_sentiment, analyze_article
from app.analytics.technical import compute_technicals, derive_fundamentals
from app.core.db import get_db
from app.data.collectors import collect_fundamentals, collect_market_data, collect_news, resolve_company_profile
from app.data.validation import normalize_symbol
from app.schemas.common import PricePoint

router = APIRouter()


class CompareRequest(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=3)


@router.post("/compare")
async def compare_companies(body: CompareRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        symbols = [normalize_symbol(s) for s in body.symbols]
        if len(set(symbols)) != len(symbols):
            raise HTTPException(status_code=422, detail="duplicate symbols in comparison")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    results: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        results[symbol] = await _collect_comparison_data(db, symbol)

    comparison: dict[str, Any] = {"symbols": symbols, "methodology": "Same deterministic calculations applied to both companies over the same 1y daily window; SEC XBRL latest fiscal year for fundamentals; missing metrics reported as unavailable (no imputation). Metrics are compared, not ranked.", "metrics": {}, "retrieved_at": [results[s]["retrieved_at"] for s in symbols]}

    metric_specs = [
        ("revenue_growth_yoy", "Revenue growth YoY (%)", [results[s]["fundamentals"].get("revenue_growth_yoy") for s in symbols]),
        ("net_profit_margin", "Net profit margin", [results[s]["fundamentals"].get("net_profit_margin") for s in symbols]),
        ("debt_to_equity", "Debt-to-equity (total liabilities/equity)", [results[s]["fundamentals"].get("debt_to_equity") for s in symbols]),
        ("roe", "Return on equity", [results[s]["fundamentals"].get("roe") for s in symbols]),
        ("annualized_volatility_pct", "Annualized volatility (%)", [results[s]["technical_stats"].get("annualized_volatility_pct") for s in symbols]),
        ("max_drawdown_pct", "Max drawdown (%)", [results[s]["technical_stats"].get("max_drawdown_pct") for s in symbols]),
        ("price_change_pct", "1y price change (%)", [results[s]["price_change_pct"] for s in symbols]),
        ("sentiment_label", "News sentiment", [results[s]["sentiment"].get("aggregate_label") for s in symbols]),
        ("eps_diluted", "Diluted EPS (latest FY, USD)", [results[s]["fundamentals"].get("eps_diluted") for s in symbols]),
    ]
    for key, label, values in metric_specs:
        comparison["metrics"][key] = {"label": label, "values": {s: v for s, v in zip(symbols, values)}}

    tech_signals = {s: results[s]["signals"] for s in symbols}
    comparison["technical_signals"] = tech_signals
    comparison["risks"] = {s: results[s]["risks"] for s in symbols}
    comparison["disclaimer"] = "Educational comparison of computed metrics. Differences are observations, not rankings or recommendations."
    return comparison


async def _collect_comparison_data(db: AsyncSession, symbol: str) -> dict[str, Any]:
    profile = await resolve_company_profile(db, symbol)

    fundamentals: dict[str, Any] = {}
    try:
        f = await collect_fundamentals(db, symbol)
        if not f.get("error"):
            derived = derive_fundamentals(f["metrics"])
            latest = derived.get("latest", {})
            fundamentals = {
                "revenue_growth_yoy": latest.get("revenue_growth_yoy", {}).get("value"),
                "net_profit_margin": latest.get("ratios", {}).get("net_profit_margin", {}).get("value"),
                "debt_to_equity": latest.get("ratios", {}).get("debt_to_equity", {}).get("value"),
                "roe": latest.get("ratios", {}).get("roe", {}).get("value"),
                "eps_diluted": latest.get("eps_diluted", {}).get("value"),
                "fiscal_year": latest.get("latest_fiscal_year"),
                "period": latest.get("revenue", {}).get("period"),
            }
    except Exception:
        fundamentals = {}

    technical_stats: dict[str, Any] = {}
    signals: list[dict[str, Any]] = []
    price_change_pct: float | None = None
    try:
        m = await collect_market_data(db, symbol, "1y")
        closes = [p.close for p in m["points"] if p.close is not None]
        if len(closes) >= 2:
            price_change_pct = round((closes[-1] - closes[0]) / closes[0] * 100, 2)
        tech = compute_technicals([PricePoint(ts=p.ts, open=p.open, high=p.high, low=p.low, close=p.close, adjclose=p.adjclose, volume=p.volume) for p in m["points"]])
        technical_stats = tech["statistics"]
        signals = tech["signals"]
    except Exception:
        pass

    sentiment: dict[str, Any] = {"aggregate_label": "unavailable"}
    try:
        n = await collect_news(db, symbol, profile["name"], 15)
        analyzed = []
        for a in n["articles"]:
            s = analyze_article(a["title"], a.get("summary", ""))
            analyzed.append({**a, "sentiment_label": s["label"], "sentiment_score": s["score"]})
        sentiment = aggregate_sentiment(analyzed)
    except Exception:
        pass

    risks: list[str] = []
    if fundamentals.get("debt_to_equity") is not None and fundamentals["debt_to_equity"] > 2.0:
        risks.append(f"leverage: liabilities {fundamentals['debt_to_equity']:.1f}x equity (SEC 10-K)")
    if fundamentals.get("revenue_growth_yoy") is not None and fundamentals["revenue_growth_yoy"] < 0:
        risks.append(f"revenue declined {abs(fundamentals['revenue_growth_yoy']):.1f}% YoY (SEC 10-K)")
    if not fundamentals:
        risks.append("no SEC fundamentals available")

    return {"profile": {"symbol": symbol, "name": profile["name"]}, "fundamentals": fundamentals, "technical_stats": technical_stats, "price_change_pct": price_change_pct, "signals": signals, "sentiment": sentiment, "risks": risks, "retrieved_at": profile["retrieved_at"].isoformat()}

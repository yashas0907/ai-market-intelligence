"""Typed tool registry. Every tool: typed inputs/outputs, validation, logging, error handling.

Agents may ONLY make factual claims backed by tool outputs — never from parametric knowledge.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.core.observability import obs
from app.data.collectors import (
    collect_fundamentals,
    collect_market_data,
    collect_news,
    resolve_company_profile,
    search_companies,
)
from app.data.validation import normalize_symbol


class ToolResult(BaseModel):
    tool: str
    ok: bool
    data: dict[str, Any] | list[Any] | None = None
    error: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MarketDataTool(BaseModel):
    symbol: str
    range: str = "1y"

    class Config:
        extra = "forbid"


async def tool_market_data(session, params: MarketDataTool) -> ToolResult:
    try:
        sym = normalize_symbol(params.symbol)
        result = await collect_market_data(session, sym, params.range)
        return ToolResult(tool="market_data", ok=True, data={"symbol": sym, "currency": result["currency"], "points": [{"ts": p.ts.isoformat(), "close": p.close, "volume": p.volume} for p in result["points"]], "retrieved_at": result["retrieved_at"].isoformat()}, retrieved_at=result["retrieved_at"])
    except Exception as exc:
        obs.incr("tool_error", tool="market_data")
        return ToolResult(tool="market_data", ok=False, error=f"market data unavailable: {str(exc)[:200]}")


async def tool_fundamentals(session, params: BaseModel) -> ToolResult:
    try:
        symbol = normalize_symbol(getattr(params, "symbol", ""))
        result = await collect_fundamentals(session, symbol)
        if result.get("error"):
            return ToolResult(tool="fundamentals", ok=False, error=result["error"], retrieved_at=result["retrieved_at"])
        return ToolResult(tool="fundamentals", ok=True, data={"symbol": symbol, "metrics": result["metrics"]}, retrieved_at=result["retrieved_at"])
    except Exception as exc:
        obs.incr("tool_error", tool="fundamentals")
        return ToolResult(tool="fundamentals", ok=False, error=f"fundamentals unavailable: {str(exc)[:200]}")


class NewsToolParams(BaseModel):
    symbol: str
    limit: int = Field(default=20, ge=1, le=50)

    class Config:
        extra = "forbid"


async def tool_news(session, params: NewsToolParams) -> ToolResult:
    try:
        symbol = normalize_symbol(params.symbol)
        profile = await resolve_company_profile(session, symbol)
        result = await collect_news(session, symbol, profile["name"], limit=params.limit)
        articles = [{"title": a["title"], "source": a["source"], "url": a["url"], "published_at": a["published_at"].isoformat(), "summary": a["summary"]} for a in result["articles"]]
        return ToolResult(tool="news", ok=True, data={"symbol": symbol, "articles": articles, "company_name": profile["name"]}, retrieved_at=result["retrieved_at"])
    except Exception as exc:
        obs.incr("tool_error", tool="news")
        return ToolResult(tool="news", ok=False, error=f"news unavailable: {str(exc)[:200]}")


class SearchToolParams(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=25)

    class Config:
        extra = "forbid"


async def tool_search(params: SearchToolParams) -> ToolResult:
    try:
        hits = await search_companies(params.query, params.limit)
        return ToolResult(tool="search", ok=True, data={"hits": hits})
    except Exception as exc:
        obs.incr("tool_error", tool="search")
        return ToolResult(tool="search", ok=False, error=f"search failed: {str(exc)[:200]}")


class TechnicalToolParams(BaseModel):
    symbol: str
    range: str = "1y"

    class Config:
        extra = "forbid"


async def tool_technical(session, params: TechnicalToolParams) -> ToolResult:
    from app.analytics.technical import compute_technicals
    from app.schemas.common import PricePoint

    try:
        symbol = normalize_symbol(params.symbol)
        result = await collect_market_data(session, symbol, params.range)
        points = result["points"]
        price_points = [PricePoint(ts=p.ts, open=p.open, high=p.high, low=p.low, close=p.close, adjclose=p.adjclose, volume=p.volume) for p in points]
        tech = compute_technicals(price_points)
        return ToolResult(
            tool="technical",
            ok=True,
            data={"symbol": symbol, "statistics": tech["statistics"], "signals": tech["signals"], "latest": tech["latest"]},
            retrieved_at=result["retrieved_at"],
        )
    except Exception as exc:
        obs.incr("tool_error", tool="technical")
        return ToolResult(tool="technical", ok=False, error=f"technical analysis unavailable: {str(exc)[:200]}")


class VectorSearchToolParams(BaseModel):
    query: str
    company_symbol: str | None = None
    doc_type: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)

    class Config:
        extra = "forbid"


async def tool_vector_search(session, params: VectorSearchToolParams) -> ToolResult:
    from app.retrieval.vector_store import retrieve

    try:
        results = await retrieve(session, params.query, company_symbol=params.company_symbol, doc_type=params.doc_type, top_k=params.top_k)
        return ToolResult(tool="vector_search", ok=True, data={"results": results})
    except Exception as exc:
        obs.incr("tool_error", tool="vector_search")
        return ToolResult(tool="vector_search", ok=False, error=f"retrieval failed: {str(exc)[:200]}")


TOOL_REGISTRY = {
    "market_data": tool_market_data,
    "fundamentals": tool_fundamentals,
    "news": tool_news,
    "search": tool_search,
    "technical": tool_technical,
    "vector_search": tool_vector_search,
}


def get_tool(name: str):
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    return tool

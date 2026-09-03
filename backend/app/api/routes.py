from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.data.collectors import search_companies
from app.data.validation import normalize_symbol
from app.schemas.common import SearchHit
from app.services.jobs import get_status, start_research

router = APIRouter()


@router.get("/companies/search", response_model=list[SearchHit])
async def company_search(q: str = Query(min_length=1, max_length=100), limit: int = Query(default=10, ge=1, le=25)) -> list[SearchHit]:
    try:
        hits = await search_companies(q, limit)
        return [SearchHit(symbol=h["symbol"], name=h["name"], exchange=h.get("exchange"), score=h.get("score", 0), source="yahoo" if "cik" not in h else "sec") for h in hits]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"search failed: {str(exc)[:150]}")


@router.get("/company/{symbol}")
async def company_profile(symbol: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.data.collectors import resolve_company_profile

    try:
        sym = normalize_symbol(symbol)
        profile = await resolve_company_profile(db, sym)
        return {
            "symbol": profile["symbol"],
            "name": profile["name"],
            "exchange": profile.get("exchange"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "cik": profile.get("cik"),
            "currency": profile.get("currency"),
            "sources": profile.get("sources", []),
            "retrieved_at": profile["retrieved_at"].isoformat(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"profile lookup failed: {str(exc)[:150]}")


@router.get("/company/{symbol}/market")
async def company_market(symbol: str, range: str = Query(default="1y", pattern="^(1mo|3mo|6mo|1y|2y|5y|max)$"), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.data.collectors import collect_market_data

    try:
        result = await collect_market_data(db, symbol, range)
        points = [{"ts": p.ts.isoformat(), "open": p.open, "high": p.high, "low": p.low, "close": p.close, "adjclose": p.adjclose, "volume": p.volume} for p in result["points"]]
        return {"symbol": result["symbol"], "currency": result.get("currency"), "points": points, "count": len(points), "retrieved_at": result["retrieved_at"].isoformat(), "note": "split-adjusted daily OHLCV from Yahoo Finance public API"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"market data failed: {str(exc)[:150]}")


@router.get("/company/{symbol}/fundamentals")
async def company_fundamentals(symbol: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.analytics.technical import derive_fundamentals
    from app.data.collectors import collect_fundamentals

    try:
        result = await collect_fundamentals(db, symbol)
        if result.get("error"):
            return {"symbol": result["symbol"], "error": result["error"], "metrics": [], "derived": {}, "retrieved_at": result["retrieved_at"].isoformat()}
        derived = derive_fundamentals(result["metrics"])
        return {"symbol": result["symbol"], "metrics": result["metrics"], "derived": derived, "retrieved_at": result["retrieved_at"].isoformat(), "source": "SEC EDGAR XBRL (annual 10-K facts)"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"fundamentals failed: {str(exc)[:150]}")


@router.get("/company/{symbol}/news")
async def company_news(symbol: str, limit: int = Query(default=20, ge=1, le=50), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.analytics.sentiment import analyze_article
    from app.data.collectors import collect_news, resolve_company_profile

    try:
        profile = await resolve_company_profile(db, symbol)
        result = await collect_news(db, profile["symbol"], profile["name"], limit)
        articles = []
        for a in result["articles"]:
            s = analyze_article(a["title"], a.get("summary", ""))
            articles.append({"title": a["title"], "source": a["source"], "url": a["url"], "published_at": a["published_at"].isoformat(), "summary": a.get("summary", ""), "sentiment": s["label"], "sentiment_score": s["score"]})
        return {"symbol": profile["symbol"], "articles": articles, "retrieved_at": result["retrieved_at"].isoformat(), "source": "Google News RSS (metadata + snippets)"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"news failed: {str(exc)[:150]}")


@router.get("/company/{symbol}/technical")
async def company_technical(symbol: str, range: str = Query(default="1y", pattern="^(1mo|3mo|6mo|1y|2y|5y|max)$"), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.analytics.technical import compute_technicals
    from app.data.collectors import collect_market_data

    try:
        result = await collect_market_data(db, symbol, range)
        points = result["points"]
        tech = compute_technicals(points)
        return {"symbol": result["symbol"], "signals": tech["signals"], "latest": tech["latest"], "statistics": tech["statistics"], "series": tech["series"], "retrieved_at": result["retrieved_at"].isoformat()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"technical analysis failed: {str(exc)[:150]}")


@router.post("/research")
async def create_research(symbol: str, depth: str = Query(default="standard", pattern="^(quick|standard|deep)$")) -> dict[str, Any]:
    try:
        result = await start_research(symbol, depth)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"failed to start research: {str(exc)[:150]}")


@router.get("/research/{job_id}")
async def research_status(job_id: str) -> dict[str, Any]:
    status = await get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="research job not found")
    return status


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(), "version": "1.0.0"}

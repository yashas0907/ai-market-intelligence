from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.data.validation import normalize_symbol
from app.models import WatchlistItem

router = APIRouter()


class WatchlistAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    note: str | None = Field(default=None, max_length=200)


@router.get("/watchlist")
async def get_watchlist(db: AsyncSession = Depends(get_db)) -> dict:
    rows = (await db.execute(select(WatchlistItem).order_by(WatchlistItem.added_at.desc()))).scalars().all()
    items = []
    for row in rows:
        items.append({"symbol": row.symbol, "company_name": row.company_name, "added_at": row.added_at.isoformat(), "last_analysis_at": row.last_analysis_at.isoformat() if row.last_analysis_at else None, "note": row.note})
    return {"items": items, "count": len(items)}


@router.post("/watchlist", status_code=201)
async def add_to_watchlist(body: WatchlistAdd, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        symbol = normalize_symbol(body.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    existing = (await db.execute(select(WatchlistItem).where(WatchlistItem.symbol == symbol))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"{symbol} already in watchlist")
    from app.data.collectors import resolve_company_profile

    profile = await resolve_company_profile(db, symbol)
    db.add(WatchlistItem(symbol=symbol, company_name=profile["name"], note=body.note))
    await db.commit()
    return {"symbol": symbol, "company_name": profile["name"], "added": True}


@router.delete("/watchlist/{symbol}", status_code=200)
async def remove_from_watchlist(symbol: str, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        symbol = normalize_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    row = (await db.execute(select(WatchlistItem).where(WatchlistItem.symbol == symbol))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{symbol} not in watchlist")
    await db.delete(row)
    await db.commit()
    return {"symbol": symbol, "removed": True}

"""Background refresher: keeps watchlist symbols' caches warm so their views are
fast and data stays fresh. Runs every 15 minutes in-process; each refresh uses
its own DB session. Cache TTLs still apply — this only pre-warms."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.observability import logger, obs
from app.models import WatchlistItem

_task: asyncio.Task | None = None
_INTERVAL_SECONDS = 900


async def _refresh_symbol(symbol: str) -> None:
    from app.core.db import SessionLocal
    from app.data.collectors import collect_fundamentals, collect_market_data

    async with SessionLocal() as s:
        try:
            await collect_market_data(s, symbol, "1y")
        except Exception:
            pass
    async with SessionLocal() as s:
        try:
            await collect_fundamentals(s, symbol)
        except Exception:
            pass


async def _loop() -> None:
    while True:
        await asyncio.sleep(_INTERVAL_SECONDS)
        try:
            from app.core.db import SessionLocal

            async with SessionLocal() as session:
                symbols = (await session.execute(select(WatchlistItem.symbol))).scalars().all()
            for sym in symbols:
                await _refresh_symbol(sym)
            if symbols:
                obs.incr("refresher_cycle", n=len(symbols))
                logger.info("refresher.cycle", symbols=len(symbols))
        except Exception as exc:
            logger.warning("refresher.error", error=str(exc)[:150])


def start_refresher() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())

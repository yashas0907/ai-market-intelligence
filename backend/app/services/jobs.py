"""Background research jobs with real status tracking (no fake progress)."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.observability import logger, obs
from app.data.validation import normalize_symbol
from app.models import ResearchSession
from app.workflows.orchestrator import Orchestrator

_jobs: dict[str, asyncio.Task] = {}
_completed_cache: dict[str, tuple[str, dict[str, Any]]] = {}
_DEDUP_TTL_SECONDS = 3600


async def start_research(symbol: str, depth: str = "standard") -> dict[str, Any]:
    """Create a research job. Duplicate prevention: if an identical (symbol, depth)
    report completed recently, return it instead of re-running."""
    from app.core.db import SessionLocal

    symbol = normalize_symbol(symbol)
    get_settings()

    cache_key = f"{symbol}:{depth}"
    cached = _completed_cache.get(cache_key)
    if cached:
        cached_at, payload = cached
        if (datetime.now(timezone.utc) - cached_at).total_seconds() < _DEDUP_TTL_SECONDS:
            obs.incr("research_dedup_hit")
            return {"job_id": payload["job_id"], "status": "completed", "deduplicated": True, "report": payload["report"]}

    public_id = str(uuid.uuid4())
    async with SessionLocal() as session:
        row = ResearchSession(public_id=public_id, symbol=symbol, status="pending", depth=depth, stage="queued")
        session.add(row)
        await session.commit()

    task = asyncio.create_task(_run_job(public_id, symbol, depth))
    _jobs[public_id] = task
    obs.incr("research_started")
    return {"job_id": public_id, "status": "pending", "deduplicated": False}


async def _run_job(public_id: str, symbol: str, depth: str) -> None:
    from app.core.db import SessionLocal

    start = time.perf_counter()
    async with SessionLocal() as session:
        row = (await session.execute(select(ResearchSession).where(ResearchSession.public_id == public_id))).scalar_one_or_none()
        if row is None:
            logger.error("jobs.session_missing", public_id=public_id)
            return
        row.status = "running"
        row.started_at = datetime.now(timezone.utc)
        await session.commit()

    try:
        async with SessionLocal() as session:
            orchestrator = Orchestrator(session)
            report = await orchestrator.run_research(public_id, symbol, depth)
        _completed_cache[f"{symbol}:{depth}"] = (datetime.now(timezone.utc), {"job_id": public_id, "report": report})
    except Exception as exc:
        logger.error("jobs.job_failed", public_id=public_id, symbol=symbol, error=str(exc)[:300])
        obs.incr("research_failed")
        async with SessionLocal() as session:
            row = (await session.execute(select(ResearchSession).where(ResearchSession.public_id == public_id))).scalar_one_or_none()
            if row:
                row.status = "failed"
                row.error = str(exc)[:500]
                row.completed_at = datetime.now(timezone.utc)
                row.duration_ms = int((time.perf_counter() - start) * 1000)
                await session.commit()
    finally:
        _jobs.pop(public_id, None)


async def get_status(public_id: str) -> dict[str, Any] | None:
    from app.core.db import SessionLocal

    async with SessionLocal() as session:
        row = (await session.execute(select(ResearchSession).where(ResearchSession.public_id == public_id))).scalar_one_or_none()
        if row is None:
            return None
        result = {
            "job_id": row.public_id,
            "symbol": row.symbol,
            "status": row.status,
            "stage": row.stage,
            "progress": row.progress,
            "stages": row.stages or [],
            "error": row.error,
            "created_at": row.created_at.isoformat(),
            "duration_ms": row.duration_ms,
        }
        if row.status == "completed":
            from app.models import ResearchReportDB

            report_row = (await session.execute(select(ResearchReportDB).where(ResearchReportDB.session_id == row.id))).scalar_one_or_none()
            if report_row:
                result["report"] = report_row.report_json
        return result


async def cancel_stale_jobs(max_age_seconds: int = 1800) -> int:
    from app.core.db import SessionLocal

    async with SessionLocal() as session:
        rows = (await session.execute(select(ResearchSession).where(ResearchSession.status.in_(["pending", "running"])))).scalars().all()
        now = datetime.now(timezone.utc)
        cancelled = 0
        for row in rows:
            if row.created_at and (now - row.created_at.replace(tzinfo=timezone.utc)).total_seconds() > max_age_seconds:
                row.status = "failed"
                row.error = "cancelled: exceeded maximum job age"
                cancelled += 1
        await session.commit()
        return cancelled

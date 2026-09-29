"""Real-time streaming via Server-Sent Events.

- Research progress: push updates the moment a stage completes (no polling lag).
- Live quotes: push updates every 30s while a symbol is selected.
Clients without SSE support fall back to the polling endpoints.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.jobs import get_status

router = APIRouter()

_MAX_STREAM_SECONDS = 600
_QUOTE_PUSH_SECONDS = 30
_QUOTE_MAX_PUSHES = 40


@router.get("/research/{job_id}/stream")
async def stream_research(job_id: str):
    async def gen():
        last_key = None
        for _ in range(_MAX_STREAM_SECONDS):
            status = await get_status(job_id)
            if status is None:
                yield f"event: error\ndata: {json.dumps({'error': 'job not found'})}\n\n"
                return
            key = (status["status"], status["stage"], status["progress"])
            if key != last_key:
                last_key = key
                payload = {"status": status["status"], "stage": status["stage"], "progress": status["progress"], "stages": status["stages"] or []}
                yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
            if status["status"] in ("completed", "failed"):
                final = {"status": status["status"], "report": status.get("report"), "error": status.get("error")}
                yield f"event: done\ndata: {json.dumps(final, default=str)}\n\n"
                return
            await asyncio.sleep(1)
        yield f"event: error\ndata: {json.dumps({'error': 'stream timeout — use GET /api/research/' + job_id})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/company/{symbol}/quote/stream")
async def stream_quote(symbol: str):
    """Push quote updates every 30s while the client stays connected.

    Source may delay quotes up to 15 minutes — each push carries its retrieval
    timestamp; more frequent pushes improve responsiveness, not data freshness.
    """
    from app.data.collectors import get_live_quote

    async def gen():
        last = None
        for _ in range(_QUOTE_MAX_PUSHES):
            try:
                q = await get_live_quote(symbol)
            except Exception as exc:
                yield f"event: error\ndata: {json.dumps({'error': str(exc)[:150]})}\n\n"
                return
            key = (q.get("price"), q.get("as_of"))
            if key != last:
                last = key
                yield f"event: quote\ndata: {json.dumps(q, default=str)}\n\n"
            await asyncio.sleep(_QUOTE_PUSH_SECONDS)
        yield f"event: error\ndata: {json.dumps({'error': 'stream ended — reconnect for live quotes'})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )

"""Real-time research progress streaming via Server-Sent Events.

The UI receives push updates the moment a stage completes (no polling lag).
Falls back to GET /api/research/{id} polling for clients without SSE support.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.jobs import get_status

router = APIRouter()

_MAX_STREAM_SECONDS = 600


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

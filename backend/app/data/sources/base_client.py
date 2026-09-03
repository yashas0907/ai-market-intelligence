import asyncio
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.observability import obs, logger


class SourceError(Exception):
    def __init__(self, source: str, message: str, status: int | None = None):
        self.source = source
        self.status = status
        super().__init__(f"[{source}] {message}")


class BaseClient(ABC):
    """HTTP client wrapper with retry, timeout, UA header, observability."""

    source_name: str = "base"

    def __init__(self, user_agent: str | None = None, timeout: float | None = None):
        s = get_settings()
        self.settings = s
        self.user_agent = user_agent or s.http_user_agent
        self.timeout = timeout or s.http_timeout_seconds
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(
                    headers={"User-Agent": self.user_agent, "Accept": "application/json,text/*"},
                    timeout=self.timeout,
                    follow_redirects=True,
                )
            return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _fetch_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = await self._get_client()
        last_exc: Exception | None = None
        retries = self.settings.source_max_retries
        for attempt in range(retries + 1):
            try:
                with obs.timer("source_fetch", source=self.source_name):
                    resp = await client.get(url, params=params)
                obs.incr("source_response", source=self.source_name, status=str(resp.status_code))
                if resp.status_code == 429:
                    await asyncio.sleep(self.settings.source_retry_backoff * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                last_exc = exc
                obs.incr("source_error", source=self.source_name)
                logger.warning("source.fetch_error", source=self.source_name, url=url[:120], attempt=attempt, error=str(exc)[:150])
                if attempt < retries:
                    await asyncio.sleep(self.settings.source_retry_backoff * (attempt + 1))
        raise SourceError(self.source_name, f"fetch failed after {retries + 1} attempts: {str(last_exc)[:200]}")

    async def _fetch_xml(self, url: str, params: dict[str, Any] | None = None) -> bytes:
        client = await self._get_client()
        last_exc: Exception | None = None
        retries = self.settings.source_max_retries
        for attempt in range(retries + 1):
            try:
                with obs.timer("source_fetch", source=self.source_name):
                    resp = await client.get(url, params=params, headers={"Accept": "application/rss+xml,text/xml,*/*"})
                obs.incr("source_response", source=self.source_name, status=str(resp.status_code))
                if resp.status_code == 429:
                    await asyncio.sleep(self.settings.source_retry_backoff * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.content
            except httpx.HTTPError as exc:
                last_exc = exc
                obs.incr("source_error", source=self.source_name)
                logger.warning("source.fetch_error", source=self.source_name, url=url[:120], attempt=attempt, error=str(exc)[:150])
                if attempt < retries:
                    await asyncio.sleep(self.settings.source_retry_backoff * (attempt + 1))
        raise SourceError(self.source_name, f"fetch failed after {retries + 1} attempts: {str(last_exc)[:200]}")

    async def fetch_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._fetch_json(url, params)

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        ...

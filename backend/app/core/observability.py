import logging
import sys
import time
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable

import structlog

from app.core.config import get_settings

_SETTINGS = None


def _get_level() -> int:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = get_settings()
    return getattr(logging, _SETTINGS.log_level.upper(), logging.INFO)


def configure_logging() -> None:
    level = _get_level()
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "app"):
    return structlog.get_logger(name)


class Observability:
    """In-process metrics registry for observability (counters, timers, recent events)."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.timers: dict[str, list[float]] = {}
        self.events: list[dict[str, Any]] = []
        self._max_events = 500

    def incr(self, name: str, value: int = 1, **tags: Any) -> None:
        key = self._key(name, tags)
        self.counters[key] = self.counters.get(key, 0) + value

    def _key(self, name: str, tags: dict[str, Any]) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"

    @contextmanager
    def timer(self, name: str, **tags: Any):
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.timers.setdefault(name, []).append(elapsed)
            self.incr(name + "_calls", **tags)

    def record_event(self, event_type: str, detail: dict[str, Any] | None = None) -> None:
        self.events.append({"type": event_type, "detail": detail or {}, "ts": time.time()})
        if len(self.events) > self._max_events:
            self.events = self.events[-self._max_events:]

    def snapshot(self) -> dict[str, Any]:
        import statistics as _stats

        timer_stats = {}
        for name, samples in self.timers.items():
            if samples:
                timer_stats[name] = {
                    "count": len(samples),
                    "mean_ms": round(_stats.mean(samples) * 1000, 2),
                    "p95_ms": round(_stats.quantiles(samples, n=20)[-1] * 1000, 2) if len(samples) >= 20 else round(max(samples) * 1000, 2),
                }
        return {
            "counters": dict(sorted(self.counters.items())),
            "timers": timer_stats,
            "recent_events": list(self.events[-50:]),
        }


obs = Observability()
logger = get_logger("core.observability")


def log_tool_call(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any):
        tool_name = getattr(func, "__name__", "unknown")
        with obs.timer("tool_call", tool=tool_name):
            logger.info("tool.call", tool=tool_name)
            try:
                result = await func(*args, **kwargs)
                obs.incr("tool_success", tool=tool_name)
                return result
            except Exception as exc:
                obs.incr("tool_error", tool=tool_name)
                logger.error("tool.error", tool=tool_name, error=str(exc)[:200])
                raise

    return wrapper


from dotenv import load_dotenv

load_dotenv()  # load .env before settings are constructed

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api import compare, documents, routes, watchlist
from app.core.config import get_settings
from app.core.db import init_db
from app.core.observability import configure_logging, get_logger, obs

configure_logging()
logger = get_logger("main")
settings = get_settings()

limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.rate_limit_per_minute}/minute"])

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Educational/research market intelligence platform. NOT financial advice.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

API = settings.api_prefix
app.include_router(routes.router, prefix=API, tags=["companies"])
app.include_router(watchlist.router, prefix=API, tags=["watchlist"])
app.include_router(compare.router, prefix=API, tags=["compare"])
app.include_router(documents.router, prefix=API, tags=["observability-documents"])


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    # Warm the SEC ticker-map cache so the first user search is fast.
    try:
        from app.data.collectors import cached
        from app.data.sources.clients import SecClient

        s = get_settings()
        await cached("sec:ticker_map", s.cache_company_ttl, SecClient().get_ticker_map)
        logger.info("app.cache_warmed", entries="sec:ticker_map")
    except Exception as exc:
        logger.warning("app.cache_warm_failed", error=str(exc)[:150])
    logger.info("app.started", version=settings.app_version, environment=settings.environment, llm=settings.llm_provider)


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    import time

    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        obs.incr("http_error", path=request.url.path, method=request.method)
        logger.error("http.unhandled", path=request.url.path, error=str(exc)[:200])
        return JSONResponse(status_code=500, content={"detail": "internal server error"})
    elapsed_ms = (time.perf_counter() - start) * 1000
    obs.incr("http_request", path=request.url.path, method=request.method, status=str(response.status_code))
    obs.timers.setdefault("http_request_latency", []).append(elapsed_ms / 1000.0)
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    if request.url.path not in ("/api/health",):
        logger.info("http.request", method=request.method, path=request.url.path, status=response.status_code, ms=round(elapsed_ms, 1))
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)

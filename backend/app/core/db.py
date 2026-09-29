import asyncio
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Base

_settings = get_settings()

engine = create_async_engine(_settings.database_url, echo=False, pool_pre_ping=True, connect_args={"timeout": 30})


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    """WAL mode: concurrent readers + one writer (standard production SQLite).
    busy_timeout=30s: write transactions queue instead of failing with
    'database is locked' under parallel request load."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

# SQLite allows one writer per database. Parallel async requests each holding a
# deferred transaction fail with BUSY_SNAPSHOT when another writer commits
# between their read and write (busy_timeout cannot help there). Serializing all
# write sections through this in-process lock fixes it for the single-worker
# deployment; multi-process deployments need Postgres or a file lock.
db_write_lock = asyncio.Lock()


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session

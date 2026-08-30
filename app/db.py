"""Database engine, session factory, and pool instrumentation.

The engine's connection pool is bounded (`pool_size`, `max_overflow`,
`pool_timeout` from config). That bound is the resource ADR 0002 exhausts.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from . import config, metrics
from .logging_conf import get_logger
from .tracing import tracer

log = get_logger()

engine = create_async_engine(
    config.DATABASE_URL,
    pool_size=config.DB_POOL_SIZE,
    max_overflow=config.DB_MAX_OVERFLOW,
    pool_timeout=config.DB_POOL_TIMEOUT,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def acquire_session() -> AsyncIterator[AsyncSession]:
    """Open a session AND force the connection checkout eagerly, timing the wait.

    SQLAlchemy checks a connection out of the pool lazily (on first query). We
    force it here with `session.connection()` so we can measure exactly how long
    a request blocks waiting for the pool — the `db_wait_ms` in ADR 0003. When
    the pool is exhausted, this is where the request parks and, after
    `pool_timeout`, where the TimeoutError is raised.
    """
    session = SessionLocal()
    t0 = time.perf_counter()
    try:
        # The db.checkout span captures exactly the time spent waiting for the
        # pool. Under exhaustion its duration balloons — the trace-level smoking gun.
        with tracer.start_as_current_span("db.checkout") as span:
            await session.connection()  # <-- pool checkout happens here
            wait = time.perf_counter() - t0
            span.set_attribute("db.wait_ms", round(wait * 1000, 2))
        metrics.DB_CHECKOUT_WAIT.observe(wait)
        # Stash the measured wait on the session for the handler/logs to read.
        session.info["db_wait_ms"] = round(wait * 1000, 2)
        yield session
    finally:
        await session.close()


async def ensure_schema_and_seed() -> None:
    """Idempotently create the widgets table and seed it.

    Makes the app runnable against a bare Postgres even without the init.sql
    bootstrap, so `docker compose up` and a local run behave identically.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS widgets (
                    id    SERIAL PRIMARY KEY,
                    name  TEXT NOT NULL,
                    price NUMERIC(10, 2) NOT NULL
                )
                """
            )
        )
        count = (await conn.execute(text("SELECT COUNT(*) FROM widgets"))).scalar_one()
        if count == 0:
            await conn.execute(
                text(
                    "INSERT INTO widgets (name, price) "
                    "SELECT 'widget-' || g, (g * 1.5)::numeric "
                    "FROM generate_series(1, 100) AS g"
                )
            )
            log.info("db.seeded", rows=100)


def pool_stats() -> dict[str, int]:
    """Snapshot of pool state for the metrics updater and health checks."""
    pool = engine.sync_engine.pool
    return {
        "size": pool.size(),
        "checked_out": pool.checkedout(),
        "available": pool.checkedin(),
        "overflow": pool.overflow(),
    }

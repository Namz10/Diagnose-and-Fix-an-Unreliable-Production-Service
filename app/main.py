"""Widget service — FastAPI application.

Exposes the business endpoint that contains the injected fault (ADR 0002) plus
the observability surface (ADR 0003). The fault is toggled by config.FIX_ENABLED
so the same binary demonstrates both the broken and fixed behaviour.
"""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SATimeoutError

from . import config, db, metrics
from .downstream import call_downstream
from .logging_conf import configure_logging, get_logger
from .middleware import ObservabilityMiddleware
from .tracing import configure_tracing

configure_logging()
configure_tracing()
log = get_logger()


async def _pool_gauge_updater() -> None:
    """Continuously publish pool state to Prometheus (ADR 0003, the key signal)."""
    while True:
        stats = db.pool_stats()
        metrics.DB_POOL_CONNECTIONS.labels("size").set(stats["size"])
        metrics.DB_POOL_CONNECTIONS.labels("checked_out").set(stats["checked_out"])
        metrics.DB_POOL_CONNECTIONS.labels("available").set(stats["available"])
        metrics.DB_POOL_CONNECTIONS.labels("overflow").set(stats["overflow"])
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "service.starting",
        service=config.SERVICE_NAME,
        fix_enabled=config.FIX_ENABLED,
        pool_size=config.DB_POOL_SIZE,
        max_overflow=config.DB_MAX_OVERFLOW,
        pool_timeout=config.DB_POOL_TIMEOUT,
    )
    await db.ensure_schema_and_seed()
    app.state.http = httpx.AsyncClient()
    updater = asyncio.create_task(_pool_gauge_updater())
    try:
        yield
    finally:
        updater.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await updater
        await app.state.http.aclose()
        await db.engine.dispose()
        log.info("service.stopped")


app = FastAPI(title=config.SERVICE_NAME, lifespan=lifespan)
app.add_middleware(ObservabilityMiddleware)


# --- Business endpoint (contains the injected fault) -----------------------
@app.get("/widgets/{widget_id}")
async def get_widget(widget_id: int, request: Request):
    client: httpx.AsyncClient = request.app.state.http
    try:
        if config.FIX_ENABLED:
            widget, computed, db_wait = await _handle_fixed(client, widget_id)
        else:
            widget, computed, db_wait = await _handle_buggy(client, widget_id)
    except SATimeoutError:
        # The pool is exhausted. Note the response is deliberately generic — from
        # the API alone you cannot tell the DB pool is the culprit (ADR 0002).
        metrics.DB_CHECKOUT_TIMEOUTS.inc()
        log.warning("db.pool_checkout_timeout", widget_id=widget_id)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if widget is None:
        raise HTTPException(status_code=404, detail="widget not found")

    log.info("widget.served", widget_id=widget_id, db_wait_ms=db_wait)
    return {
        "id": widget["id"],
        "name": widget["name"],
        "price": float(widget["price"]),
        "score": computed.get("score"),
        "db_wait_ms": db_wait,
    }


async def _handle_buggy(client, widget_id):
    """BUGGY PATH (ADR 0002): the DB connection is held across the slow call."""
    async with db.acquire_session() as session:
        widget = await _load_widget(session, widget_id)
        # The connection is STILL checked out while we await the slow downstream.
        computed = await call_downstream(client, widget_id)
        await _record_access(session, widget_id)
        return widget, computed, session.info.get("db_wait_ms")


async def _handle_fixed(client, widget_id):
    """FIXED PATH: release the connection before the slow call, reacquire after."""
    async with db.acquire_session() as session:
        widget = await _load_widget(session, widget_id)
        db_wait = session.info.get("db_wait_ms")
    # Connection is back in the pool. The slow call holds no DB resource.
    computed = await call_downstream(client, widget_id)
    async with db.acquire_session() as session:
        await _record_access(session, widget_id)
    return widget, computed, db_wait


async def _load_widget(session, widget_id: int):
    row = (
        await session.execute(
            text("SELECT id, name, price FROM widgets WHERE id = :id"),
            {"id": widget_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _record_access(session, widget_id: int) -> None:
    # Represents the second DB interaction after the downstream call.
    await session.execute(text("SELECT 1"))


# --- Observability endpoints ----------------------------------------------
@app.get("/healthz")
async def healthz():
    """Liveness: the process/event loop is responsive. No dependencies touched."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness: can we actually get a DB connection and round-trip a query?

    During pool exhaustion this begins to fail — distinguishing 'app is dead'
    from 'app cannot get a DB connection' (ADR 0003).
    """
    try:
        async def _probe():
            async with db.acquire_session() as session:
                await session.execute(text("SELECT 1"))

        await asyncio.wait_for(_probe(), timeout=1.0)
    except (asyncio.TimeoutError, SATimeoutError):
        return Response(
            content='{"status":"not_ready","reason":"db_unavailable"}',
            status_code=503,
            media_type="application/json",
        )
    return {"status": "ready", "pool": db.pool_stats()}


@app.get("/metrics")
async def prometheus_metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

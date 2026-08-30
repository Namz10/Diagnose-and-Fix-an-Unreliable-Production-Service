"""Client for the slow downstream dependency (the simulated external API).

The request_id is propagated via the X-Request-ID header so a single request can
be traced across the service boundary (ADR 0003).
"""
from __future__ import annotations

import time

import httpx

from . import config, metrics
from .context import get_request_id
from .tracing import tracer


async def call_downstream(client: httpx.AsyncClient, widget_id: int) -> dict:
    t0 = time.perf_counter()
    with tracer.start_as_current_span("downstream.call") as span:
        span.set_attribute("widget_id", widget_id)
        try:
            resp = await client.get(
                f"{config.DOWNSTREAM_URL}/compute",
                params={"widget_id": widget_id},
                headers={"X-Request-ID": get_request_id() or ""},
                timeout=config.DOWNSTREAM_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            dur = time.perf_counter() - t0
            span.set_attribute("duration_ms", round(dur * 1000, 2))
            metrics.DOWNSTREAM_DURATION.observe(dur)

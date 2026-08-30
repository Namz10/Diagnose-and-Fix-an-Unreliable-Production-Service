"""Request middleware: request IDs, structured access logs, and HTTP metrics.

Implements ADR 0003 layers 1 & 2 at the edge of every request.
"""
from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from . import metrics
from .context import set_request_id
from .logging_conf import get_logger
from .tracing import tracer

log = get_logger()


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        set_request_id(request_id)

        # Bind request_id so every structlog line during this request carries it.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        route = request.url.path
        method = request.method
        label_route = _normalize_route(route)
        metrics.HTTP_IN_FLIGHT.inc()
        t0 = time.perf_counter()
        status = 500
        # Root span; db.checkout and downstream.call nest under it, so a single
        # trace shows where a slow request spent its time.
        with tracer.start_as_current_span("http.request") as span:
            span.set_attribute("http.method", method)
            span.set_attribute("http.route", label_route)
            span.set_attribute("request_id", request_id)
            try:
                response = await call_next(request)
                status = response.status_code
                response.headers["X-Request-ID"] = request_id
                return response
            finally:
                duration = time.perf_counter() - t0
                span.set_attribute("http.status_code", status)
                metrics.HTTP_IN_FLIGHT.dec()
                # Collapse path params to keep metric label cardinality low.
                metrics.HTTP_REQUEST_DURATION.labels(
                    label_route, method, str(status)
                ).observe(duration)
                metrics.HTTP_REQUESTS_TOTAL.labels(
                    label_route, method, str(status)
                ).inc()
                log.info(
                    "request.completed",
                    method=method,
                    path=route,
                    route=label_route,
                    status=status,
                    duration_ms=round(duration * 1000, 2),
                )


def _normalize_route(path: str) -> str:
    # Keep the metric route labels bounded regardless of the widget id.
    if path.startswith("/widgets/"):
        return "/widgets/{id}"
    return path

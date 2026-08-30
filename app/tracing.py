"""OpenTelemetry tracing with a compact console (log) exporter (ADR 0003).

We deliberately export spans to stdout as one-line JSON rather than running a
Jaeger/OTLP backend — it keeps the stack container-free while still producing
real distributed-trace data. Under load the `db.checkout` span balloons while
`downstream.call` stays flat, which is the visual smoking gun for pool
exhaustion.

Spans are created manually at the three points that matter:
    http.request  ->  db.checkout  ->  downstream.call
"""
from __future__ import annotations

import json

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from . import config


def _compact(span) -> str:
    """One line per span so it reads cleanly in `docker compose logs`."""
    ctx = span.get_span_context()
    data = {
        "otel_span": span.name,
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_id": format(span.parent.span_id, "016x") if span.parent else None,
        "duration_ms": round((span.end_time - span.start_time) / 1e6, 2),
        "attributes": dict(span.attributes or {}),
    }
    return json.dumps(data) + "\n"


def configure_tracing() -> None:
    provider = TracerProvider(
        resource=Resource.create({"service.name": config.SERVICE_NAME})
    )
    provider.add_span_processor(
        SimpleSpanProcessor(ConsoleSpanExporter(formatter=_compact))
    )
    trace.set_tracer_provider(provider)


# Proxy tracer; resolves to the real one once configure_tracing() runs.
tracer = trace.get_tracer(config.SERVICE_NAME)

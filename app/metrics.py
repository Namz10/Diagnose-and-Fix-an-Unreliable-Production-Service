"""Prometheus metrics (ADR 0003, layer 2).

Label cardinality is kept deliberately low (bounded route/status label sets) so
the telemetry does not itself become a resource problem.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# End-to-end request latency, split by route + status so we can read p95/p99.
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=("route", "method", "status"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=("route", "method", "status"),
)

# Concurrency actually reached — how many requests are in the handler at once.
HTTP_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "In-flight HTTP requests",
)

# THE key signal for this exercise: pool saturation.
DB_POOL_CONNECTIONS = Gauge(
    "db_pool_connections",
    "SQLAlchemy connection pool state",
    labelnames=("state",),  # checked_out | available | overflow | size
)

# How long requests block waiting to check out a connection.
DB_CHECKOUT_WAIT = Histogram(
    "db_pool_checkout_wait_seconds",
    "Time spent waiting to acquire a DB connection from the pool",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

DB_CHECKOUT_TIMEOUTS = Counter(
    "db_pool_checkout_timeouts_total",
    "Number of times a request failed to acquire a DB connection before timeout",
)

# Downstream latency — to exonerate or implicate the slow external API.
DOWNSTREAM_DURATION = Histogram(
    "downstream_request_duration_seconds",
    "Latency of the downstream dependency call",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

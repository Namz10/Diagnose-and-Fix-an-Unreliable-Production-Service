"""Structured JSON logging via structlog (ADR 0003, layer 1).

Every log line is a JSON object. `request_id` is bound per-request in the
middleware using structlog's contextvars, so it appears automatically on every
event emitted while handling that request — this is what lets us trace a single
slow request end to end.
"""
from __future__ import annotations

import logging

import structlog

from . import config


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = config.SERVICE_NAME):
    return structlog.get_logger(name)

"""Runtime configuration, read from environment variables.

Kept intentionally simple (plain os.getenv) so there is no hidden magic — every
knob that shapes the failure or the fix is visible here.
"""
from __future__ import annotations

import os


def _get_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# --- Database -------------------------------------------------------------
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://app:app@localhost:5432/app",
)

# The bounded resource we deliberately break in ADR 0002. Small on purpose so
# the failure reproduces with modest load.
DB_POOL_SIZE: int = _get_int("DB_POOL_SIZE", 5)
DB_MAX_OVERFLOW: int = _get_int("DB_MAX_OVERFLOW", 0)
# How long a request will block waiting to check out a connection before the
# pool raises a TimeoutError. Kept modest so a saturated pool fails "loudly".
DB_POOL_TIMEOUT: float = _get_float("DB_POOL_TIMEOUT", 5.0)

# --- Downstream (the slow external API) -----------------------------------
DOWNSTREAM_URL: str = os.getenv("DOWNSTREAM_URL", "http://localhost:9000")
DOWNSTREAM_TIMEOUT: float = _get_float("DOWNSTREAM_TIMEOUT", 5.0)

# --- The fault toggle -----------------------------------------------------
# FIX_ENABLED=false  -> the buggy code path (connection held across the slow
#                       downstream call) described in ADR 0002.
# FIX_ENABLED=true   -> the fixed code path (connection released before the
#                       downstream call).
# Same binary, one switch, so the before/after demo is honest.
FIX_ENABLED: bool = _get_bool("FIX_ENABLED", False)

# --- Misc -----------------------------------------------------------------
SERVICE_NAME: str = os.getenv("SERVICE_NAME", "widget-service")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

"""Simulated slow external API (the downstream dependency).

Responds correctly but with realistic, variable latency (~300-800ms, with
occasional spikes). It is healthy and fast enough on its own — the failure in
the widget-service comes from *holding a DB connection while waiting on this*,
not from this service being broken.
"""
from __future__ import annotations

import asyncio
import os
import random

from fastapi import FastAPI

BASE_MS = int(os.getenv("DOWNSTREAM_BASE_MS", "300"))
JITTER_MS = int(os.getenv("DOWNSTREAM_JITTER_MS", "500"))
SPIKE_PROB = float(os.getenv("DOWNSTREAM_SPIKE_PROB", "0.05"))
SPIKE_MS = int(os.getenv("DOWNSTREAM_SPIKE_MS", "1500"))

app = FastAPI(title="downstream-stub")


@app.get("/compute")
async def compute(widget_id: int):
    delay = BASE_MS + random.uniform(0, JITTER_MS)
    if random.random() < SPIKE_PROB:
        delay += SPIKE_MS
    await asyncio.sleep(delay / 1000.0)
    # A deterministic-ish "score" so the response is meaningful.
    return {"widget_id": widget_id, "score": (widget_id * 37) % 100, "delay_ms": round(delay, 1)}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

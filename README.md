# Unreliable Production Service — Diagnose & Fix

A small backend service that works fine under light load but **fails
intermittently under concurrency**. The failure is invisible from the API
response alone; you have to look at the added observability to find it.

This repo is the full exercise: the service, the injected fault, the
observability to diagnose it, and the fix.

**Observability implemented (all six):** structured logs · request IDs · metrics
· traces (OpenTelemetry, console exporter) · health checks · dashboards.

---

## Architecture

```
        ┌────────────┐   GET /widgets/{id}   ┌──────────────────┐
 client │            │ ────────────────────► │  widget-service  │
        └────────────┘                       │   (FastAPI)      │
                                             │  bounded DB pool │
                                             └───┬──────────┬───┘
                          slow HTTP (300-800ms)  │          │  SQL (fast)
                                       ┌──────────▼──┐   ┌───▼────────┐
                                       │ downstream  │   │ PostgreSQL │
                                       │ (stub API)  │   │            │
                                       └─────────────┘   └────────────┘

        Prometheus scrapes /metrics ──► Grafana dashboard
```

- **`widget-service`** — the app under test. One business endpoint plus health
  and metrics endpoints.
- **PostgreSQL** — accessed through a **bounded** SQLAlchemy connection pool
  (`pool_size=5, max_overflow=0`). This bound is the resource that breaks.
- **downstream** — a stub external API that is healthy but slow (variable
  latency). It is *not* broken; the bug is what the app does *while waiting on it*.
- **Prometheus + Grafana** — metrics and a pre-provisioned dashboard.

Each of these was chosen for a specific reason — see the "why" notes inline below.

---

## Quick start

Requires Docker + Docker Compose.

```bash
make up          # build & start everything in the BUGGY configuration
make smoke       # healthz / readyz / one widget — all fine
```

Open:
- App: http://localhost:8000/widgets/1
- Grafana: http://localhost:3000 → dashboard **"Widget Service — Reliability"**
- Prometheus: http://localhost:9090

Reproduce the failure and then fix it — see the two sections below.

---

## The failure (what and why)

**Fault: DB connection-pool exhaustion.** The buggy `GET /widgets/{id}` handler
checks a connection out of the pool, and then — *while still holding it* — awaits
the slow downstream call before releasing it:

```python
async with acquire_session() as session:      # connection checked out
    widget = await load_widget(session, id)    # fast query
    computed = await call_downstream(id)       # 300-800ms — STILL holding it
    await record_access(session, id)
```

- **Light load (1–3 concurrent):** at most a couple of the 5 connections are
  ever in use → every request is fast → looks perfectly healthy.
- **Concurrent load (20–50):** each in-flight request pins a connection for the
  whole downstream duration. Past ~5 overlapping requests the pool is fully
  checked out; further requests block up to `pool_timeout` (5s) then fail. The
  client just sees **intermittent 503s and a latency cliff** — nothing says
  "database pool".

This failure mode was chosen because it is latent under light load, emergent
under concurrency, opaque at the API edge, and only diagnosable with pool-level
observability — the best fit for a "hard to identify from the response" bug.

### See it for yourself

```bash
make load-light   # concurrency=2  -> ~100% success, low latency
make load         # concurrency=30 -> errors appear, p99 spikes to seconds
```

---

## Diagnosing it (as if you didn't know the cause)

This is the runbook an on-call engineer would follow with only the symptom in
hand. Watch the Grafana dashboard while `make load` runs.

1. **Symptom.** Under load, `make load` reports non-200s and p99 in the seconds.
   A single `curl /widgets/1` is still fast. The 503 body says only
   `"Service temporarily unavailable"` — uninformative.

2. **Is the process alive?** `curl /healthz` → `200 ok` throughout. So it is not
   a crash or a wedged event loop. But `curl /readyz` starts returning
   `503 db_unavailable` during load. → the app is up but **can't get a database
   connection**. That points at a dependency/resource, not app logic.

3. **What are the failing requests waiting on?** In the JSON logs, filter to
   failing requests and compare timings:
   ```bash
   docker compose logs app | grep pool_checkout_timeout
   docker compose logs app | grep widget.served   # note db_wait_ms
   ```
   Healthy requests have `db_wait_ms ≈ 0`; under load `db_wait_ms` climbs into
   the thousands while the *query itself* stays fast. Requests are **waiting to
   start**, not running slowly.

4. **Confirm on metrics (the smoking gun).** On the dashboard:
   - **DB pool state**: `checked_out` pinned at `size` (5), `available` at 0.
   - **DB checkout wait p95**: climbing to seconds; **checkout timeouts/s** > 0.
   - **Downstream latency p95**: normal (~0.5s) — so the dependency is *not* the
     problem; we are just *holding a DB connection while waiting on it*.

   → **Root cause: connection-pool exhaustion caused by holding a DB connection
   across the slow downstream call.**

Each signal exists to answer one specific diagnostic question, so the telemetry
is motivated by the failure rather than added for its own sake.

---

## The fix (and proof)

The fix (`FIX_ENABLED=true`) releases the connection **before** the slow call and
reacquires one only for the final write:

```python
async with acquire_session() as session:   # held for ~1ms
    widget = await load_widget(session, id)
# connection returned to the pool
computed = await call_downstream(id)        # holds NO db resource
async with acquire_session() as session:
    await record_access(session, id)
```

Plus: an explicit `pool_timeout` so a saturated pool fails fast and loudly, and
a documented pool-sizing rationale (Little's Law: `pool ≥ arrival_rate × hold_time`).

Verify with the identical load:

```bash
make down
make fixed        # same stack, FIX_ENABLED=true
make load         # concurrency=30
```

Expected: ~100% success, p99 back near downstream latency, and on the dashboard
the **pool `checked_out` no longer pins at 5** and **checkout timeouts stay at 0**.

---

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /widgets/{id}` | Business endpoint (contains the fault). |
| `GET /healthz` | Liveness — process/event loop responsive. |
| `GET /readyz` | Readiness — real DB round-trip with a tight timeout. |
| `GET /metrics` | Prometheus exposition. |

## Layout

```
app/            widget-service (FastAPI): endpoints, db pool, metrics, logging
downstream/     slow external-API stub
db/             Postgres bootstrap SQL
observability/  Prometheus config + Grafana datasource/dashboard (as code)
loadtest/       asyncio load generator to reproduce the failure
docker-compose.yml, Dockerfile, Makefile
```

## Running locally without Docker (optional)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# needs a Postgres reachable at DATABASE_URL and the downstream stub running:
uvicorn downstream.main:app --port 9000 &
DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/app \
DOWNSTREAM_URL=http://localhost:9000 \
uvicorn app.main:app --port 8000
```

## Config knobs

| Env var | Default | Meaning |
|---------|---------|---------|
| `FIX_ENABLED` | `false` | `false` = buggy, `true` = fixed |
| `DB_POOL_SIZE` | `5` | pool size (the bound that breaks) |
| `DB_MAX_OVERFLOW` | `0` | extra connections beyond pool_size |
| `DB_POOL_TIMEOUT` | `5` | seconds to wait for a connection before failing |
| `DOWNSTREAM_BASE_MS` / `_JITTER_MS` / `_SPIKE_PROB` | `300`/`500`/`0.05` | downstream latency profile |

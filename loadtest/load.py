"""Load generator to reproduce the failure on demand (ADR 0002 trigger).

Fires N concurrent workers at GET /widgets/{id} for a fixed duration and prints
a summary (success/error counts + latency percentiles). Under light concurrency
the service is healthy; crank --concurrency up and the pool exhausts.

Usage:
    python loadtest/load.py --url http://localhost:8000 --concurrency 30 --duration 20
"""
from __future__ import annotations

import argparse
import asyncio
import random
import time

import httpx


async def worker(client, base_url, stop_at, latencies, statuses):
    while time.monotonic() < stop_at:
        wid = random.randint(1, 100)
        t0 = time.perf_counter()
        try:
            r = await client.get(f"{base_url}/widgets/{wid}", timeout=30)
            code = r.status_code
        except Exception:
            code = 0  # client-side timeout / connection error
        latencies.append(time.perf_counter() - t0)
        statuses[code] = statuses.get(code, 0) + 1


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))
    return s[k]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--duration", type=int, default=20)
    args = ap.parse_args()

    latencies: list[float] = []
    statuses: dict[int, int] = {}
    stop_at = time.monotonic() + args.duration

    print(
        f"Load: {args.concurrency} workers x {args.duration}s -> {args.url}/widgets/{{id}}"
    )
    limits = httpx.Limits(max_connections=args.concurrency + 10)
    async with httpx.AsyncClient(limits=limits) as client:
        await asyncio.gather(
            *[
                worker(client, args.url, stop_at, latencies, statuses)
                for _ in range(args.concurrency)
            ]
        )

    total = len(latencies)
    ok = statuses.get(200, 0)
    print("\n=== Results ===")
    print(f"requests:   {total}")
    print(f"success200: {ok} ({(100*ok/total if total else 0):.1f}%)")
    for code in sorted(statuses):
        if code != 200:
            label = "client_error/timeout" if code == 0 else str(code)
            print(f"non-200 {label}: {statuses[code]}")
    print(f"latency p50: {pct(latencies,50)*1000:.0f} ms")
    print(f"latency p95: {pct(latencies,95)*1000:.0f} ms")
    print(f"latency p99: {pct(latencies,99)*1000:.0f} ms")
    print(f"latency max: {(max(latencies) if latencies else 0)*1000:.0f} ms")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""Stress SSE + mixed load against a local PatientZero server and watch /health latency.

Opens N watch SSE streams (both tenants), M crew SSE runs, K /api/ask workers, abruptly drops a share of the
streams every few seconds and reopens them, and probes /health every 0.5 s. Prints a JSON summary. Exit 1 when
/health ever took longer than --health-max-s (the server is considered hung). Each simulated
client sends its own X-Forwarded-For so the per-client stream caps do not hide the global load.

  python scripts/stress_streams.py --base http://127.0.0.1:8077 --watch 60 --crew 8 --ask 10 --seconds 120
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time

import httpx

ASKS = ["debug@4.4.2", "who maintains chalk", "qix", "Shai-Hulud", "what changed in the fleet", "chalk@5.6.1"]


async def watch_stream(c: httpx.AsyncClient, base: str, stats: dict, stop: float, tenant: str, ip: str):
    while time.monotonic() < stop:
        try:
            async with c.stream("GET", f"{base}/api/watch/stream", params={"tenant": tenant}, headers={"X-Forwarded-For": ip},
                                timeout=httpx.Timeout(10, read=30)) as r:
                if r.status_code != 200:
                    stats[f"watch_rejected_{r.status_code}"] += 1
                    await asyncio.sleep(1 + random.random())
                    continue
                stats["watch_opened"] += 1
                hold = random.uniform(1, 8)  # then drop the connection abruptly (no clean close of the body)
                t0 = time.monotonic()
                async for _ in r.aiter_raw():
                    if time.monotonic() - t0 > hold:
                        break
                stats["watch_dropped"] += 1
        except Exception as e:  # noqa: BLE001
            stats["watch_errors"] += 1
            stats.setdefault("watch_err_sample", repr(e)[:200])
            await asyncio.sleep(0.5)


async def crew_stream(c: httpx.AsyncClient, base: str, stats: dict, stop: float, tenant: str, ip: str):
    while time.monotonic() < stop:
        try:
            async with c.stream("POST", f"{base}/api/crew/run", json={"query": random.choice(["qix", "debug@4.4.2"]),
                                                                      "tenant": tenant}, headers={"X-Forwarded-For": ip},
                                timeout=httpx.Timeout(10, read=60)) as r:
                if r.status_code != 200:
                    stats[f"crew_rejected_{r.status_code}"] += 1
                    await asyncio.sleep(1)
                    continue
                stats["crew_started"] += 1
                drop = random.random() < 0.5  # half the clients vanish mid-run
                n = 0
                async for _ in r.aiter_raw():
                    n += 1
                    if drop and n >= 2:
                        stats["crew_dropped"] += 1
                        break
                else:
                    stats["crew_done"] += 1
        except Exception as e:  # noqa: BLE001
            stats["crew_errors"] += 1
            stats.setdefault("crew_err_sample", repr(e)[:200])
            await asyncio.sleep(0.5)


async def asker(c: httpx.AsyncClient, base: str, stats: dict, stop: float):
    while time.monotonic() < stop:
        try:
            r = await c.post(f"{base}/api/ask", params={"tenant": random.choice(["acme", "globex"])},
                             json={"query": random.choice(ASKS)}, timeout=30)
            stats["ask_ok" if r.status_code == 200 else "ask_bad"] += 1
            if r.status_code not in (200, 429):
                stats.setdefault("ask_bad_sample", f"{r.status_code} {r.text[:200]}")
        except Exception as e:  # noqa: BLE001
            stats["ask_errors"] += 1
            stats.setdefault("ask_err_sample", repr(e)[:200])


async def health(base: str, stats: dict, stop: float):
    async with httpx.AsyncClient() as c:  # its own connection pool, so client-side limits never hide a hang
        while time.monotonic() < stop:
            t0 = time.monotonic()
            try:
                r = await c.get(f"{base}/health", timeout=15)
                ok = r.status_code == 200
            except Exception:  # noqa: BLE001
                ok = False
            dt = time.monotonic() - t0
            stats["health"].append(dt)
            if not ok:
                stats["health_fail"] += 1
            await asyncio.sleep(0.5)


async def main(a):
    from collections import defaultdict
    stats: dict = defaultdict(int)
    stats["health"] = []
    stop = time.monotonic() + a.seconds
    limits = httpx.Limits(max_connections=None, max_keepalive_connections=0)
    async with httpx.AsyncClient(limits=limits) as c:
        tasks = [health(a.base, stats, stop)]
        tasks += [watch_stream(c, a.base, stats, stop, ("acme", "globex")[i % 2], f"10.1.{i // a.per_ip}.1") for i in range(a.watch)]
        tasks += [crew_stream(c, a.base, stats, stop, ("acme", "globex")[i % 2], f"10.2.{i}.1") for i in range(a.crew)]
        tasks += [asker(c, a.base, stats, stop) for _ in range(a.ask)]
        await asyncio.gather(*tasks)
    # one last probe after the load stops
    async with httpx.AsyncClient() as c:
        t0 = time.monotonic()
        r = await c.get(f"{a.base}/health", timeout=15)
        after = {"status": r.status_code, "s": round(time.monotonic() - t0, 3)}
    h = sorted(stats.pop("health"))
    out = dict(stats)
    out.update(health_probes=len(h), health_p50_s=round(h[len(h) // 2], 3), health_max_s=round(h[-1], 3),
               health_after=after)
    print(json.dumps(out, indent=1))
    return 1 if h[-1] > a.health_max_s or out.get("health_fail") else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8077")
    p.add_argument("--watch", type=int, default=60)
    p.add_argument("--crew", type=int, default=8)
    p.add_argument("--ask", type=int, default=10)
    p.add_argument("--seconds", type=float, default=120)
    p.add_argument("--per-ip", type=int, default=4, help="watch streams sharing one simulated client IP")
    p.add_argument("--health-max-s", type=float, default=2.0)
    raise SystemExit(asyncio.run(main(p.parse_args())))

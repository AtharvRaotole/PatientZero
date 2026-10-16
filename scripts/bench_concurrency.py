"""Concurrency benchmark for PatientZero on FalkorDB.

Two targets:
  db    (default) N threads, each with its own FalkorDB connection, mixed reads (exposure traversals on the tenant
        graph) and writes (WatchEvent-shaped nodes in a throwaway graph bench:<run>, deleted at the end).
  http  N concurrent HTTP clients against a running API (local or live): read-only /api/ask questions in
        deterministic mode (never calls an LLM) plus /api/fleet. Use modest load on shared deploys.

Examples:
  cd backend && uv run python ../scripts/bench_concurrency.py --clients 32 --duration 20 --save
  cd backend && uv run python ../scripts/bench_concurrency.py --target http --url http://localhost:8000 --clients 30 --duration 20
  cd backend && uv run python ../scripts/bench_concurrency.py --target http --url https://patientzero-production-2cb3.up.railway.app --clients 30 --duration 20

--save stores a db-target result where GET /api/bench/last reads it (the FalkorDB the backend is configured for).
--json PATH writes the full result as JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

HTTP_QUESTIONS = ["debug@4.4.2", "chalk@5.6.1", "GHSA-4x49-vf9v-38px", "who to wake for auth-service",
                  "path from img-service to simple-swizzle", "maintainer qix was phished", "Shai-Hulud"]


def _pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))], 2)


def _stats(lat, errs, secs):
    return {"ops": len(lat), "errors": errs, "throughput_ops_s": round(len(lat) / secs, 1) if secs else None,
            "p50_ms": _pct(lat, 50), "p95_ms": _pct(lat, 95), "p99_ms": _pct(lat, 99),
            "max_ms": round(max(lat), 2) if lat else None, "mean_ms": round(sum(lat) / len(lat), 2) if lat else None}


async def http_bench(url: str, clients: int, duration: float, seed: int | None) -> dict:
    import httpx
    url = url.rstrip("/")
    lat = {"ask": [], "fleet": []}
    errs = {"ask": 0, "fleet": 0}
    status_counts: dict[str, int] = {}
    samples: list[str] = []
    rnd = random.Random(seed)
    limits = httpx.Limits(max_connections=clients, max_keepalive_connections=clients)
    hdrs = {"User-Agent": "PatientZero-bench/1.0"}
    if os.getenv("ADMIN_TOKEN"):  # the per-IP ask rate limit exempts the admin token (never printed)
        hdrs["X-Admin-Token"] = os.environ["ADMIN_TOKEN"]
    async with httpx.AsyncClient(timeout=30, limits=limits, headers=hdrs) as c:
        h = await c.get(url + "/health")
        health = h.json() if h.status_code == 200 else {"status": h.status_code}
        stop = time.perf_counter() + duration

        async def worker(i):
            r2 = random.Random(rnd.random())
            while time.perf_counter() < stop:
                op = "fleet" if r2.random() < 0.15 else "ask"
                t = time.perf_counter()
                try:
                    if op == "ask":
                        resp = await c.post(url + "/api/ask", json={"query": r2.choice(HTTP_QUESTIONS), "mode": "deterministic"})
                    else:
                        resp = await c.get(url + "/api/fleet")
                    ms = (time.perf_counter() - t) * 1000
                    status_counts[str(resp.status_code)] = status_counts.get(str(resp.status_code), 0) + 1
                    if resp.status_code == 200:
                        lat[op].append(ms)
                    else:
                        errs[op] += 1
                        if len(samples) < 5:
                            samples.append(f"{op}: HTTP {resp.status_code} {resp.text[:240]}")
                except Exception as e:  # noqa: BLE001
                    errs[op] += 1
                    if len(samples) < 5:
                        samples.append(f"{op}: {type(e).__name__}")

        t0 = time.perf_counter()
        await asyncio.gather(*(worker(i) for i in range(clients)))
        secs = time.perf_counter() - t0
    all_lat = lat["ask"] + lat["fleet"]
    return {"target": "http", "url": url, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "clients": clients, "duration_s": round(secs, 2), "overall": _stats(all_lat, sum(errs.values()), secs),
            "by_op": {k: _stats(v, errs[k], secs) for k, v in lat.items()}, "status_counts": status_counts,
            "error_samples": samples, "health": {k: health.get(k) for k in ("status", "falkordb", "graph", "agent_mode")},
            "writes": "none (read-only HTTP load; server-side mixed runs use POST /api/bench/run)"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=["db", "http"], default="db")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--clients", type=int, default=16)
    ap.add_argument("--duration", type=float, default=10)
    ap.add_argument("--graph", default=None, help="read graph for --target db (default: FALKORDB_GRAPH)")
    ap.add_argument("--write-pct", type=int, default=None)
    ap.add_argument("--retry-busy", action="store_true",
                    help="db target: retry FalkorDB queue-full rejections like the app does (default: count them)")
    ap.add_argument("--procs", type=int, default=1, help="db target: spread clients over N processes (Python GIL)")
    ap.add_argument("--label", default="local")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    if a.target == "http":
        if a.clients > 64:
            ap.error("--clients is capped at 64 for HTTP runs")
        res = asyncio.run(http_bench(a.url, a.clients, a.duration, a.seed))
    else:
        from app.bench import runner
        mix = None
        if a.write_pct is not None:
            rest = 100 - a.write_pct
            mix = {"read_exposure_incident": rest * 0.5, "read_exposure_deep": rest * 0.25,
                   "read_who_to_wake": rest * 0.2, "read_scratch": rest * 0.05, "write_event": a.write_pct}
        res = runner.run(clients=a.clients, duration_s=a.duration, read_graph=a.graph, mix=mix, label=a.label,
                         seed=a.seed, procs=a.procs, retry_busy=a.retry_busy)
        res["target"] = "db"
        if a.save:
            runner.save(res)
    o = res["overall"]
    print(f"{res['target']} {res['clients']} clients x {res['duration_s']} s: {o['ops']} ops, "
          f"{o['throughput_ops_s']} ops/s, p50 {o['p50_ms']} ms, p95 {o['p95_ms']} ms, p99 {o['p99_ms']} ms, "
          f"errors {o['errors']}")
    for k, v in res.get("by_op", {}).items():
        print(f"  {k:24s} {v['ops']:7d} ops  {v['throughput_ops_s']} ops/s  p50 {v['p50_ms']}  p95 {v['p95_ms']}  "
              f"p99 {v['p99_ms']}  err {v['errors']}")
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=str))
    return 0 if o["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

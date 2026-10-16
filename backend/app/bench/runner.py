"""N concurrent clients, each with its own FalkorDB connection, for a fixed duration.

Operation mix (weights are configurable):
  read_exposure_incident  `exposure` over the 19 qix packages at depth 4 (the main product traversal)
  read_exposure_deep      `exposure` for debug at depth 6
  read_who_to_wake        `who_to_wake` for 3 services
  write_event             CREATE a WatchEvent-shaped node in graph bench:<run> (deleted at the end)
  read_scratch            read-after-write count on the same bench graph
Reports throughput, p50/p95/p99/max per operation and overall, and the error count. Nothing in a tenant graph is
written; the scratch graph is always deleted, even on errors.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .. import config, db
from ..db import CATALOGUE

QIX_PKGS = ["ansi-regex", "ansi-styles", "backslash", "chalk", "chalk-template", "color-convert", "color-name",
            "color-string", "debug", "error-ex", "has-ansi", "is-arrayish", "proto-tinker-wc", "simple-swizzle",
            "slice-ansi", "strip-ansi", "supports-color", "supports-hyperlinks", "wrap-ansi"]
DEFAULT_MIX = {"read_exposure_incident": 40, "read_exposure_deep": 20, "read_who_to_wake": 15, "write_event": 20,
               "read_scratch": 5}
MAX_CLIENTS = int(os.getenv("BENCH_MAX_CLIENTS", "64"))
MAX_DURATION_S = float(os.getenv("BENCH_MAX_DURATION_S", "60"))
LAST_KEY = "patientzero:bench:last"
HISTORY_KEY = "patientzero:bench:history"


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))
    return round(xs[k], 2)


def _stats(lat: list[float], errs: int, secs: float) -> dict:
    return {"ops": len(lat), "errors": errs, "throughput_ops_s": round(len(lat) / secs, 1) if secs else None,
            "p50_ms": _pct(lat, 50), "p95_ms": _pct(lat, 95), "p99_ms": _pct(lat, 99),
            "max_ms": round(max(lat), 2) if lat else None,
            "mean_ms": round(sum(lat) / len(lat), 2) if lat else None}


def _new_client():
    """A fresh FalkorDB client (its own connection pool) per bench client, same settings as the app."""
    from falkordb import FalkorDB
    if config.FALKORDB_URL:
        return FalkorDB.from_url(config.FALKORDB_URL)
    kw: dict[str, Any] = dict(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT, socket_timeout=10,
                              socket_connect_timeout=3)
    if config.FALKORDB_USERNAME:
        kw["username"] = config.FALKORDB_USERNAME
    if config.FALKORDB_PASSWORD:
        kw["password"] = config.FALKORDB_PASSWORD
    if config.FALKORDB_SSL:
        kw["ssl"] = True
    return FalkorDB(**kw)


def _drive(read_graph: str, scratch: str, run_id: str, services: list[str], mix: dict[str, float], n_threads: int,
           start_wall: float, duration_s: float, seed: float, offset: int) -> dict:
    """Run n_threads clients in this process from start_wall for duration_s. Returns raw latencies per op."""
    ops = list(mix)
    weights = [mix[o] for o in ops]
    lat: dict[str, list[float]] = {o: [] for o in ops}
    errs: dict[str, int] = {o: 0 for o in ops}
    samples: list[str] = []
    lock = threading.Lock()
    master = random.Random(seed)
    seeds = [master.random() for _ in range(n_threads)]

    def worker(ci: int):
        rnd = random.Random(seeds[ci - offset])
        cl = _new_client()
        rg, sg = cl.select_graph(read_graph), cl.select_graph(scratch)
        try:
            cl.connection.ping()
        except Exception:  # noqa: BLE001
            pass
        ll = {o: [] for o in ops}
        le = {o: 0 for o in ops}
        n = 0
        while time.time() < start_wall:
            time.sleep(0.001)
        stop = time.perf_counter() + duration_s
        while time.perf_counter() < stop:
            op = rnd.choices(ops, weights)[0]
            t = time.perf_counter()
            try:
                if op == "read_exposure_incident":
                    rg.ro_query(CATALOGUE["exposure"]["cypher"], {"pkgs": QIX_PKGS, "depth": 4})
                elif op == "read_exposure_deep":
                    rg.ro_query(CATALOGUE["exposure"]["cypher"], {"pkgs": ["debug"], "depth": 6})
                elif op == "read_who_to_wake":
                    rg.ro_query(CATALOGUE["who_to_wake"]["cypher"],
                                {"services": rnd.sample(services, min(3, len(services)))})
                elif op == "write_event":
                    n += 1
                    sg.query(CATALOGUE["bench_write_event"]["cypher"],
                             {"run": run_id, "id": f"{ci}-{n}", "client": ci, "ts": time.time(),
                              "service": rnd.choice(services)})
                else:
                    sg.ro_query(CATALOGUE["bench_read_scratch"]["cypher"], {"run": run_id})
                ll[op].append((time.perf_counter() - t) * 1000)
            except Exception as e:  # noqa: BLE001
                le[op] += 1
                with lock:
                    if len(samples) < 5:
                        samples.append(f"{op}: {type(e).__name__}: {str(e)[:120]}")
        with lock:
            for o in ops:
                lat[o].extend(ll[o])
                errs[o] += le[o]
        try:
            cl.connection.close()
        except Exception:  # noqa: BLE001
            pass

    threads = [threading.Thread(target=worker, args=(offset + i,), daemon=True) for i in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=duration_s + 60)
    return {"lat": lat, "errs": errs, "samples": samples}


def run(clients: int = 16, duration_s: float = 10.0, read_graph: str | None = None,
        mix: dict[str, float] | None = None, label: str = "local", seed: int | None = None, procs: int = 1) -> dict:
    """procs > 1 spreads the clients over worker processes, so the Python client (GIL) is not the bottleneck."""
    clients = max(1, min(MAX_CLIENTS, int(clients)))
    duration_s = max(1.0, min(MAX_DURATION_S, float(duration_s)))
    procs = max(1, min(int(procs), clients, os.cpu_count() or 1))
    mix = {k: v for k, v in (mix or DEFAULT_MIX).items() if k in DEFAULT_MIX and v > 0} or DEFAULT_MIX
    read_graph = read_graph or config.GRAPH_NAME
    run_id = "B-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]
    scratch = f"bench:{run_id}"
    try:
        g0 = db.client().select_graph(read_graph)
        services = [r[0] for r in g0.ro_query("MATCH (s:Service) RETURN s.name ORDER BY s.name").result_set]
        db.client().select_graph(scratch).query(CATALOGUE["bench_setup"]["cypher"], {"run": run_id})
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"bench setup failed: {type(e).__name__}: {e}") from e
    written = None
    try:
        if not services:
            raise RuntimeError(f"graph {read_graph} has no services")
        rs = random.Random(seed)
        start_wall = time.time() + (0.5 if procs == 1 else 3.0)
        split = [clients // procs + (1 if i < clients % procs else 0) for i in range(procs)]
        offsets = [sum(split[:i]) for i in range(procs)]
        args = [(read_graph, scratch, run_id, services, mix, split[i], start_wall, duration_s, rs.random(), offsets[i])
                for i in range(procs)]
        if procs == 1:
            parts = [_drive(*args[0])]
        else:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(procs, mp_context=mp.get_context("spawn")) as ex:
                parts = list(ex.map(_drive_star, args))
        secs = duration_s
        written = db.client().select_graph(scratch).ro_query(CATALOGUE["bench_count"]["cypher"]).result_set[0][0]
    finally:
        try:
            db.client().select_graph(scratch).delete()
        except Exception:  # noqa: BLE001
            pass
    ops = list(mix)
    lat = {o: [x for p in parts for x in p["lat"][o]] for o in ops}
    errs = {o: sum(p["errs"][o] for p in parts) for o in ops}
    samples = [s for p in parts for s in p["samples"]][:5]
    all_lat = [x for o in ops for x in lat[o]]
    reads = [x for o in ops if o.startswith("read") for x in lat[o]]
    writes = [x for o in ops if o.startswith("write") for x in lat[o]]
    return {
        "id": run_id, "label": label, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "clients": clients, "procs": procs, "duration_s": round(secs, 2), "read_graph": read_graph,
        "scratch_graph": scratch, "scratch_deleted": scratch not in db.client().list_graphs(), "mix": mix,
        "overall": _stats(all_lat, sum(errs.values()), secs),
        "reads": _stats(reads, sum(v for k, v in errs.items() if k.startswith("read")), secs),
        "writes": _stats(writes, sum(v for k, v in errs.items() if k.startswith("write")), secs),
        "by_op": {o: _stats(lat[o], errs[o], secs) for o in ops}, "events_written": written,
        "error_samples": samples, "falkordb": _server_info(),
    }


def _drive_star(a):
    return _drive(*a)


def _server_info() -> dict:
    try:
        info = db.client().connection.info("server")
        return {"redis_version": info.get("redis_version"),
                "modules": [f"{m.get('name')}@{m.get('ver')}" for m in (db.client().connection.module_list() or [])
                            if isinstance(m, dict) and m.get("name") == "graph"]}
    except Exception:  # noqa: BLE001
        return {}


def save(result: dict) -> None:
    try:
        r = db.client().connection
        r.set(LAST_KEY, json.dumps(result, default=str))
        r.lpush(HISTORY_KEY, json.dumps({k: result[k] for k in ("id", "label", "at", "clients", "duration_s", "overall")},
                                        default=str))
        r.ltrim(HISTORY_KEY, 0, 19)
    except Exception:  # noqa: BLE001
        pass


def last() -> dict:
    try:
        r = db.client().connection
        raw = r.get(LAST_KEY)
        hist = [json.loads(x) for x in (r.lrange(HISTORY_KEY, 0, 9) or [])]
        return {"last": json.loads(raw) if raw else None, "history": hist}
    except Exception as e:  # noqa: BLE001
        return {"last": None, "history": [], "error": type(e).__name__}

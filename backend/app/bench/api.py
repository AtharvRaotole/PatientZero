"""HTTP routes for the concurrency benchmark. GET /api/bench/last is public; POST /api/bench/run is admin only."""
from __future__ import annotations

import os
import threading
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..admin import require_admin
from . import runner

router = APIRouter(prefix="/api/bench", tags=["bench"])
SERVER_MAX_CLIENTS = int(os.getenv("BENCH_SERVER_MAX_CLIENTS", "50"))
SERVER_MAX_DURATION_S = float(os.getenv("BENCH_SERVER_MAX_DURATION_S", "30"))
SERVER_PROCS = int(os.getenv("BENCH_SERVER_PROCS", "2"))  # client processes (the Python client is GIL bound)
_state: dict[str, Any] = {"running": None, "error": None}
_lock = threading.Lock()


# End-to-end HTTP reference (scripts/bench_concurrency.py --target http against the live deploy, read-only
# /api/ask in deterministic mode plus /api/fleet, admin token so the per-IP limit does not apply).
# Raw JSON: docs/verification/bench/live-http-c30-queue200.json. Shown next to every in-process number.
HTTP_REFERENCE = {
    "throughput_req_s": 49.4, "clients": 30, "duration_s": 20.44, "requests": 1009, "errors": 0,
    "p50_ms": 615.3, "p95_ms": 1010.5, "p99_ms": 1149.6, "at": "2026-10-16T15:32:16+00:00",
    "target": "live Railway deploy (one uvicorn process, FalkorDB 4.22.1, MAX_QUEUED_QUERIES 200)",
    "what": "read-only HTTP: POST /api/ask (deterministic, no LLM) 85% and GET /api/fleet 15%, from one client box",
    "source": "docs/verification/concurrency.md, docs/verification/bench/live-http-c30-queue200.json",
}


def _scope(r: dict) -> dict:
    """What the in-process number is and is not, in the payload itself (not only in the docs)."""
    ops = ((r.get("overall") or {}).get("throughput_ops_s"))
    retry = bool(r.get("retry_busy"))
    mix = r.get("mix") or {}
    total = sum(float(v) for v in mix.values()) or 100.0
    w = round(100 * float(mix.get("write_event", 20)) / total)
    where = "the deploy's own app process" if r.get("label") == "server" else f"a {r.get('label') or 'local'} run"
    return {
        "kind": "in_process_db",
        "summary": (f"{ops} ops/s is measured in process, straight to FalkorDB (no HTTP): {r.get('clients')} "
                    f"FalkorDB connections ({where}, {r.get('procs') or 1} process(es)), {100 - w}% product reads over "
                    f"{r.get('read_graph')} and {w}% synthetic writes into the scratch graph {r.get('scratch_graph')} "
                    f"(deleted afterwards, not product writes), "
                    + ("with retry on FalkorDB queue-full (busy) rejections like the app. " if retry
                       else "every busy rejection counted as an error. ")
                    + f"End to end over HTTP the live deploy serves about {HTTP_REFERENCE['throughput_req_s']} req/s "
                      f"at {HTTP_REFERENCE['clients']} clients."),
        "transport": "FalkorDB Python client to the database; no HTTP, no FastAPI, no MCP in the timed path",
        "writes": "synthetic WatchEvent-shaped nodes in a scratch graph bench:<run>, deleted after the run",
        "retry_busy": retry,
    }


def _public(r: dict | None) -> dict | None:
    if not r:
        return r
    out = {k: v for k, v in r.items() if k not in ("host",)}
    out["scope"] = _scope(r)
    return out


@router.get("/last")
def bench_last() -> dict[str, Any]:
    """The latest saved benchmark result (local script runs or server-side runs), plus a short history.

    `last.scope` says what the number measures (in-process to FalkorDB, synthetic scratch writes, retry_busy), and
    `http` carries the end-to-end HTTP throughput so the two are never confused."""
    d = runner.last()
    last = _public(d.get("last"))
    return {"last": last, "history": d.get("history", []), "running": _state["running"],
            "last_error": _state["error"],
            "numbers": {"in_process_ops_s": ((last or {}).get("overall") or {}).get("throughput_ops_s"),
                        "in_process_clients": (last or {}).get("clients"),
                        "http_req_s": HTTP_REFERENCE["throughput_req_s"], "http_clients": HTTP_REFERENCE["clients"]},
            "http": HTTP_REFERENCE}


class RunRequest(BaseModel):
    clients: int = Field(30, ge=1, le=64)
    duration_s: float = Field(20, ge=1, le=60)
    write_pct: int | None = Field(None, ge=0, le=90, description="share of writes; reads split the rest")
    retry_busy: bool = Field(True, description="retry FalkorDB queue-full rejections like the app does")


@router.post("/run", dependencies=[Depends(require_admin)], status_code=202)
def bench_run(req: RunRequest | None = None):
    """Start a server-side run against this deploy's FalkorDB (admin). Bounded; one run at a time."""
    req = req or RunRequest()
    with _lock:
        if _state["running"]:
            return JSONResponse(status_code=409, content={"error": "busy", "message": "a benchmark is already running",
                                                          "running": _state["running"]})
        clients = min(req.clients, SERVER_MAX_CLIENTS)
        dur = min(req.duration_s, SERVER_MAX_DURATION_S)
        _state["running"] = {"clients": clients, "duration_s": dur}
        _state["error"] = None
    mix = None
    if req.write_pct is not None:
        w = req.write_pct
        rest = 100 - w
        mix = {"read_exposure_incident": rest * 0.5, "read_exposure_deep": rest * 0.25, "read_who_to_wake": rest * 0.2,
               "read_scratch": rest * 0.05, "write_event": w}

    def go():
        try:
            try:
                res = runner.run(clients=clients, duration_s=dur, mix=mix, label="server", procs=SERVER_PROCS,
                                 retry_busy=req.retry_busy)
            except (OSError, ImportError, RuntimeError) as e:
                if "setup failed" in str(e) or SERVER_PROCS <= 1:
                    raise
                res = runner.run(clients=clients, duration_s=dur, mix=mix, label="server", procs=1, retry_busy=req.retry_busy)
            runner.save(res)
        except Exception as e:  # noqa: BLE001
            _state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            _state["running"] = None

    threading.Thread(target=go, name="bench", daemon=True).start()
    return {"status": "started", "clients": clients, "duration_s": dur, "poll": "/api/bench/last"}

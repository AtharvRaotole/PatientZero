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


def _public(r: dict | None) -> dict | None:
    if not r:
        return r
    return {k: v for k, v in r.items() if k not in ("host",)}


@router.get("/last")
def bench_last() -> dict[str, Any]:
    """The latest saved benchmark result (local script runs or server-side runs), plus a short history."""
    d = runner.last()
    return {"last": _public(d.get("last")), "history": d.get("history", []), "running": _state["running"],
            "last_error": _state["error"]}


class RunRequest(BaseModel):
    clients: int = Field(30, ge=1, le=64)
    duration_s: float = Field(20, ge=1, le=60)
    write_pct: int | None = Field(None, ge=0, le=90, description="share of writes; reads split the rest")


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
                res = runner.run(clients=clients, duration_s=dur, mix=mix, label="server", procs=SERVER_PROCS)
            except (OSError, ImportError, RuntimeError) as e:
                if "setup failed" in str(e) or SERVER_PROCS <= 1:
                    raise
                res = runner.run(clients=clients, duration_s=dur, mix=mix, label="server", procs=1)
            runner.save(res)
        except Exception as e:  # noqa: BLE001
            _state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            _state["running"] = None

    threading.Thread(target=go, name="bench", daemon=True).start()
    return {"status": "started", "clients": clients, "duration_s": dur, "poll": "/api/bench/last"}

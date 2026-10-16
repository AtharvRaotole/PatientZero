"""HTTP routes for the incident crew.

POST /api/crew/run        {query, depth?, session_id?, tenant?}   text/event-stream by default; JSON when the
                          request sends `Accept: application/json` or `?stream=false`.
GET  /api/crew/{run_id}   replay a finished run from the graph.
GET  /api/crew            recent runs in the tenant.
Rate limit: CREW_PER_IP_PER_MIN (default 6) runs per client IP per minute; at most CREW_MAX_CONCURRENT (6) runs at
once, CREW_MAX_CONCURRENT_PER_IP (2) per client (429/503 beyond). Streams send `: ping` every 15 s while waiting.
Deterministic: zero LLM calls.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import config, db, sessions, tenants
from ..byo.ratelimit import RateLimiter, client_ip
from ..streamguard import Slots
from .engine import RUN_RE, Crew, get_run

log = logging.getLogger("patientzero.crew")

router = APIRouter(tags=["crew"])
limiter = RateLimiter(int(os.getenv("CREW_PER_IP_PER_MIN", "6")), 60.0)
# Runs keep going (and writing) after the client disconnects, so concurrency is capped on the run, not the stream.
RUNS = Slots("crew", int(os.getenv("CREW_MAX_CONCURRENT", "6")), int(os.getenv("CREW_MAX_CONCURRENT_PER_IP", "2")))
PING_S = float(os.getenv("CREW_PING_S", "15"))


class CrewRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    depth: int | None = Field(None, ge=1, le=6)
    session_id: str | None = Field(None, max_length=64)
    tenant: str | None = Field(None, max_length=32)


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str, separators=(',', ':'))}\n\n".encode()


def _prepare(req: CrewRequest, request: Request) -> tuple[str, str | None, str, int | None, dict | None]:
    tenant = tenants.validate(req.tenant or tenants.current())
    sid = sessions.validate(req.session_id)
    wait = limiter.check(client_ip(request))
    if wait:
        raise HTTPException(status_code=429, detail=f"crew rate limit: try again in {wait:.0f}s",
                            headers={"Retry-After": str(int(wait) + 1)})
    with tenants.use(tenant):
        q, depth, fu = sessions.resolve_followup(sid, req.query, req.depth)
    return tenant, sid, q, depth, fu


def _record(crew: Crew, result: dict, sid: str | None, original: str, fu: dict | None) -> None:
    if not sid:
        return
    br = next((f for a in result.get("agents") or [] for f in a["findings"] if f["kind"] == "blast_radius"), None)
    ent = next((f for a in result.get("agents") or [] for f in a["findings"] if f["kind"] == "entity"), None)
    r = {"intent": "crew", "outcome": result.get("status"), "depth": crew.depth, "message": result.get("message"),
         "entity_kind": (ent or {}).get("data", {}).get("entity_kind"),
         "entity_key": (ent or {}).get("data", {}).get("entity_key"),
         "exposed_count": len((br or {}).get("data", {}).get("exposed") or []),
         "incident_id": crew.incident_id, "entity": {
             "services": [e["service"] for e in (br or {}).get("data", {}).get("exposed") or []][:25]}}
    if r["entity_kind"] == "maintainer":
        r["entity"]["maintainers"] = [r["entity_key"]]
    elif r["entity_kind"] == "package":
        r["entity"]["packages"] = [r["entity_key"]]
    elif r["entity_kind"] == "advisory":
        r["entity"]["advisories"] = [{"id": r["entity_key"]}]
    elif r["entity_kind"] == "version":
        r["entity"]["package_versions"] = [{"key": r["entity_key"], "in_graph": True}]
    with tenants.use(crew.tenant):
        result["session"] = {k: v for k, v in sessions.record(sid, query=original, resolved=crew.query, r=r,
                                                               kind="crew", crew_run=crew.run_id,
                                                               followup=fu).items() if k != "trace"}
        result["session"]["followup"] = {k: v for k, v in (fu or {}).items() if k != "trace"} or None


def busy_response(why: str) -> JSONResponse:
    return JSONResponse(status_code=503 if why == "global" else 429, headers={"Retry-After": "15"},
                        content={"error": "crew_busy", "scope": why, "detail": "too many crew runs in progress"
                                 + (" from this client" if why == "per_client" else "") + ", retry shortly"})


@router.post("/api/crew/run")
async def crew_run(req: CrewRequest, request: Request, stream: bool = True):
    ip = client_ip(request)
    tenant, sid, q, depth, fu = await asyncio.to_thread(_prepare, req, request)
    why = RUNS.acquire(ip)
    if why:
        return busy_response(why)
    depth = depth or config.DEFAULT_DEPTH
    wants_json = (not stream) or "application/json" in request.headers.get("accept", "")
    if wants_json:
        try:
            crew = Crew(q, depth, tenant, sid)
            result = await crew.run()
            await asyncio.to_thread(_record, crew, result, sid, req.query, fu)
        finally:
            RUNS.release(ip)
        return JSONResponse(json.loads(json.dumps(result, default=str)))

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    crew = Crew(q, depth, tenant, sid, emit=lambda e, d: emit(e, d) if e != "done" else None)

    async def work():
        try:
            result = await crew.run()
            await asyncio.to_thread(_record, crew, result, sid, req.query, fu)
            emit("done", result)
        except Exception as e:  # noqa: BLE001 - the client gets a code and a fixed sentence, the log gets the rest
            log.warning("crew stream %s failed: %r", crew.run_id, e)
            emit("error", {"run_id": crew.run_id, **db.public_error(e)})
        finally:
            RUNS.release(ip)
            emit("__end__", {})

    task = asyncio.create_task(work())  # keeps running (and writing consistently) even if the client leaves
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)

    async def gen():
        if fu:
            yield _sse("followup", {k: v for k, v in fu.items() if k != "trace"})
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=PING_S)
            except asyncio.TimeoutError:
                yield b": ping\n\n"  # heartbeat: keeps proxies from cutting a quiet stream, surfaces dead clients
                continue
            if event == "__end__":
                break
            yield _sse(event, data)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "X-Crew-Run": crew.run_id})


_TASKS: set[asyncio.Task] = set()  # strong refs so a detached run is never garbage collected mid-flight


@router.get("/api/crew")
def crew_list(limit: int = 20) -> dict[str, Any]:
    from ..db import Trace
    tr = Trace()
    return {"tenant": tenants.current(), "runs": tr.run("crew", "crew_list_runs", {"limit": max(1, min(100, limit))}),
            "trace": tr.steps}


@router.get("/api/crew/{run_id}")
def crew_get(run_id: str) -> dict[str, Any]:
    if not RUN_RE.match(run_id):
        raise HTTPException(status_code=400, detail="invalid run id")
    r = get_run(run_id, tenants.current())
    if r is None:
        raise HTTPException(status_code=404, detail=f"no crew run {run_id} in tenant {tenants.current()}")
    return r

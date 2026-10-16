"""HTTP routes for the live OSV watch. Contract: docs/CONTRACT-watch-mcp-bench.md."""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..admin import require_admin
from ..byo.ratelimit import RateLimiter, client_ip
from ..db import GraphUnavailable
from . import core

router = APIRouter(prefix="/api/watch", tags=["watch"])
REPLAY_LIMIT = RateLimiter(int(os.getenv("WATCH_REPLAY_RATE", "6")), 60.0)
MAX_STREAMS = int(os.getenv("WATCH_MAX_STREAMS", "100"))
STREAM_MAX_S = float(os.getenv("WATCH_STREAM_MAX_S", "900"))
_streams = {"open": 0}


def _err(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message, **extra})


@router.get("/feed")
def watch_feed(tenant: str | None = None, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Recent WatchEvents (newest first) for one tenant, with the last polls and their stats."""
    t = core.current_tenant(tenant)
    return core.feed(t, limit)


class PollRequest(BaseModel):
    max_batch: int = Field(core.MAX_BATCH, ge=1, le=200)


@router.post("/poll", dependencies=[Depends(require_admin)])
def watch_poll(req: PollRequest | None = None):
    """Run one bounded poll now (admin). 409 when a poll is already running."""
    try:
        s = core.poll(max_batch=(req.max_batch if req else core.MAX_BATCH), source="manual")
    except RuntimeError as e:
        if str(e) == "busy":
            return _err(409, "busy", "a watch poll is already running")
        raise
    return s


class ReplayRequest(BaseModel):
    advisory: str = Field("debug@4.4.2", min_length=3, max_length=80,
                          description="Advisory id or alias (MAL-, GHSA-, CVE-) or package@version already in the graph")


@router.post("/replay")
def watch_replay(request: Request, req: ReplayRequest | None = None):
    """Re-play a PAST real advisory through the watcher. Labelled replay=true. Rate limited per client."""
    wait = REPLAY_LIMIT.check(client_ip(request))
    if wait:
        return JSONResponse(status_code=429, content={"error": "rate_limited", "message": f"retry in {wait} s",
                                                      "retry_after": wait}, headers={"Retry-After": str(int(wait) + 1)})
    return core.replay((req.advisory if req else "debug@4.4.2"))


@router.get("/status")
def watch_status() -> dict[str, Any]:
    st = core.load_state()
    return {"running": core.LAST["running"], "loop": core.LAST["loop"], "last_poll": core.LAST["poll"],
            "last_error": core.LAST.get("last_error"), "watermark": st.get("watermark"),
            "max_batch": core.MAX_BATCH, "interval_s": core.INTERVAL_S, "depth": core.DEPTH,
            "index_url": core.osv.INDEX_URL, "streams_open": _streams["open"]}


@router.get("/stream")
async def watch_stream(request: Request, tenant: str | None = None):
    """Server-sent events: `event: watch` per WatchEvent and `event: poll` per finished poll, `: ping` every 15 s.
    Starts with `event: hello`. Streams close after WATCH_STREAM_MAX_S; EventSource reconnects on its own."""
    if _streams["open"] >= MAX_STREAMS:
        return _err(503, "too_many_streams", "too many open watch streams, retry shortly")
    try:
        t = await asyncio.to_thread(core.current_tenant, tenant)
    except GraphUnavailable:
        raise
    start_seq = core.BUS.seq

    async def gen():
        _streams["open"] += 1
        seq, t0, last_ping = start_seq, time.monotonic(), time.monotonic()
        try:
            hello = {"tenant": t, "last_poll": core.LAST["poll"], "loop": core.LAST["loop"]}
            yield f"event: hello\ndata: {json.dumps(hello, default=str)}\n\n"
            while time.monotonic() - t0 < STREAM_MAX_S:
                if await request.is_disconnected():
                    break
                for s, e in core.BUS.since(seq):
                    seq = s
                    if e["kind"] == "event" and e.get("tenant") != t:
                        continue
                    kind = "watch" if e["kind"] == "event" else "poll"
                    payload = {k: v for k, v in e.items() if k != "kind"}
                    yield f"id: {s}\nevent: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"
                if time.monotonic() - last_ping > 15:
                    last_ping = time.monotonic()
                    yield ": ping\n\n"
                await asyncio.sleep(1.0)
        finally:
            _streams["open"] -= 1

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

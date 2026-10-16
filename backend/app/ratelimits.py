"""Per-client rate limits for the public endpoints that do real work (graph traversals, writes, optional LLM).

ASK_PER_IP_PER_MIN (default 120; a slider drag sends one ask per step) covers /api/ask, /api/fix, /api/summary and
/api/tools/{name};
APPROVE_PER_IP_PER_MIN (default 10) covers playbook approval. A valid X-Admin-Token skips them (benchmarks, ops),
and so does a direct loopback request without X-Forwarded-For (local dev and the in-process bench; on Railway every
request arrives through the edge proxy, which always adds X-Forwarded-For).
"""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request

from . import config
from .byo.ratelimit import RateLimiter, client_ip

ASK = RateLimiter(int(os.getenv("ASK_PER_IP_PER_MIN", "120")), 60.0)
APPROVE = RateLimiter(int(os.getenv("APPROVE_PER_IP_PER_MIN", "10")), 60.0)


def _exempt(request: Request) -> bool:
    tok = request.headers.get("x-admin-token")
    if tok and config.ADMIN_TOKEN and hmac.compare_digest(tok.encode(), config.ADMIN_TOKEN.encode()):
        return True
    host = request.client.host if request.client else ""
    # "testclient" is Starlette's TestClient peer name, never a real socket address.
    proxied = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip")
    return not proxied and host in ("127.0.0.1", "::1", "testclient")


def _limit(limiter: RateLimiter, what: str):
    def dep(request: Request) -> None:
        if _exempt(request):
            return
        wait = limiter.check(client_ip(request))
        if wait:
            raise HTTPException(status_code=429, detail=f"{what} rate limit: try again in {wait:.0f}s",
                                headers={"Retry-After": str(int(wait) + 1)})
    return dep


ask_limit = _limit(ASK, "ask")
approve_limit = _limit(APPROVE, "approve")

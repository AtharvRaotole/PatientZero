"""HTTP routes for bring-your-own-repo scans."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from ..db import GraphUnavailable
from . import scan
from .ratelimit import RateLimiter, client_ip
from .sources import ByoError, MAX_PACKAGE_JSON

router = APIRouter(prefix="/api/byo", tags=["byo"])
SCAN_LIMIT = RateLimiter(int(os.getenv("BYO_RATE_LIMIT", "5")), 60.0)
SCAN_TIMEOUT_S = float(os.getenv("BYO_SCAN_TIMEOUT", "60"))


class ScanRequest(BaseModel):
    github_url: str | None = Field(None, max_length=300)
    package_json: str | None = Field(None, max_length=MAX_PACKAGE_JSON)
    include_dev: bool = True
    depth: int = Field(scan.DEFAULT_DEPTH, ge=1, le=6)

    @model_validator(mode="after")
    def one_input(self):
        if bool(self.github_url and self.github_url.strip()) == bool(self.package_json and self.package_json.strip()):
            raise ValueError("send exactly one of github_url or package_json")
        return self


def _err(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message, **extra},
                        headers={"Retry-After": str(int(extra["retry_after"]) + 1)} if "retry_after" in extra else None)


@router.post("/scan")
async def byo_scan(req: ScanRequest, request: Request) -> Any:
    wait = SCAN_LIMIT.check(client_ip(request))
    if wait:
        return _err(429, "rate_limited", f"Too many scans: {SCAN_LIMIT.limit} per minute per IP. "
                                         f"Try again in {wait:.0f}s.", retry_after=wait)
    try:
        return await asyncio.wait_for(asyncio.to_thread(
            scan.run_scan, github_url=(req.github_url or "").strip() or None,
            package_json=req.package_json if req.package_json and req.package_json.strip() else None,
            include_dev=req.include_dev, depth=req.depth), timeout=SCAN_TIMEOUT_S)
    except ByoError as e:
        return _err(e.status, e.code, e.message)
    except asyncio.TimeoutError:
        return _err(504, "scan_timeout", f"The scan took longer than {SCAN_TIMEOUT_S:.0f}s. Large dependency trees "
                                         "are cached as they resolve, so retrying usually succeeds.")
    except GraphUnavailable:
        raise  # app-level handler returns the 503


@router.get("/scans")
def byo_scans() -> dict[str, Any]:
    out = []
    for name in scan.list_byo_graphs():
        m = scan.scan_meta(name)
        if m:
            out.append({"graph": name, **{k: m.get(k) for k in ("id", "source", "repo", "created_at", "expires_at",
                                                                  "direct_count", "has_lockfile")}})
    out.sort(key=lambda x: -(x.get("created_at") or 0))
    return {"scans": out, "ttl_seconds": scan.TTL_S, "max_graphs": scan.MAX_GRAPHS}


@router.post("/cleanup")
def byo_cleanup() -> dict[str, Any]:
    return {"deleted": scan.cleanup()}

"""Admin guard for destructive endpoints (demo reset, playbook wipe, BYO cleanup).

Set ADMIN_TOKEN in the environment and send it as the `X-Admin-Token` header. On a public deploy ADMIN_TOKEN must
be set; when it is unset (local development) the endpoints stay open, and /health reports admin_guard=false.
"""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from . import config
from .db import Trace


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    want = config.ADMIN_TOKEN
    if not want:
        return
    if not x_admin_token or not hmac.compare_digest(x_admin_token.encode(), want.encode()):
        raise HTTPException(status_code=401, detail="admin token required (X-Admin-Token header)")


router = APIRouter()


@router.post("/api/admin/reset", dependencies=[Depends(require_admin)])
def admin_reset(include_byo: bool = False) -> dict[str, Any]:
    """Start the demo clean in ONE tenant (?tenant=, default acme): delete Incident/Finding memory, crew runs,
    sessions and Playbooks in that tenant's graph. Other tenants are untouched. BYO scan graphs (a visitor's own
    lockfile) are kept unless ?include_byo=true is passed explicitly."""
    from . import tenants
    from .byo import scan
    tr = Trace()
    inc = tr.run("recall", "reset_memory", write=True)
    pb = tr.run("playbook", "playbook_reset", write=True)
    graphs: list[str] = []
    if include_byo:
        graphs = scan.list_byo_graphs()
        for g in graphs:
            scan.delete_graph(g)
        tr.add("byo", f"deleted {len(graphs)} BYO scan graph(s) (include_byo=true)")
    else:
        tr.add("byo", "BYO scan graphs kept (pass include_byo=true to delete them)")
    return {"tenant": tenants.current(), "incidents_and_findings": inc[0]["deleted"] if inc else 0,
            "playbooks": pb[0]["deleted"] if pb else 0, "byo_graphs": graphs, "byo_deleted": bool(include_byo),
            "trace": tr.steps}

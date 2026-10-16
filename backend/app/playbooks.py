"""Approve-then-remember: approved npm overrides become (:Playbook) nodes that later incidents reuse.

Routes (auto-included by main.py): POST /api/incidents/{id}/approve, GET /api/playbooks,
GET /api/playbooks/match?packages=a,b, DELETE /api/playbooks. Deterministic: no LLM anywhere.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .admin import require_admin
from .db import Trace
from .tools import spec_package

router = APIRouter(tags=["playbooks"])
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")


class ApproveRequest(BaseModel):
    approved_by: str = Field("on-call", min_length=1, max_length=80)
    note: str | None = Field(None, max_length=500)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _shape(pb: dict, **extra) -> dict:
    out = dict(pb)
    for k in ("overrides", "service_patches"):
        try:
            out[k] = json.loads(pb.get(k) or "{}")
        except (TypeError, ValueError):
            out[k] = {}
    out.update(extra)
    return out


STEPS = [
    "Merge each service's own package.json patch (service_patches): direct dependencies get the bumped range in "
    "dependencies, transitive packages get overrides scoped to the compromised major (e.g. ansi-styles@^6)",
    "Delete node_modules and clean the npm cache (npm cache clean --force)",
    "Reinstall, then rebuild any browser bundles from scratch",
    "Redeploy and check CI logs for installs during the attack window",
]


def approve(tr: Trace, incident_id: str, approved_by: str = "on-call", note: str | None = None) -> dict:
    inc = tr.run("playbook", "playbook_incident", {"id": incident_id})
    if not inc:
        raise LookupError(f"incident '{incident_id}' not found")
    existing = tr.run("playbook", "playbook_for_incident", {"id": incident_id})
    if existing:
        return dict(playbook=_shape(existing[0]["playbook"]), created=False)
    i = inc[0]
    try:
        patch = json.loads(i.get("patch") or "{}")
    except (TypeError, ValueError):
        patch = {}
    from .tools import spec_pins
    if not patch or not all(isinstance(v, dict) and "overrides" in v for v in patch.values()):
        raise ValueError(f"incident '{incident_id}' has no install-safe patch to approve (it predates scoped "
                         f"overrides): run Generate fix again")
    service_patches = dict(sorted(patch.items()))
    # Fleet-wide block: transitive overrides only (same-spec overrides for direct deps stay per service).
    overrides = dict(sorted({k: v for per in patch.values() for k, v in per["overrides"].items()
                             if not v.startswith("^")}.items()))
    specs = spec_pins(patch.values())
    services = sorted(patch.keys()) or list(i.get("services") or [])
    kind = "maintainer_compromise" if str(i.get("entity_kind") or "").lower() == "maintainer" else "package_compromise"
    pb_id = f"PB-{_now()[:10]}-{uuid.uuid4().hex[:6]}"
    name = (f"{i.get('entity_key') or 'incident'} compromise: {len(specs)} scoped pin(s) across "
            f"{len(service_patches)} service patch(es)")
    steps = list(STEPS) + ([f"Approver note: {note}"] if note else [])
    tr.run("playbook", "playbook_create", dict(
        id=pb_id, incident_id=incident_id, name=name, trigger_kind=kind, trigger=i.get("trigger") or "",
        approved_at=_now(), approved_by=approved_by, overrides=json.dumps(overrides),
        service_patches=json.dumps(service_patches), services=services,
        steps=steps), write=True)
    tr.run("playbook", "playbook_link_packages",
           {"id": pb_id, "rows": [{"pkg": spec_package(k), "spec": k, "pin": v} for k, v in specs.items()]},
           write=True)
    tr.run("playbook", "playbook_link_advisories", {"id": pb_id}, write=True)
    pb = tr.run("playbook", "playbook_for_incident", {"id": incident_id})[0]["playbook"]
    return dict(playbook=_shape(pb), created=True)


def list_playbooks(tr: Trace, limit: int = 50) -> list[dict]:
    rows = tr.run("playbook", "playbook_list", {"limit": limit})
    return [_shape(r["playbook"], packages=r["packages"], advisories=r["advisories"], reused_by=r["reused_by"])
            for r in rows]


def matching(tr: Trace, packages: list[str]) -> list[dict]:
    if not packages:
        return []
    rows = tr.run("playbook", "playbook_match", {"pkgs": sorted(set(packages))})
    return [_shape(r["playbook"], matched=r["matched"], source_incident=r["source_incident"],
                   source_at=r["source_at"]) for r in rows]


def apply_matching(tr: Trace, incident_id: str, specs: dict[str, str]) -> list[dict]:
    """Called by remediation_plan after a new Incident is written: find and record reusable playbooks.

    Returns each matching playbook with a message and whether its pins agree with today's proposed fix.
    """
    out = []
    for pb in matching(tr, sorted({spec_package(k) for k in specs})):
        # compare scoped specs: the playbook agrees when every spec it shares with today's fix pins the same version
        shared = [m for m in pb["matched"] if m.get("spec") in specs]
        agree = bool(shared) and all(specs[m["spec"]] == m["pin"] for m in shared)
        tr.run("playbook", "playbook_apply", {"incident_id": incident_id, "playbook_id": pb["id"], "at": _now()},
               write=True)
        pb["agrees_with_proposed_fix"] = agree
        pb["message"] = (f"Applying approved playbook {pb['id']} from incident {pb.get('source_incident')} "
                         f"(approved by {pb.get('approved_by')} at {pb.get('approved_at')}): covers "
                         f"{', '.join(m.get('spec') or m['pkg'] for m in pb['matched'])}"
                         + ("" if agree else ". Pins differ from today's proposal; review before applying") + ".")
        out.append(pb)
    return out


# --------------------------------------------------------------------------- routes
def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


@router.post("/api/incidents/{incident_id}/approve")
def api_approve(incident_id: str, req: ApproveRequest | None = None) -> Any:
    if not ID_RE.match(incident_id):
        return _err(400, "bad_request", "invalid incident id")
    req = req or ApproveRequest()
    tr = Trace()
    try:
        r = approve(tr, incident_id, req.approved_by, req.note)
    except LookupError as e:
        return _err(404, "incident_not_found", str(e))
    except ValueError as e:
        return _err(409, "nothing_to_approve", str(e))
    return {**r, "trace": tr.steps}


@router.get("/api/playbooks")
def api_playbooks(limit: int = 50) -> dict[str, Any]:
    tr = Trace()
    return {"playbooks": list_playbooks(tr, max(1, min(200, limit))), "trace": tr.steps}


@router.get("/api/playbooks/match")
def api_playbooks_match(packages: str) -> dict[str, Any]:
    pk = [p.strip() for p in packages.split(",") if p.strip()][:100]
    tr = Trace()
    return {"packages": pk, "playbooks": matching(tr, pk), "trace": tr.steps}


@router.delete("/api/playbooks", dependencies=[Depends(require_admin)])
def api_playbooks_reset() -> dict[str, Any]:
    tr = Trace()
    rows = tr.run("playbook", "playbook_reset", write=True)
    return {"deleted": rows[0]["deleted"] if rows else 0, "trace": tr.steps}

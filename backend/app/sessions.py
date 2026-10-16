"""Persistent conversation history in the graph, with follow-up resolution.

A Session and its Turns live in the tenant's graph. Each Turn is linked ABOUT the nodes it concerned (Maintainer,
Package, Service, Version, Advisory, Incident). A follow-up such as "who should I wake for that?" or "and at depth
6?" is resolved by reading the last Turn and its ABOUT nodes back from FalkorDB, then rewriting the question into
an explicit one that the deterministic router answers. No LLM is involved.

Routes: GET /api/sessions/{id}?tenant=acme
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException

from . import router as qrouter
from . import tenants, tools
from .db import Trace

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
FOLLOWUP_RE = re.compile(
    r"\b(that|this|it|those|them|same|there|again|instead)\b|^\s*(and|what about|how about|now|ok|okay|so|then)\b",
    re.I)
DEPTH_RE = re.compile(r"\bdepth\s*(?:of\s*|=\s*|to\s*)?([1-6])\b|\b([1-6])\s*hops?\b", re.I)
CAMPAIGN_LABEL = {cid: label for _, cid, label in qrouter.CAMPAIGNS}

api = APIRouter(tags=["sessions"])


def validate(session_id: str | None) -> str | None:
    if session_id in (None, ""):
        return None
    if not isinstance(session_id, str) or not SESSION_RE.match(session_id):
        raise ValueError("invalid session_id: use 8 to 64 characters from A-Z, a-z, 0-9, '_' and '-'")
    return session_id


def last_turn(tr: Trace, sid: str) -> dict | None:
    rows = tr.run("sessions", "session_turns", {"sid": sid, "limit": 1}, note="last turn + ABOUT nodes")
    return _turn(rows[0]) if rows else None


def _turn(r: dict) -> dict:
    t = dict(r)
    t["about"] = [a for a in (r.get("about") or []) if a and a.get("kind")]
    return t


def history(sid: str, limit: int = 50) -> dict | None:
    tr = Trace()
    s = tr.run("sessions", "session_get", {"sid": sid})
    if not s:
        return None
    turns = [_turn(r) for r in tr.run("sessions", "session_turns", {"sid": sid, "limit": limit})]
    turns.reverse()
    return {**s[0], "tenant": s[0].get("tenant") or tenants.current(), "history": turns, "trace": tr.steps}


def parse_depth(text: str) -> int | None:
    m = DEPTH_RE.search(text or "")
    return int(m.group(1) or m.group(2)) if m else None


def _anchor(turn: dict) -> dict | None:
    """What 'that' refers to: a Campaign node the last turn is ABOUT (read from the graph), else the turn's trigger,
    else the most specific node it was ABOUT."""
    about = turn.get("about") or []
    camp = [a["key"] for a in about if a.get("kind") == "Campaign" and a.get("key")]
    if camp:
        return {"kind": "campaign", "key": camp[0], "via": "graph"}
    kind, key = turn.get("entity_kind"), turn.get("entity_key")
    if kind and key:
        return {"kind": kind, "key": key}
    for want in ("Incident", "Advisory", "Version", "Maintainer", "Service", "Package"):
        hit = [a["key"] for a in about if a["kind"] == want]
        if hit:
            return {"kind": want.lower(), "key": hit[0], "all": hit}
    return None


def _wake_question(anchor: dict, turn: dict) -> str:
    kind, key = anchor["kind"], anchor["key"]
    if kind == "campaign":
        return f"who should I wake for {CAMPAIGN_LABEL.get(key, key)}"
    services = [a["key"] for a in turn.get("about") or [] if a["kind"] == "Service"]
    if kind == "service" or (not turn.get("entity_kind") and services):
        return f"who should I wake for {', '.join(services or [key])}"
    if kind == "maintainer":
        return f"who should I wake, maintainer {key} was compromised"
    if kind == "version":
        return f"who should I wake, {key} was compromised"
    if kind in ("package", "packages"):
        return f"who should I wake, {key.replace(',', ' and ')} compromised"
    return f"who should I wake for {key}"


def resolve_followup(sid: str | None, text: str, depth: int | None) -> tuple[str, int | None, dict | None]:
    """Rewrite a follow-up into an explicit question using the last turn stored in the graph.

    Returns (question, depth, followup) where followup is None when the question stands on its own."""
    if not sid:
        return text, depth, None
    says_depth = parse_depth(text)
    if not (FOLLOWUP_RE.search(text) or says_depth):
        return text, depth, None
    tr = Trace()
    ent = tools.resolve_entity(text, tr)
    if ent.get("found") or qrouter.campaign_of(text):
        # Names its own subject: not a follow-up, but an explicit "at depth N" still applies.
        return text, (says_depth or depth), None
    intent = qrouter.classify(text)
    if intent == "single_points_of_failure":
        return text, depth, None
    last = last_turn(tr, sid)
    if not last:
        return text, depth, {"resolved": False, "reason": "no earlier turn in this session", "trace": tr.steps}
    anchor = _anchor(last)
    if not anchor and not last.get("resolved_query"):
        return text, depth, {"resolved": False, "reason": "the last turn did not name anything", "trace": tr.steps}
    if intent == "who_to_wake" and anchor:
        q = _wake_question(anchor, last)
    else:
        q = last.get("resolved_query") or last.get("query") or text
    new_depth = says_depth or (depth if depth else last.get("depth"))
    return q, new_depth, {"resolved": True, "from_turn": last["i"], "anchor": anchor, "rewritten": q,
                          "depth": new_depth, "original": text,
                          "reason": (f"'{text}' refers to turn {last['i']} "
                                     + (f"({anchor['kind']} {anchor['key']})" if anchor else "")).strip(),
                          "trace": tr.steps}


def about_refs(r: dict) -> dict[str, list[str]]:
    """Graph nodes a response was about, grouped by label, from the resolved entity and the result."""
    ent = r.get("entity") or {}
    refs: dict[str, list[str]] = {"services": list(ent.get("services") or []),
                                  "packages": list(ent.get("packages") or []),
                                  "maintainers": list(ent.get("maintainers") or []),
                                  "advisories": [a["id"] for a in ent.get("advisories") or [] if a.get("id")],
                                  "versions": [pv["key"] for pv in ent.get("package_versions") or []
                                               if pv.get("in_graph")],
                                  "incidents": [],
                                  "campaigns": [r["entity_key"]] if r.get("entity_kind") == "campaign" else []}
    refs["services"] += [e["service"] for e in (r.get("exposed") or [])[:25] if e.get("service")]
    if (r.get("path") or {}).get("package"):
        refs["packages"].append(r["path"]["package"])
    fix = r.get("fix") or {}
    if fix.get("incident_id"):
        refs["incidents"].append(fix["incident_id"])
    mem = r.get("memory") or {}
    if r.get("outcome") == "answered_from_memory":
        sb = mem.get("seen_before") or {}
        if sb.get("id"):
            refs["incidents"].append(sb["id"])
        elif mem.get("incidents"):
            refs["incidents"].append(mem["incidents"][0].get("id"))
    if r.get("incident_id"):
        refs["incidents"].append(r["incident_id"])
    return {k: sorted({x for x in v if x}) for k, v in refs.items()}


def record(sid: str, *, query: str, resolved: str, r: dict, kind: str = "ask", crew_run: str | None = None,
           followup: dict | None = None) -> dict:
    """Append a turn to the session (creating it on first use) and link it ABOUT the graph nodes."""
    tr = Trace()
    rows = tr.run("sessions", "session_add_turn", dict(
        sid=sid, tenant=tenants.current(), ts=tools.now_iso(), query=query[:500], resolved=resolved[:500],
        intent=r.get("intent"), outcome=r.get("outcome"), depth=r.get("depth"),
        kind_entity=r.get("entity_kind"), key_entity=r.get("entity_key"), message=(r.get("message") or "")[:600],
        turn_kind=kind, crew_run=crew_run, followup_from=(followup or {}).get("from_turn"),
        exposed_count=int(r.get("exposed_count") or 0)), write=True)
    i = rows[0]["i"]
    linked = 0
    for label, keys in about_refs(r).items():
        if keys:
            if label == "campaigns":
                keys = [{"id": k, "label": CAMPAIGN_LABEL.get(k, k)} for k in keys]
            res = tr.run("sessions", f"session_about_{label}", {"sid": sid, "i": i, "keys": keys}, write=True)
            linked += res[0]["linked"] if res else 0
    return {"id": sid, "turn": i, "about_linked": linked, "trace": tr.steps}


@api.get("/api/sessions/{session_id}")
def api_session(session_id: str, limit: int = 50) -> dict[str, Any]:
    try:
        sid = validate(session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    h = history(sid, max(1, min(200, limit)))
    if h is None:
        raise HTTPException(status_code=404, detail=f"no session {sid} in tenant {tenants.current()}")
    return h

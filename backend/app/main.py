"""PatientZero API: FastAPI over FalkorDB. Every answer comes from queries/*.cypher run against the graph."""
from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import admin, catalog, config, db, sessions, tenants, tools
from .ratelimits import ask_limit
from .core import agent
from .domains.patientzero import DOMAIN
from .mcp_agent import wrap_domain
from .db import GraphUnavailable, Trace
from .models import AskRequest, AskResponse, ErrorResponse, FixRequest, SummaryRequest, SummaryResponse

log = logging.getLogger("patientzero")
@contextlib.asynccontextmanager
async def _lifespan(_app):
    """Sync routes run on AnyIO's worker threads (40 by default). Size it to the DB pool so slow traversals under a
    burst cannot leave /health and quick reads queued behind them; the DB pool bounds real concurrency."""
    import anyio.to_thread
    anyio.to_thread.current_default_thread_limiter().total_tokens = int(os.getenv("API_THREADS", "64"))
    yield


app = FastAPI(title="PatientZero API", version="1.0.0", lifespan=_lifespan,
              description="Supply-chain incident agent on FalkorDB. Deterministic tools, no generated Cypher.")
app.add_middleware(tenants.TenantMiddleware)  # ?tenant= or X-Tenant, validated against the allowlist (inner)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_origin_regex=config.CORS_ORIGIN_REGEX,
                   allow_credentials=False,  # no cookies: the admin token is an explicit header
                   allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                   allow_headers=["Content-Type", "Accept", "X-Tenant", "X-Admin-Token", "Last-Event-ID"],
                   expose_headers=["X-Crew-Run", "Retry-After"])

# The agent selects and runs its tools through our own MCP server (MCP_AGENT=off for direct calls).
AGENT_DOMAIN = wrap_domain(DOMAIN)

GRAPH_DOWN = ("graph unavailable: FalkorDB is not reachable, so PatientZero cannot answer. "
              "It never answers from anywhere else. Start it with scripts/start-falkordb.sh.")


@app.exception_handler(GraphUnavailable)
async def _graph_down(_: Request, exc: GraphUnavailable):
    log.warning("graph unavailable: %s", exc)
    return JSONResponse(status_code=503, content=ErrorResponse(error="graph_unavailable", message=GRAPH_DOWN).model_dump())


@app.exception_handler(ValueError)
async def _bad_request(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content=ErrorResponse(error="bad_request", message=str(exc)).model_dump())


@app.exception_handler(Exception)
async def _internal(_: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content=ErrorResponse(
        error="internal_error", message=f"{type(exc).__name__}: {exc}").model_dump())


def fleet_graph_rows(tr: Trace) -> dict:
    nodes = tr.run("fleet_graph", "fleet_graph_nodes")
    edges = tr.run("fleet_graph", "fleet_graph_edges")
    return {"nodes": nodes, "edges": edges}


@app.get("/health")
def health() -> dict[str, Any]:
    p = db.ping()
    return {"status": "ok" if p["ok"] and p.get("graph_loaded") else "degraded",
            "falkordb": "up" if p["ok"] else "down", "graph": p.get("graph"), "tenant": tenants.current(),
            "tenants": p.get("tenants_loaded"),
            "graph_loaded": p.get("graph_loaded", False), "error": p.get("error"),
            "agent_mode": "llm" if agent.llm_available() else "deterministic",
            "llm_model": config.OPENAI_MODEL if agent.llm_available() else None,
            "domain": DOMAIN.name, "tools": DOMAIN.registry.names(), "llm_budget": agent.budget.status(),
            "queries": len(db.CATALOGUE), "admin_guard": bool(config.ADMIN_TOKEN),
            "mcp_agent": _mcp_agent_status()}


def _mcp_agent_status() -> dict:
    from .mcp_agent import domain as _md
    return {**_md.status(), "active": AGENT_DOMAIN is not DOMAIN}


def client_ip(request: Request) -> str:
    """Rightmost X-Forwarded-For entry (added by the edge proxy, so clients cannot spoof it), else the socket peer."""
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "")


def _ask(query: str, depth: int | None, fresh: bool, session: str, fix: bool, mode: str = "auto",
         ip: str = "", session_id: str | None = None, tenant: str | None = None) -> AskResponse:
    """One question, in one tenant's graph. With session_id, follow-ups are resolved from the last turn stored in
    the graph and the turn is appended to the session's history."""
    t = tenants.validate(tenant or tenants.current())
    sid = sessions.validate(session_id)
    with tenants.use(t):
        q, depth, fu = sessions.resolve_followup(sid, query, depth)
        r = agent.run(AGENT_DOMAIN, q, mode=mode, depth=depth, fresh=fresh, session=session or (sid or ""),
                      generate_fix=fix, client_ip=ip)
        r["tenant"] = t
        r["company"] = tenants.company(t)
        if fu:
            r["trace"] = (fu.pop("trace", None) or []) + r["trace"]
            r["followup"] = fu
            if fu.get("resolved"):
                r["query"] = query
                r["resolved_query"] = q
        if sid:
            rec = sessions.record(sid, query=query, resolved=q, r=r, kind="fix" if fix else "ask", followup=fu)
            r["trace"] = r["trace"] + rec.pop("trace")
            r["session"] = rec
    return AskResponse.model_validate(r)


@app.post("/api/ask", response_model=AskResponse, responses={503: {"model": ErrorResponse}},
          dependencies=[Depends(ask_limit)])
def api_ask(req: AskRequest, request: Request) -> AskResponse:
    return _ask(req.query, req.depth, req.fresh, req.session, False, req.mode, client_ip(request),
                req.session_id, req.tenant)


@app.post("/api/fix", response_model=AskResponse, responses={503: {"model": ErrorResponse}},
          dependencies=[Depends(ask_limit)])
def api_fix(req: FixRequest) -> AskResponse:
    """Generate fix: re-runs the investigation, builds overrides + drafts, writes the Incident to the graph."""
    r = _ask(req.query, req.depth, True, req.session, True, session_id=req.session_id, tenant=req.tenant)
    if r.fix is None:
        raise HTTPException(status_code=409, detail=f"Nothing to fix: {r.message}")
    return r


@app.get("/api/tenants")
def api_tenants() -> dict[str, Any]:
    """Tenants on this deploy. Each one is a separate FalkorDB graph with its own memory."""
    p = db.ping()
    loaded = p.get("tenants_loaded") or {}
    return {"default": tenants.DEFAULT, "current": tenants.current(),
            "tenants": [{**t, "loaded": bool(loaded.get(t["id"]))} for t in tenants.listing()]}


@app.post("/api/summary", response_model=SummaryResponse, dependencies=[Depends(ask_limit)])
def api_summary(req: SummaryRequest, request: Request) -> SummaryResponse:
    """Optional AI incident summary (one paragraph) of the deterministic result, name-checked against it."""
    if req.tenant:
        tenants.set_current(req.tenant)
    r = DOMAIN.route(req.query, depth=req.depth, fresh=req.fresh)
    try:
        return SummaryResponse(**agent.summarize(DOMAIN, r, client_ip(request)))
    except agent.budget.BudgetExceeded as e:
        return SummaryResponse(summary=None, error=f"LLM limit: {e}")
    except Exception as e:  # noqa: BLE001 - optional feature
        return SummaryResponse(summary=None, error=f"LLM error: {type(e).__name__}")


@app.get("/api/fleet")
def api_fleet() -> dict[str, Any]:
    tr = Trace()
    services = tools.fleet_overview(tr)
    stats = tr.run("fleet_overview", "graph_stats")
    return {"graph": tenants.graph_name(), "tenant": tenants.current(), "company": tenants.company(),
            "synthetic": True, "services": services, "stats": stats, "trace": tr.steps}


@app.get("/api/fleet/graph")
def api_fleet_graph() -> dict[str, Any]:
    tr = Trace()
    g = fleet_graph_rows(tr)
    return {**g, "trace": tr.steps}


@app.get("/api/incidents")
def api_incidents(limit: int = 20) -> dict[str, Any]:
    tr = Trace()
    return {"incidents": tools.list_incidents(tr, max(1, min(100, limit))), "trace": tr.steps}


@app.get("/api/timeline")
def api_timeline() -> dict[str, Any]:
    """Dated events from the graph (compromise, advisories, incidents, playbooks), oldest first."""
    tr = Trace()
    rows = tr.run("timeline", "timeline_events")
    # UI-safe shape whatever the graph holds: known kind, string title, int count, list of non-null items.
    kinds = {"compromise", "advisory", "campaign", "incident", "playbook", "watch", "watch_replay"}
    rows = [{"kind": r["kind"], "at": str(r["at"]), "title": str(r.get("title") or r["kind"]),
             "count": int(r.get("count") or 0), "items": [str(i) for i in (r.get("items") or []) if i is not None],
             "ref": r.get("ref")}
            for r in rows if r.get("at") and r.get("kind") in kinds]
    rows.sort(key=lambda r: r["at"])
    return {"events": rows, "trace": tr.steps}


@app.get("/api/incidents/{incident_id}/check")
def api_incident_check(incident_id: str) -> dict[str, Any]:
    if len(incident_id) > 80:
        raise ValueError("invalid incident id")
    tr = Trace()
    rows = tr.run("recall", "incident_exposed_count", {"id": incident_id})
    return {"id": incident_id, "exposed_edges": rows[0]["exposed_edges"] if rows else 0}


@app.post("/api/memory/reset", dependencies=[Depends(admin.require_admin)])
def api_reset() -> dict[str, Any]:
    """Demo housekeeping: delete Incident/Finding nodes (agent memory). The data graph is untouched."""
    tr = Trace()
    rows = tr.run("recall", "reset_memory", write=True)
    return {"deleted": rows[0]["deleted"] if rows else 0, "trace": tr.steps}


@app.get("/api/tools")
def api_tools() -> dict[str, Any]:
    return {"domain": DOMAIN.name,
            "tools": [{"name": t.name, "description": t.description, "parameters": t.parameters}
                      for t in DOMAIN.registry.tools.values()],
            "queries": {k: v["file"] for k, v in db.CATALOGUE.items()}}


@app.post("/api/tools/{name}", dependencies=[Depends(ask_limit)])
def api_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = catalog.TOOLS.get(name)
    if not fn:
        raise HTTPException(status_code=404, detail=f"unknown tool '{name[:40]}'")
    if len(args) > 10 or any(isinstance(v, str) and len(v) > 500 or isinstance(v, (list, dict)) and len(v) > 100
                             for v in args.values()):
        raise ValueError("tool arguments too large (max 10 arguments, strings up to 500 characters, lists up to 100)")
    try:
        return fn(**args)
    except TypeError as e:
        raise ValueError(f"bad arguments for {name}: {e}") from e


@app.get("/api/graph/demo")
def graph_demo() -> dict[str, Any]:
    """Kept for the scaffold health check. Returns a tiny real slice of the fleet graph."""
    tr = Trace()
    return {"path": tools.path_between(tr, "img-service", "is-arrayish"), "trace": tr.steps}


app.include_router(admin.router)
app.include_router(sessions.api)
from .crew.api import router as _crew_router  # noqa: E402

app.include_router(_crew_router)

# Routers owned by other modules (BYO scans, playbooks). Included when importable; see
# docs/CONTRACT-byo-playbooks.md. A missing or broken module never takes the core API down.
for _mod in ("app.byo.routes", "app.byo.api", "app.playbooks", "app.playbook_routes", "app.byo.playbooks",
             "app.watch.api", "app.bench.api"):
    try:
        import importlib

        _m = importlib.import_module(_mod)
        if getattr(_m, "router", None) is not None and _m.router not in [r for r in app.router.routes]:
            app.include_router(_m.router)
            log.info("included router from %s", _mod)
    except ModuleNotFoundError as _e:
        if _e.name != _mod:
            log.warning("router %s not loaded: %s", _mod, _e)
    except Exception as _e:  # noqa: BLE001
        log.warning("router %s failed to load: %s", _mod, _e)

# Live OSV watch: scheduled background poll (off under pytest or with WATCH_INTERVAL_S=0). See app/watch.
try:
    from .watch import core as _watch_core

    _watch_core.start_background()
except Exception as _e:  # noqa: BLE001
    log.warning("watch loop not started: %s", _e)


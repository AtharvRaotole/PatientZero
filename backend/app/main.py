"""PatientZero API: FastAPI over FalkorDB. Every answer comes from queries/*.cypher run against the graph."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import catalog, config, db, llm, tools
from .db import GraphUnavailable, Trace
from .models import AskRequest, AskResponse, ErrorResponse, FixRequest
from .router import ask

log = logging.getLogger("patientzero")
app = FastAPI(title="PatientZero API", version="1.0.0",
              description="Supply-chain incident agent on FalkorDB. Deterministic tools, no generated Cypher.")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

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


_known: set[str] | None = None


def known_names() -> set[str]:
    """Package + service names in the graph (for validating the optional LLM sentence)."""
    global _known
    if _known is None:
        tr = Trace()
        g = fleet_graph_rows(tr)
        _known = {n["id"].split("@")[0].lower() for n in g["nodes"]}
    return _known


def fleet_graph_rows(tr: Trace) -> dict:
    nodes = tr.run("fleet_graph", "fleet_graph_nodes")
    edges = tr.run("fleet_graph", "fleet_graph_edges")
    return {"nodes": nodes, "edges": edges}


@app.get("/health")
def health() -> dict[str, Any]:
    p = db.ping()
    return {"status": "ok" if p["ok"] and p.get("graph_loaded") else "degraded",
            "falkordb": "up" if p["ok"] else "down", "graph": config.GRAPH_NAME,
            "graph_loaded": p.get("graph_loaded", False), "error": p.get("error"),
            "llm": "on" if (config.LLM_ENABLED and config.LLM_API_KEY) else "off",
            "queries": len(db.CATALOGUE)}


def _ask(query: str, depth: int | None, fresh: bool, session: str, fix: bool) -> AskResponse:
    r = ask(query, depth=depth, fresh=fresh, session=session, generate_fix=fix)
    if r["outcome"] in ("answered_with_path", "not_in_graph") and r["exposed"]:
        r["llm_summary"] = llm.summarize(r, known_names()) if config.LLM_ENABLED else None
    return AskResponse.model_validate(r)


@app.post("/api/ask", response_model=AskResponse, responses={503: {"model": ErrorResponse}})
def api_ask(req: AskRequest) -> AskResponse:
    return _ask(req.query, req.depth, req.fresh, req.session, False)


@app.post("/api/fix", response_model=AskResponse, responses={503: {"model": ErrorResponse}})
def api_fix(req: FixRequest) -> AskResponse:
    """Generate fix: re-runs the investigation, builds overrides + drafts, writes the Incident to the graph."""
    r = _ask(req.query, req.depth, True, req.session, True)
    if r.fix is None:
        raise HTTPException(status_code=409, detail=f"Nothing to fix: {r.message}")
    return r


@app.get("/api/fleet")
def api_fleet() -> dict[str, Any]:
    tr = Trace()
    services = tools.fleet_overview(tr)
    stats = tr.run("fleet_overview", "graph_stats")
    return {"graph": config.GRAPH_NAME, "synthetic": True, "services": services, "stats": stats, "trace": tr.steps}


@app.get("/api/fleet/graph")
def api_fleet_graph() -> dict[str, Any]:
    tr = Trace()
    g = fleet_graph_rows(tr)
    return {**g, "trace": tr.steps}


@app.get("/api/incidents")
def api_incidents(limit: int = 20) -> dict[str, Any]:
    tr = Trace()
    return {"incidents": tools.list_incidents(tr, max(1, min(100, limit))), "trace": tr.steps}


@app.get("/api/incidents/{incident_id}/check")
def api_incident_check(incident_id: str) -> dict[str, Any]:
    tr = Trace()
    rows = tr.run("recall", "incident_exposed_count", {"id": incident_id})
    return {"id": incident_id, "exposed_edges": rows[0]["exposed_edges"] if rows else 0}


@app.post("/api/memory/reset")
def api_reset() -> dict[str, Any]:
    """Demo housekeeping: delete Incident/Finding nodes (agent memory). The data graph is untouched."""
    tr = Trace()
    rows = tr.run("recall", "reset_memory", write=True)
    return {"deleted": rows[0]["deleted"] if rows else 0, "trace": tr.steps}


@app.get("/api/tools")
def api_tools() -> dict[str, Any]:
    return {"tools": [{"name": n, "description": d} for n, d in catalog.TOOL_DOCS.items()],
            "queries": {k: v["file"] for k, v in db.CATALOGUE.items()}}


@app.post("/api/tools/{name}")
def api_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = catalog.TOOLS.get(name)
    if not fn:
        raise HTTPException(status_code=404, detail=f"unknown tool '{name}'")
    try:
        return fn(**args)
    except TypeError as e:
        raise ValueError(f"bad arguments for {name}: {e}") from e


@app.get("/api/graph/demo")
def graph_demo() -> dict[str, Any]:
    """Kept for the scaffold health check. Returns a tiny real slice of the fleet graph."""
    tr = Trace()
    return {"path": tools.path_between(tr, "img-service", "is-arrayish"), "trace": tr.steps}

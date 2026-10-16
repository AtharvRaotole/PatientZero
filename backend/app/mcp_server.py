"""PatientZero MCP server (stdio). Exposes the 8 SPEC tools plus `investigate`, the agent's composite tool, and the
Track 2 tools crew_run, session_history and list_tenants.

The web agent (/api/ask) is a client of this same server object over the in-memory MCP transport
(app/mcp_agent), so tool selection through MCP is what the product does, not a side door.

Run:  cd backend && uv run python -m app.mcp_server
Claude Desktop / Cursor config: command "uv", args ["--directory", "<repo>/backend", "run", "python", "-m", "app.mcp_server"]
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import catalog

mcp = FastMCP("patientzero")


@mcp.tool(description=catalog.TOOL_DOCS["resolve_entity"])
def resolve_entity(text: str) -> dict:
    return catalog.resolve_entity(text)


@mcp.tool(description=catalog.TOOL_DOCS["incident_scope"])
def incident_scope(maintainer: str | None = None, advisory_id: str | None = None) -> dict:
    return catalog.incident_scope(maintainer=maintainer, advisory_id=advisory_id)


@mcp.tool(description=catalog.TOOL_DOCS["exposure"])
def exposure(package: str, bad_version: str, depth: int = 4) -> dict:
    return catalog.exposure(package, bad_version, depth)


@mcp.tool(description=catalog.TOOL_DOCS["who_to_wake"])
def who_to_wake(service: str) -> dict:
    return catalog.who_to_wake(service)


@mcp.tool(description=catalog.TOOL_DOCS["single_points_of_failure"])
def single_points_of_failure(limit: int = 10) -> dict:
    return catalog.single_points_of_failure(limit)


@mcp.tool(description=catalog.TOOL_DOCS["path_between"])
def path_between(service: str, package: str) -> dict:
    return catalog.path_between(service, package)


@mcp.tool(description=catalog.TOOL_DOCS["remediation_plan"])
def remediation_plan(trigger: str, depth: int = 4) -> dict:
    return catalog.remediation_plan(trigger, depth, session="mcp")


@mcp.tool(description=catalog.TOOL_DOCS["recall"])
def recall(entity: str) -> dict:
    return catalog.recall(entity)


# ---------------------------------------------------------------- crew, sessions, tenants (Track 2)
@mcp.tool(description="Run the four-agent incident crew (Triage, Investigator, Responder, Fixer) for a question. "
                      "Agents hand off through FalkorDB: each writes AgentRun and Finding nodes and the next agent "
                      "reads them back from the graph. Writes crew memory (incident last_seen and delta). "
                      "Args: query, depth 1..6, tenant (default acme), optional session_id.")
async def crew_run(query: str, depth: int = 4, tenant: str | None = None, session_id: str | None = None) -> dict:
    import asyncio

    from . import sessions, tenants
    from .crew.api import _record
    from .crew.engine import Crew
    if not query or len(query) > 500:
        raise ValueError("query must be 1 to 500 characters")
    if not (1 <= int(depth) <= 6):
        raise ValueError("depth must be between 1 and 6")
    t = tenants.for_tool(tenant)
    sid = sessions.validate(session_id)

    def _prep():
        with tenants.use(t):
            return sessions.resolve_followup(sid, query, int(depth))
    q, d, fu = await asyncio.to_thread(_prep)
    from .crew.api import RUNS
    if RUNS.acquire("mcp"):
        raise RuntimeError("too many crew runs in progress, retry shortly")
    try:
        crew = Crew(q, d or int(depth), t, sid)
        result = await crew.run()
        await asyncio.to_thread(_record, crew, result, sid, query, fu)
    finally:
        RUNS.release("mcp")
    result.pop("trace", None)
    result.pop("replay_trace", None)
    return result


@mcp.tool(description="Conversation history of a session in one tenant, read from the graph: every turn with its "
                      "query, intent, outcome and the nodes it was ABOUT. Args: session_id, tenant (default acme).")
def session_history(session_id: str, tenant: str | None = None, limit: int = 50) -> dict:
    from . import sessions, tenants
    t = tenants.for_tool(tenant)
    sid = sessions.validate(session_id)
    if not sid:
        raise ValueError("session_id is required")
    with tenants.use(t):
        h = sessions.history(sid, max(1, min(200, int(limit))))
    if h is None:
        return {"session_id": sid, "tenant": t, "found": False, "history": []}
    h.pop("trace", None)
    return {**h, "found": True}


@mcp.tool(description="Tenants on this deploy. Each tenant is a separate FalkorDB graph with its own fleet, "
                      "incidents, playbooks, sessions and crew runs.")
def list_tenants() -> dict:
    from . import db, tenants
    loaded = db.ping().get("tenants_loaded") or {}
    return {"default": tenants.DEFAULT,
            "tenants": [{**x, "loaded": bool(loaded.get(x["id"]))} for x in tenants.listing()]}


from .mcp_agent.server import register as _register_agent_tools  # noqa: E402

_register_agent_tools(mcp)

if __name__ == "__main__":
    mcp.run()

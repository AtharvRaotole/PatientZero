"""PatientZero MCP server (stdio). Exposes the same 8 tools as the web API.

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


if __name__ == "__main__":
    mcp.run()

"""Tools that the agent needs on the shared MCP server, beyond the 8 SPEC tools in app/mcp_server.py.

`investigate` is the composite tool: the deterministic router's full investigation (resolve, scope, exposure with
npm semver, who to wake, memory, or a fix when generate_fix is true). The agent's MCP client calls it with the
request context (depth, fresh, session) that the model never controls.
"""
from __future__ import annotations

import inspect
import json
from typing import Any

INVESTIGATE_DOC = (
    "Run the full incident investigation for a question (resolve, scope, exposure paths with npm semver, who to "
    "wake, or memory recall). Prefer this for any incident question. Accepts a maintainer, advisory, "
    "package@version, or a named campaign such as 'Shai-Hulud worm'.")

# Arguments set from the request context by the agent, hidden from the LLM's function schema.
CONTEXT_ARGS = ("fresh", "session", "generate_fix", "options")


def investigate(question: str, depth: int | None = None, fresh: bool = False, session: str = "",
                generate_fix: bool = False, options: dict[str, Any] | None = None) -> str:
    from ..domains.patientzero import DOMAIN
    from ..router import ask
    opts: dict[str, Any] = {"depth": depth, "fresh": bool(fresh), "session": session or "",
                            "generate_fix": bool(generate_fix)}
    accepted = inspect.signature(DOMAIN.route).parameters
    takes_kwargs = any(p.kind is p.VAR_KEYWORD for p in accepted.values())
    if takes_kwargs:  # PatientZero.route(text, **opts) forwards to router.ask: keep only what ask accepts
        accepted = inspect.signature(ask).parameters
    for k, v in (options or {}).items():
        if k in accepted:
            opts[k] = v
    # Compact JSON text (the full structured response). FastMCP would pretty-print a dict with indent=2, which
    # costs about a third more bytes and CPU on every agent call; clients json-parse the text either way.
    return json.dumps(DOMAIN.route(question, **opts), separators=(",", ":"), default=str)


def register(mcp) -> None:
    """Idempotently add the agent tools to a FastMCP server instance."""
    existing = {t.name for t in mcp._tool_manager.list_tools()}
    if "investigate" not in existing:
        mcp.add_tool(investigate, name="investigate", description=INVESTIGATE_DOC, structured_output=False)

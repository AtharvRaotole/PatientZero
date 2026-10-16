"""MCP gateway for the deterministic router: each graph tool the router calls is executed with MCP tools/call.

app.router imports `routed_tools as tools`. Outside an MCP chain (crew, sessions, the HTTP tool API, the MCP
server's own tools) every attribute is the plain app.tools function, so behaviour is unchanged. Inside
`chain()` (McpDomain.route), the read tools below go through PatientZero's own MCP server instead:

    resolve_entity, incident_scope, exposure (scope mode, fleet computed server side), who_to_wake (services
    mode), package_maintainers, recall (key mode, or limit mode for the latest incidents), path_between,
    single_points_of_failure

Each call adds a trace step {tool: "mcp", via: "mcp", mcp_tool, params, ms} followed by the Cypher steps the
tool ran server side. A transport failure runs that one tool directly (step via "direct"). A tool error that is
not "graph unavailable" aborts the chain (McpToolError) so the caller can fall back to the composite
`investigate` tool. Left in process on purpose: remediation_plan (writes; the UI's explicit Generate fix) and
the fuzzy maintainer lookup tools._resolve inside infer_maintainer (a ranking helper, not a tool).
"""
from __future__ import annotations

import contextlib
import contextvars
import time
from typing import Any

from .. import tools as _tools

_ACTIVE: contextvars.ContextVar["Chain | None"] = contextvars.ContextVar("pz_mcp_chain", default=None)


class McpToolError(RuntimeError):
    """A chained MCP tool returned an error (not a transport failure)."""


class Chain:
    def __init__(self, intent: str):
        self.intent = intent
        self.calls: list[dict] = []  # {tool, via, ms, args}

    @property
    def via(self) -> str:
        vias = {c["via"] for c in self.calls}
        return "mcp" if vias <= {"mcp"} else ("direct" if vias == {"direct"} else "mixed")


@contextlib.contextmanager
def chain(intent: str):
    """Route the router's graph tools over MCP for the duration of one request, on one shared MCP session."""
    from . import client
    server_ctx = contextvars.copy_context()  # taken before the chain is active: server-side tools run plain
    c = Chain(intent)
    token = _ACTIVE.set(c)
    try:
        try:
            sess = client.ChainSession(server_ctx=server_ctx).__enter__()
        except Exception:  # noqa: BLE001 - no shared session: each call opens its own (and falls back if needed)
            sess = None
        try:
            yield c
        finally:
            if sess is not None:
                sess.__exit__(None, None, None)
    finally:
        _ACTIVE.reset(token)


class _ServerFleet(list):
    """Placeholder for the fleet overview: the MCP exposure tool reads the fleet server side in the same call."""


def _show(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if k == "scope" and isinstance(v, list):
            out[k] = [f"{s.get('package')}@{s.get('bad_version')}" for s in v[:20]] + (
                [f"... {len(v) - 20} more"] if len(v) > 20 else [])
        elif isinstance(v, list) and len(v) > 20:
            out[k] = v[:20] + [f"... {len(v) - 20} more"]
        else:
            out[k] = v
    return out


def _call(c: Chain, tr, name: str, args: dict, direct):
    from . import client
    from .domain import _stats
    args = {k: v for k, v in args.items() if v is not None}
    t0 = time.perf_counter()
    token = _ACTIVE.set(None)  # the server-side tool must run the plain functions, never re-enter MCP
    try:
        res = client.call_tool(name, args)
    except client.McpCallError as e:
        msg = str(e)
        if "graph unavailable" in msg.lower():
            from ..db import GraphUnavailable
            raise GraphUnavailable(msg) from e
        raise McpToolError(f"{name}: {msg[:160]}") from e
    except Exception as e:  # noqa: BLE001 - transport problem: run this one tool directly
        _stats["direct_fallbacks"] += 1
        _stats["last_error"] = f"{name}: {type(e).__name__}"
        reason = f"{type(e).__name__}: {str(e)[:120]}"
        tr.steps.append(dict(tool="mcp", query=None, cypher=None, params=_show(args), ms=0, rows=0,
                             note=f"MCP tools/call {name} failed ({reason}); called the tool function directly",
                             via="direct", mcp_tool=name, mcp_fallback=reason, intent=c.intent))
        c.calls.append({"tool": name, "via": "direct", "ms": 0})
        return direct()
    finally:
        _ACTIVE.reset(token)
    body = res.body if isinstance(res.body, dict) else {}
    if "result" not in body:
        raise McpToolError(f"{name}: unexpected MCP result shape")
    _stats["mcp_calls"] += 1
    _stats["last_ms"] = res.ms
    inner = list(body.get("trace") or [])
    tr.steps.append(dict(tool="mcp", query=None, cypher=None, params=_show(args), ms=res.ms, rows=0,
                         note=f"router intent '{c.intent}': MCP tools/call {name} over the {res.transport} "
                              f"transport ({len(inner)} Cypher step{'s' if len(inner) != 1 else ''} server side)",
                         via="mcp", mcp_tool=name, transport=res.transport, tools_listed=res.tools_listed,
                         list_ms=res.list_ms, intent=c.intent))
    tr.steps.extend(inner)
    c.calls.append({"tool": name, "via": "mcp", "ms": res.ms,
                    "wall_ms": round((time.perf_counter() - t0) * 1000, 2)})
    return body["result"]


class RoutedTools:
    """app.tools, with the router's read tools routed through MCP while a chain is active."""

    def __getattr__(self, item: str) -> Any:
        return getattr(_tools, item)

    def resolve_entity(self, text: str, tr) -> dict:
        c = _ACTIVE.get()
        if c is None:
            return _tools.resolve_entity(text, tr)
        return _call(c, tr, "resolve_entity", {"text": text}, lambda: _tools.resolve_entity(text, tr))

    def incident_scope(self, tr, **kw) -> list[dict]:
        c = _ACTIVE.get()
        if c is None:
            return _tools.incident_scope(tr, **kw)
        return _call(c, tr, "incident_scope", {k: v for k, v in kw.items() if v},
                     lambda: _tools.incident_scope(tr, **kw))

    def fleet_overview(self, tr) -> list[dict]:
        if _ACTIVE.get() is None:
            return _tools.fleet_overview(tr)
        return _ServerFleet()

    def exposure(self, tr, scope: list[dict], depth: int, fleet: list[dict] | None = None, **kw) -> dict:
        c = _ACTIVE.get()
        if c is None or kw or (fleet is not None and not isinstance(fleet, _ServerFleet)):
            if isinstance(fleet, _ServerFleet):
                fleet = None
            return _tools.exposure(tr, scope, depth, fleet, **kw)
        return _call(c, tr, "exposure", {"scope": scope, "depth": int(depth)},
                     lambda: _tools.exposure(tr, scope, depth))

    def who_to_wake(self, tr, services: list[str]) -> list[dict]:
        c = _ACTIVE.get()
        if c is None or not services:
            return _tools.who_to_wake(tr, services)
        return _call(c, tr, "who_to_wake", {"services": list(services)}, lambda: _tools.who_to_wake(tr, services))

    def package_maintainers(self, tr, package: str):
        c = _ACTIVE.get()
        if c is None:
            return _tools.package_maintainers(tr, package)
        return _call(c, tr, "package_maintainers", {"package": package},
                     lambda: _tools.package_maintainers(tr, package))

    def recall(self, tr, entity_key: str) -> list[dict]:
        c = _ACTIVE.get()
        if c is None:
            return _tools.recall(tr, entity_key)
        return _call(c, tr, "recall", {"key": entity_key}, lambda: _tools.recall(tr, entity_key))

    def list_incidents(self, tr, limit: int = 20) -> list[dict]:
        c = _ACTIVE.get()
        if c is None:
            return _tools.list_incidents(tr, limit)
        return _call(c, tr, "recall", {"limit": int(limit)}, lambda: _tools.list_incidents(tr, limit))

    def path_between(self, tr, service: str, package: str):
        c = _ACTIVE.get()
        if c is None:
            return _tools.path_between(tr, service, package)
        return _call(c, tr, "path_between", {"service": service, "package": package},
                     lambda: _tools.path_between(tr, service, package))

    def single_points_of_failure(self, tr, limit: int = 10) -> dict:
        c = _ACTIVE.get()
        if c is None:
            return _tools.single_points_of_failure(tr, limit)
        return _call(c, tr, "single_points_of_failure", {"limit": int(limit)},
                     lambda: _tools.single_points_of_failure(tr, limit))


routed_tools = RoutedTools()

"""Domain wrapper that makes the agent select and execute its tools through MCP.

- Deterministic path: the intent router runs inside gateway.chain(), so each graph tool it picks (resolve_entity,
  then for example recall, incident_scope, exposure, who_to_wake) is executed with tools/call on PatientZero's own
  MCP server, one trace step per call. The composite `investigate` tool is only the fallback when a chained tool
  errors or the request carries options the chain cannot carry.
- LLM path: the function schemas given to the model are built from the MCP server's tools/list, and each tool the
  model picks is executed with tools/call.

Every MCP call adds a trace step {tool: "mcp", via: "mcp", mcp_tool, args, ms, transport}. If MCP itself fails
(transport or protocol), the agent falls back to calling the same Python function directly and the trace says
`via: direct` with the reason. Disable with MCP_AGENT=off.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from . import client
from .server import CONTEXT_ARGS

log = logging.getLogger("patientzero.mcp_agent")

ENABLED = os.getenv("MCP_AGENT", "on").lower() not in ("0", "off", "false", "no")
# Request options the `investigate` MCP tool carries; anything else forces a direct call (never silently dropped).
ROUTE_OPTS = {"depth", "fresh", "session", "generate_fix"}
PASS_OPTS = {"client_ip"}  # used by the agent loop (LLM budget), not by the tool
# Tools the LLM is never offered (writes go through the UI's explicit "Generate fix" button only).
LLM_HIDDEN_TOOLS = {"remediation_plan", "crew_run"}
# Deterministic intent -> the MCP tool that answers it. The router runs inside gateway.chain(), so every graph
# tool it calls (resolve_entity first, then for example incident_scope -> exposure -> who_to_wake) is an MCP
# tools/call with its own trace step; mcp.tools in the response lists the chain. `investigate` is the fallback.
INTENT_TOOL = {"exposure": "exposure", "who_to_wake": "who_to_wake", "path_between": "path_between",
               "single_points_of_failure": "single_points_of_failure", "recall": "recall",
               "resolve": "resolve_entity"}
# Agent-internal arguments (bulk modes used by the router chain), hidden from the LLM's function schemas.
AGENT_ARGS = {"exposure": ("scope",), "who_to_wake": ("services",), "recall": ("key", "limit")}

_stats = {"mcp_calls": 0, "direct_fallbacks": 0, "last_error": None, "last_ms": None}


def status() -> dict:
    return {"enabled": ENABLED, "transport": client.TRANSPORT, **_stats}


def _mcp_step(res: client.CallResult, note: str, **kw) -> dict:
    return dict(tool="mcp", query=None, cypher=None, params=_show_args(res.args), ms=res.ms, rows=0, note=note,
                via="mcp", mcp_tool=res.tool, transport=res.transport, tools_listed=res.tools_listed,
                list_ms=res.list_ms, **kw)


def _show_args(args: dict) -> dict:
    return {k: v for k, v in args.items() if v not in (None, "", False, {})}


def _direct_step(tool: str, reason: str, args: dict | None = None) -> dict:
    return dict(tool="mcp", query=None, cypher=None, params=_show_args(args or {}), ms=0, rows=0,
                note=f"MCP not used for {tool}: {reason}. Called the tool function directly.", via="direct",
                mcp_tool=tool, mcp_fallback=reason)


def _graph_error(msg: str) -> bool:
    return "graph unavailable" in msg.lower()


class McpRegistry:
    """Same interface the agent loop uses on ToolRegistry (tools, names, get, openai_tools, call), backed by MCP."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, item):
        return getattr(self.inner, item)

    def openai_tools(self) -> list[dict]:
        try:
            tools, _, _ = client.list_tools()
        except Exception as e:  # noqa: BLE001
            log.warning("MCP tools/list failed, using the local registry: %s", e)
            _stats["last_error"] = f"tools/list: {type(e).__name__}"
            return self.inner.openai_tools()
        out = []
        for t in tools:
            if t["name"] in LLM_HIDDEN_TOOLS:
                continue
            d = t["description"]
            out.append({"type": "function", "function": {
                "name": t["name"], "description": (d.split(". ")[0].rstrip(".") + ".")[:160],
                "parameters": compact_schema(t["inputSchema"], hide=CONTEXT_ARGS if t["name"] == "investigate"
                                             else AGENT_ARGS.get(t["name"], ()))}})
        return out

    def call(self, name: str, args: dict | None = None, ctx: dict | None = None) -> dict:
        args = dict(args or {})
        ctx = ctx or {}
        if name in LLM_HIDDEN_TOOLS:
            raise ValueError(f"{name} is not available to the model")
        if name == "investigate":
            args = {k: v for k, v in args.items() if k not in CONTEXT_ARGS}
            if args.get("depth") is None and ctx.get("depth"):
                args["depth"] = ctx["depth"]
            args.update(fresh=bool(ctx.get("fresh")), session=ctx.get("session", "") or "")
        t0 = time.perf_counter()
        try:
            res = client.call_tool(name, args)
        except client.McpCallError as e:
            raise ValueError(str(e).replace("Error executing tool ", "")) from e
        except Exception as e:  # noqa: BLE001 - transport problem: run the same tool directly
            _stats["direct_fallbacks"] += 1
            _stats["last_error"] = f"{name}: {type(e).__name__}"
            out = self.inner.call(name, {k: v for k, v in args.items() if k not in CONTEXT_ARGS}, ctx=ctx)
            out["trace"] = [_direct_step(name, f"{type(e).__name__}", args)] + out.get("trace", [])
            return out
        _stats["mcp_calls"] += 1
        _stats["last_ms"] = res.ms
        step = _mcp_step(res, f"MCP tools/call {name} chosen by the LLM from {res.tools_listed} MCP tools")
        body = res.body if isinstance(res.body, dict) else {"result": res.body}
        if name == "investigate":
            body["tool_via"] = "mcp"
            body["mcp"] = {"via": "mcp", "transport": res.transport, "tool": name, "chosen_by": "llm",
                           "args": _show_args(args), "ms": res.ms, "tools_listed": res.tools_listed}
            out = {"response": body, "trace": [step]}
        else:
            out = dict(body)
            out["trace"] = [step] + list(body.get("trace") or [])
        out["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return out


def compact_schema(schema: dict, hide=()) -> dict:
    """FastMCP input schema -> compact OpenAI parameters (no titles, Optional[X] as X, context args hidden)."""
    props = {}
    for k, v in (schema.get("properties") or {}).items():
        if k in hide:
            continue
        v = {kk: vv for kk, vv in v.items() if kk not in ("title", "default")}
        if "anyOf" in v:
            non_null = [x for x in v["anyOf"] if x.get("type") != "null"]
            if len(non_null) == 1:
                v = {**{kk: vv for kk, vv in v.items() if kk != "anyOf"}, **non_null[0]}
        if v.get("type") == "integer" and k == "depth":
            v.update(minimum=1, maximum=6)
        props[k] = v
    out = {"type": "object", "properties": props}
    req = [r for r in schema.get("required") or [] if r in props]
    if req:
        out["required"] = req
    return out


class McpDomain:
    """Wraps a domain adapter; route() and registry calls go through the MCP server. Everything else proxies."""

    def __init__(self, inner):
        self.inner = inner
        self.registry = McpRegistry(inner.registry)

    def __getattr__(self, item):
        return getattr(self.inner, item)

    def route(self, text: str, **opts) -> dict:
        """Run the router with every graph tool it picks executed over MCP (gateway.chain). Falls back to the
        composite `investigate` MCP tool if a chained tool errors, and to direct calls if MCP itself fails."""
        from ..router import classify
        from . import gateway
        intent = classify(text or "")
        extra = {k: v for k, v in opts.items() if k not in ROUTE_OPTS | PASS_OPTS and v not in (None, "", False)}
        if extra:  # options the chain cannot carry: the composite tool forwards what ask accepts
            return self._investigate(text, opts, intent, "route options beyond depth/fresh/session/generate_fix")
        t0 = time.perf_counter()
        try:
            with gateway.chain(intent) as ch:
                r = self.inner.route(text, **opts)
        except gateway.McpToolError as e:
            return self._investigate(text, opts, intent, str(e))
        final = r.get("intent") or intent
        names = [c["tool"] for c in ch.calls]
        r["tool_via"] = "mcp" if ch.via == "mcp" else "direct"  # any tool that fell back makes it "direct"
        r["mcp"] = {"via": ch.via, "transport": client.TRANSPORT, "intent": final,
                    "tool": INTENT_TOOL.get(final, names[-1] if names else "resolve_entity"), "tools": names,
                    "calls": len(names), "ms": round(sum(c["ms"] for c in ch.calls), 2),
                    "wall_ms": round((time.perf_counter() - t0) * 1000, 2)}
        return r

    def llm_meta(self, trace: list[dict]) -> dict:
        """The tool_via/mcp block for an LLM-mode answer, built from the MCP steps in its trace (the deterministic
        pre-run chain and every tool the model chose), so both modes report the same fields."""
        steps = [s for s in trace if s.get("tool") == "mcp"]
        if not steps:
            return {"tool_via": "direct", "mcp": {"via": "none", "transport": client.TRANSPORT, "tools": [],
                                                   "calls": 0, "chosen_by": "llm"}}
        names = [s.get("mcp_tool") for s in steps]
        chosen = [s.get("mcp_tool") for s in steps if "chosen by the LLM" in (s.get("note") or "")]
        vias = {s.get("via") for s in steps}
        via = "mcp" if vias <= {"mcp"} else ("direct" if vias == {"direct"} else "mixed")
        intent = next((s.get("intent") for s in steps if s.get("intent")), None)
        return {"tool_via": "mcp" if via == "mcp" else "direct",
                "mcp": {"via": via, "transport": client.TRANSPORT, "intent": intent,
                        "tool": chosen[-1] if chosen else (INTENT_TOOL.get(intent) or names[-1]),
                        "tools": names, "llm_tools": chosen, "calls": len(names), "chosen_by": "llm",
                        "ms": round(sum(float(s.get("ms") or 0) for s in steps), 2)}}

    def _investigate(self, text: str, opts: dict, intent: str, why: str) -> dict:
        """Fallback: the whole question through the composite `investigate` MCP tool."""
        tool = "investigate"
        extra = {k: v for k, v in opts.items() if k not in ROUTE_OPTS | PASS_OPTS and v not in (None, "", False)}
        args: dict[str, Any] = {"question": text}
        for k in ROUTE_OPTS:
            if opts.get(k) not in (None, "", False):
                args[k] = opts[k]
        if extra:
            args["options"] = extra
        try:
            res = client.call_tool(tool, args)
        except client.McpCallError as e:
            if _graph_error(str(e)):
                from ..db import GraphUnavailable
                raise GraphUnavailable(str(e)) from e
            return self._direct(text, opts, tool, f"tool error over MCP ({str(e)[:120]})", args)
        except Exception as e:  # noqa: BLE001
            return self._direct(text, opts, tool, f"{type(e).__name__}: {str(e)[:120]}", args)
        if not isinstance(res.body, dict) or "outcome" not in res.body:
            return self._direct(text, opts, tool, "unexpected MCP result shape", args)
        _stats["mcp_calls"] += 1
        _stats["last_ms"] = res.ms
        r = res.body
        r.setdefault("trace", [])
        r["trace"].insert(0, _mcp_step(res, f"fallback to the composite MCP tool investigate ({why[:120]}); "
                                            f"tools/call over the {res.transport} transport", intent=intent))
        r["tool_via"] = "mcp"
        r["mcp"] = {"via": "mcp", "transport": res.transport, "tool": tool, "intent": r.get("intent") or intent,
                    "tools": [tool], "calls": 1, "fallback": why[:160], "args": _show_args(args), "ms": res.ms,
                    "tools_listed": res.tools_listed}
        return r

    def _direct(self, text: str, opts: dict, tool: str, reason: str, args: dict) -> dict:
        _stats["direct_fallbacks"] += 1
        _stats["last_error"] = reason
        log.warning("MCP route fallback: %s", reason)
        r = self.inner.route(text, **opts)
        r.setdefault("trace", []).insert(0, _direct_step(tool, reason, args))
        r["tool_via"] = "direct"
        r["mcp"] = {"via": "direct", "tool": tool, "fallback": reason}
        return r


def wrap_domain(domain):
    """The agent's domain: MCP-backed when MCP_AGENT is on (default) and the SDK imports, else the domain itself."""
    if not ENABLED:
        return domain
    try:
        from ..mcp_server import mcp  # noqa: F401 - the server must import for MCP mode
    except Exception as e:  # noqa: BLE001
        log.warning("MCP agent mode off: server import failed: %s", e)
        _stats["last_error"] = f"server import: {type(e).__name__}"
        return domain
    return McpDomain(domain)

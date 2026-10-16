"""Generic agent loop over a domain adapter.

Modes:
  deterministic  the domain's regex/keyword router picks a fixed tool sequence (no LLM, always available)
  llm            OpenAI tool calling over the same registry. The model only chooses tools and arguments; it never
                 writes Cypher. Used when OPENAI_API_KEY is set (AGENT_MODE=auto) unless the request forces
                 deterministic. Any LLM failure falls back to the deterministic router, and the trace says so.
"""
from __future__ import annotations

import json
import time
from typing import Any, Protocol

import httpx

from .. import config
from .registry import ToolRegistry

MAX_STEPS = 6


class Domain(Protocol):
    name: str
    registry: ToolRegistry
    system_prompt: str

    def route(self, text: str, **opts) -> dict: ...           # deterministic answer (full structured response)
    def allowed_names(self, response: dict) -> set[str]: ...  # names the LLM sentence may mention
    def known_names(self) -> set[str]: ...                    # every entity name in the graph


def llm_available() -> bool:
    return bool(config.OPENAI_API_KEY) and config.AGENT_MODE != "deterministic"


def run(domain: Domain, text: str, *, mode: str = "auto", **opts) -> dict:
    want_llm = mode == "llm" or (mode == "auto" and config.AGENT_MODE in ("auto", "llm"))
    if want_llm and llm_available() and not opts.get("generate_fix"):
        try:
            return _run_llm(domain, text, **opts)
        except Exception as e:  # noqa: BLE001 - fall back, never fail the user because of the LLM
            r = domain.route(text, **opts)
            r["trace"].insert(0, _step("llm", f"LLM unavailable ({type(e).__name__}: {e}); deterministic fallback"))
            r["mode"] = "deterministic"
            return r
    r = domain.route(text, **opts)
    r["mode"] = "deterministic"
    return r


def _step(tool: str, note: str, ms: float = 0, **kw) -> dict:
    return dict(tool=tool, query=None, cypher=None, params=kw.pop("params", {}), ms=ms, rows=0, note=note, **kw)


def _chat(messages: list[dict], tools: list[dict]) -> dict:
    r = httpx.post(f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions", timeout=config.LLM_TIMEOUT,
                   headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                   json={"model": config.LLM_MODEL, "temperature": 0, "messages": messages, "tools": tools,
                         "tool_choice": "auto"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def _run_llm(domain: Domain, text: str, **opts) -> dict:
    t0 = time.perf_counter()
    trace: list[dict] = []
    structured: dict | None = None
    messages: list[dict[str, Any]] = [{"role": "system", "content": domain.system_prompt},
                                      {"role": "user", "content": text}]
    final = ""
    for _ in range(MAX_STEPS):
        t = time.perf_counter()
        msg = _chat(messages, domain.registry.openai_tools())
        calls = msg.get("tool_calls") or []
        trace.append(_step("llm", f"{config.LLM_MODEL} chose " + (", ".join(c["function"]["name"] for c in calls)
                                                                   or "to answer"),
                           ms=round((time.perf_counter() - t) * 1000, 2)))
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        if not calls:
            final = (msg.get("content") or "").strip()
            break
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
                out = domain.registry.call(name, args, ctx=opts)
                trace.extend(out.get("trace", []))
                if isinstance(out.get("response"), dict):
                    structured = out["response"]
                    trace.extend(structured.get("trace", []))
                payload = out.get("result", out.get("response", {}))
                content = json.dumps(_summarize_for_llm(payload), default=str)[:12000]
            except Exception as e:  # noqa: BLE001 - report the tool error back to the model
                trace.append(_step(name, f"tool error: {e}", error=str(e)))
                content = json.dumps({"error": str(e)})
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})
    if structured is None:
        structured = domain.route(text, **opts)
        trace.append(_step("agent", "structured panels filled by the deterministic router"))
        trace.extend(structured["trace"])
    r = dict(structured)
    r["trace"] = trace
    r["mode"] = "llm"
    r["llm_model"] = config.LLM_MODEL
    unverified = unverified_names(final, domain.allowed_names(r), domain.known_names()) if final else []
    r["llm_summary"] = final or None
    r["llm_unverified"] = unverified
    if unverified:
        r["trace"].append(_step("llm", f"flagged names not in the graph result: {', '.join(unverified)}"))
    r["total_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return r


def _summarize_for_llm(payload: Any) -> Any:
    """Trim bulky fields (Cypher, full path lists) before sending tool results back to the model."""
    if isinstance(payload, dict):
        return {k: _summarize_for_llm(v) for k, v in payload.items() if k not in ("trace", "cypher", "graph")}
    if isinstance(payload, list):
        return [_summarize_for_llm(x) for x in payload[:40]]
    return payload


def unverified_names(text: str, allowed: set[str], known: set[str]) -> list[str]:
    """Graph entity names (packages/services) mentioned in LLM text that are NOT in the tool results."""
    import re
    from ..tools import STOPWORDS
    allowed = {a.lower() for a in allowed}
    bad: list[str] = []
    for tok in re.findall(r"@?[a-z0-9][a-z0-9._/-]*[a-z0-9]", text.lower()):
        base = tok.rsplit("@", 1)[0] if "@" in tok[1:] else tok
        if base in STOPWORDS or len(base) < 3:
            continue
        if base in known and base not in allowed and base not in bad:
            bad.append(base)
    return bad


def summarize(domain: Domain, response: dict) -> dict:
    """One-paragraph AI incident summary of a deterministic result, name-checked against that result."""
    if not llm_available():
        return {"summary": None, "unverified": [], "model": None, "error": "LLM not configured (OPENAI_API_KEY)"}
    facts = json.dumps(_summarize_for_llm({k: response.get(k) for k in (
        "query", "outcome", "message", "depth", "trigger", "scope", "exposed", "safe", "clean", "wake", "memory",
        "spof", "path", "fix")}), default=str)[:14000]
    msgs = [{"role": "system", "content": domain.summary_prompt},
            {"role": "user", "content": f"Graph result (JSON):\n{facts}"}]
    t = time.perf_counter()
    r = httpx.post(f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions", timeout=config.LLM_TIMEOUT,
                   headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                   json={"model": config.LLM_MODEL, "temperature": 0.2, "messages": msgs})
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()
    return {"summary": text, "model": config.LLM_MODEL, "ms": round((time.perf_counter() - t) * 1000, 2),
            "unverified": unverified_names(text, domain.allowed_names(response), domain.known_names())}

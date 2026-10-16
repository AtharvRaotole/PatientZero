"""Generic agent loop over a domain adapter.

Cost rules: the deterministic router runs FIRST; the LLM is called only when the domain says the router was not
confident (or the request forces mode=llm). Token caps per call, answers cached per (question, graph, options).

Modes:
  deterministic  the domain's regex/keyword router picks a fixed tool sequence (no LLM, always available)
  llm            OpenAI tool calling over the same registry. The model only chooses tools and arguments; it never
                 writes Cypher. Used when OPENAI_API_KEY is set (AGENT_MODE=auto) unless the request forces
                 deterministic. Any LLM failure falls back to the deterministic router, and the trace says so.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
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
    def confident(self, response: dict) -> bool: ...          # did the deterministic router understand it?
    def allowed_names(self, response: dict) -> set[str]: ...  # names the LLM sentence may mention
    def known_names(self) -> set[str]: ...                    # every entity name in the graph


def llm_available() -> bool:
    return bool(config.OPENAI_API_KEY) and config.AGENT_MODE != "deterministic"


def run(domain: Domain, text: str, *, mode: str = "auto", **opts) -> dict:
    llm_ok = llm_available() and not opts.get("generate_fix") and mode != "deterministic"
    if mode == "llm" and llm_ok:
        return _llm_or_fallback(domain, text, **opts)
    r = domain.route(text, **opts)
    r["mode"] = "deterministic"
    if llm_ok and mode == "auto" and config.AGENT_MODE != "deterministic" and not domain.confident(r):
        r2 = _llm_or_fallback(domain, text, **opts)
        r2["trace"].insert(0, _step("agent", "deterministic router not confident; asked the LLM to pick tools"))
        return r2
    r["trace"].append(_step("agent", "LLM not used: deterministic router answered" if llm_available()
                            else "LLM not configured: deterministic router", llm_used=False))
    return r


def _llm_or_fallback(domain: Domain, text: str, **opts) -> dict:
    key = _cache_key("ask", text, opts)
    hit = _cache_get(key)
    if hit is not None:
        hit = json.loads(json.dumps(hit))
        hit["trace"].insert(0, _step("llm", "cached LLM answer (no API call)", llm_used=False, cached=True))
        return hit
    try:
        r = _run_llm(domain, text, **opts)
        _cache_put(key, r)
        return r
    except Exception as e:  # noqa: BLE001 - never fail the user because of the LLM
        r = domain.route(text, **opts)
        r["trace"].insert(0, _step("llm", f"LLM unavailable ({type(e).__name__}); deterministic fallback"))
        r["mode"] = "deterministic"
        return r


# ----------------------------------------------------------------------------- cache
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _norm(text: str) -> str:
    return " ".join(text.lower().split()).strip(" ?!.")


def _cache_key(kind: str, text: str, opts: dict) -> str:
    raw = json.dumps([kind, _norm(text), config.GRAPH_NAME, config.OPENAI_MODEL, opts.get("depth"),
                      bool(opts.get("fresh"))])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _disk() -> Path | None:
    return Path(config.LLM_CACHE_FILE) if config.LLM_CACHE_FILE else None


def _cache_get(key: str) -> dict | None:
    with _lock:
        if not _cache and _disk() and _disk().exists():
            try:
                _cache.update({k: tuple(v) for k, v in json.loads(_disk().read_text()).items()})
            except (OSError, ValueError):
                pass
        v = _cache.get(key)
    if v and time.time() - v[0] < config.LLM_CACHE_TTL:
        return v[1]
    return None


def _cache_put(key: str, value: dict) -> None:
    with _lock:
        _cache[key] = (time.time(), value)
        d = _disk()
        if d:
            try:
                d.parent.mkdir(parents=True, exist_ok=True)
                fresh = {k: v for k, v in _cache.items() if time.time() - v[0] < config.LLM_CACHE_TTL}
                d.write_text(json.dumps(fresh, default=str))
            except OSError:
                pass


def _step(tool: str, note: str, ms: float = 0, **kw) -> dict:
    return dict(tool=tool, query=None, cypher=None, params=kw.pop("params", {}), ms=ms, rows=0, note=note, **kw)


def _chat(messages: list[dict], tools: list[dict] | None, max_tokens: int) -> tuple[dict, dict]:
    body: dict[str, Any] = {"model": config.OPENAI_MODEL, "temperature": 0, "messages": messages,
                            "max_tokens": max_tokens}
    if tools:
        body.update(tools=tools, tool_choice="auto")
    r = httpx.post(f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions", timeout=config.LLM_TIMEOUT,
                   headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"}, json=body)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"], data.get("usage") or {}


def _run_llm(domain: Domain, text: str, **opts) -> dict:
    t0 = time.perf_counter()
    trace: list[dict] = []
    structured: dict | None = None
    messages: list[dict[str, Any]] = [{"role": "system", "content": domain.system_prompt},
                                      {"role": "user", "content": text}]
    final = ""
    total_tokens = 0
    for _ in range(MAX_STEPS):
        t = time.perf_counter()
        msg, usage = _chat(messages, domain.registry.openai_tools(), config.LLM_MAX_TOKENS_TOOLS)
        calls = msg.get("tool_calls") or []
        tokens = usage.get("total_tokens", 0)
        total_tokens += tokens
        trace.append(_step("llm", f"{config.OPENAI_MODEL} chose " + (", ".join(c["function"]["name"] for c in calls)
                                                                    or "to answer") + f" ({tokens} tokens)",
                           ms=round((time.perf_counter() - t) * 1000, 2), llm_used=True, tokens=tokens))
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
    r["llm_model"] = config.OPENAI_MODEL
    r["llm_tokens"] = total_tokens
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
    key = _cache_key("summary", facts, {})
    hit = _cache_get(key)
    if hit is not None:
        return {**hit, "cached": True}
    t = time.perf_counter()
    msg, usage = _chat(msgs, None, config.LLM_MAX_TOKENS_SUMMARY)
    text = (msg.get("content") or "").strip()
    out = {"summary": text, "model": config.OPENAI_MODEL, "ms": round((time.perf_counter() - t) * 1000, 2),
           "tokens": usage.get("total_tokens", 0),
           "unverified": unverified_names(text, domain.allowed_names(response), domain.known_names())}
    _cache_put(key, out)
    return out

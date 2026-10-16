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
import re
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from .. import config
from . import budget
from .registry import ToolRegistry

MAX_STEPS = 6
CACHE_VERSION = 3  # bump when prompts, tools or validation change, so stale cached answers are not served


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
    except budget.BudgetExceeded as e:
        r = domain.route(text, **opts)
        r["trace"].insert(0, _step("llm", f"LLM skipped: {e}. Deterministic fallback.", llm_used=False,
                                   budget_exceeded=True))
        r["mode"] = "deterministic"
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


def _graph() -> str:
    from .. import tenants
    return tenants.graph_name()


def _cache_key(kind: str, text: str, opts: dict) -> str:
    raw = json.dumps([CACHE_VERSION, kind, _norm(text), _graph(), config.OPENAI_MODEL, opts.get("depth"),
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


def _chat(messages: list[dict], tools: list[dict] | None, max_tokens: int, *, ip: str = "",
          kind: str = "tools") -> tuple[dict, dict]:
    budget.acquire(ip)  # raises BudgetExceeded: caller falls back to the deterministic router
    body: dict[str, Any] = {"model": config.OPENAI_MODEL, "temperature": 0, "messages": messages,
                            "max_tokens": max_tokens}
    if tools:
        body.update(tools=tools, tool_choice="auto")
    r = httpx.post(f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions", timeout=config.LLM_TIMEOUT,
                   headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"}, json=body)
    r.raise_for_status()
    data = r.json()
    usage = data.get("usage") or {}
    budget.record(kind, usage.get("total_tokens", 0), ip, config.OPENAI_MODEL)
    return data["choices"][0]["message"], usage


def _run_llm(domain: Domain, text: str, **opts) -> dict:
    t0 = time.perf_counter()
    trace: list[dict] = []
    structured: dict | None = None
    messages: list[dict[str, Any]] = [{"role": "system", "content": domain.system_prompt},
                                      {"role": "user", "content": text}]
    final = ""
    total_tokens = 0
    # Ground the model first: if the deterministic router understood the question, its investigation is handed
    # to the model as an already-made `investigate` call. The model then only phrases the answer or asks for a
    # follow-up tool, which saves a round trip and stops it from picking a weaker tool.
    if "investigate" in domain.registry.tools:
        det = domain.route(text, **opts)
        if domain.confident(det) and det.get("outcome") != "unknown_entity":
            structured = det
            trace.append(_step("agent", "deterministic router pre-ran investigate; LLM phrases the answer"))
            trace.extend(det.get("trace", []))
            dig = getattr(domain, "digest", None)
            messages += [{"role": "assistant", "content": None, "tool_calls": [
                             {"id": "pre", "type": "function", "function": {
                                 "name": "investigate", "arguments": json.dumps({"question": text})}}]},
                         {"role": "tool", "tool_call_id": "pre", "content": json.dumps(
                             dig(det) if dig else _summarize_for_llm(det), default=str)[:3000]}]
    for _ in range(MAX_STEPS):
        t = time.perf_counter()
        msg, usage = _chat(messages, domain.registry.openai_tools(), config.LLM_MAX_TOKENS_TOOLS,
                           ip=opts.get("client_ip", ""))
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
                dig = getattr(domain, "digest", None)
                content = json.dumps(dig(payload) if dig else _summarize_for_llm(payload), default=str)[:3000]
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
        # Never show an answer that names something the tools did not return: fall back to the templated,
        # deterministic answer for the same question, and say so in the trace.
        det = domain.route(text, **opts)
        r = dict(det)
        r["trace"] = trace + [_step("llm", f"LLM answer rejected: names not in the tool result: "
                                           f"{', '.join(unverified)}. Showing the deterministic answer.",
                                    llm_rejected=True)] + det["trace"]
        r.update(mode="llm", llm_model=config.OPENAI_MODEL, llm_tokens=total_tokens, llm_summary=None,
                 llm_unverified=unverified, llm_rejected=True)
    r["total_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return r


def _summarize_for_llm(payload: Any) -> Any:
    """Trim bulky fields (Cypher, full path lists) before sending tool results back to the model."""
    if isinstance(payload, dict):
        return {k: _summarize_for_llm(v) for k, v in payload.items() if k not in ("trace", "cypher", "graph")}
    if isinstance(payload, list):
        return [_summarize_for_llm(x) for x in payload[:40]]
    return payload


# Capitalised words that start sentences or name products, never people.
_NOT_PEOPLE = set("""the a an you your we our us no yes it its this these those that there then wake page run use pin
upgrade services service team teams on tier if all both only npm slack github patientzero falkordb acme cypher graph
platform payments checkout data security infra web mobile api node express chalk debug and or but so also add apply
update one two three four five six seven eight nine ten september sept october monday tuesday wednesday thursday
friday saturday sunday incident playbook advisory maintainer on-call oncall shai hulud worm owner owners""".split())
# Hyphenated or dotted English words that look like package names but are not entities.
_ENTITY_OK = {"on-call", "oncall", "tier-1", "tier-2", "tier-3", "e-mail", "re-run", "re-check", "follow-up",
              "end-to-end", "semver", "package.json", "node.js", "npm", "supply-chain", "single-point", "up-to-date",
              "multi-hop", "real-time", "high-risk", "low-risk", "well-known", "pre-release", "co-maintainer",
              "tie-broken", "algo.pagerank", "algo.sppaths", "user-facing", "customer-facing", "read-only",
              "dev-only", "build-time", "run-time", "runtime-only", "non-critical", "re-deploy", "re-install"}
_TOKEN = re.compile(r"(?<![\w@])@?[a-z0-9][a-z0-9._/-]*[a-z0-9](?:@\d+(?:\.\d+)*)?", re.I)
_VERSION = re.compile(r"^[\^~v]?\d+(\.\d+)*$")
_HANDLE = re.compile(r"(?<![\w.])@([a-z][a-z0-9._-]{1,30})\b", re.I)


def unverified_names(text: str, allowed: set[str], known: set[str]) -> list[str]:
    """Every service, package or person mentioned in LLM text that is NOT in the tool result.

    Flags (1) any graph-known name that the result does not contain, (2) any package- or service-shaped token
    (contains '-', '/', '.' or '@version') that is not in the result, even if the graph has never heard of it,
    (3) person-like 'First Last' names and @handles that are not in the result."""
    from ..tools import STOPWORDS
    if not text:
        return []
    allowed_l = {a.lower() for a in allowed if a}
    allowed_l |= {a.lstrip("#@") for a in allowed_l}
    allowed_parts = {w for a in allowed_l for w in re.split(r"[\s,()]+", a) if w}
    bad: list[str] = []

    def flag(x: str):
        if x not in bad:
            bad.append(x)

    pairs = [(m.group(1), m.group(2)) for m in re.finditer(
        r"(?=\b([A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?)\s+([A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?)\b)", text)]
    for a, b in pairs:
        if a.lower() in _NOT_PEOPLE or b.lower() in _NOT_PEOPLE:
            continue
        full = f"{a} {b}"
        if full.lower() not in allowed_l and not (a.lower() in allowed_parts and b.lower() in allowed_parts):
            flag(full)
    for m in _HANDLE.finditer(text):
        h = m.group(1).rstrip(".-_").lower()
        if _VERSION.match(h) or h in allowed_parts or f"@{h}" in allowed_parts or h in allowed_l:
            continue
        if "/" in text[m.end():m.end() + 1]:
            continue  # a scoped package (@scope/name) is handled below
        flag("@" + h)
    for tok in _TOKEN.findall(text):
        t = tok.lower().rstrip(".")
        base = t.rsplit("@", 1)[0] if "@" in t[1:] else t
        if not base or re.fullmatch(r"[\d.:/-]+", base) or _VERSION.match(base) or base in STOPWORDS or len(base) < 3 or base in _ENTITY_OK:
            continue
        if base.startswith("@") and "/" not in base:
            continue  # handles were checked above
        if base in allowed_l:
            continue
        shaped = any(c in base for c in "-/") or "@" in t[1:] or (
            "." in base and not base.endswith((".js", ".json")) and re.search(r"[a-z]\.[a-z]", base))
        if base in known or shaped:
            flag(base)
    return bad


def summarize(domain: Domain, response: dict, ip: str = "") -> dict:
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
    msg, usage = _chat(msgs, None, config.LLM_MAX_TOKENS_SUMMARY, ip=ip, kind="summary")
    text = (msg.get("content") or "").strip()
    bad = unverified_names(text, domain.allowed_names(response), domain.known_names())
    out = {"summary": None if bad else text, "model": config.OPENAI_MODEL,
           "ms": round((time.perf_counter() - t) * 1000, 2), "tokens": usage.get("total_tokens", 0),
           "unverified": bad,
           "error": (f"AI summary withheld: it named {', '.join(bad)}, which the graph result does not contain."
                     if bad else None)}
    _cache_put(key, out)
    return out

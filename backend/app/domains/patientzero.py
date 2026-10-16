"""PatientZero domain adapter: npm supply-chain incidents over the fleet graph.

Everything PatientZero-specific is wired here: the tool registry (8 SPEC tools + `investigate`), the
deterministic router, and the name sets used to validate optional LLM output.
"""
from __future__ import annotations

from .. import catalog
from ..core.registry import ToolRegistry
from ..db import Trace
from ..router import ask

registry = ToolRegistry()
S = lambda **props: {"type": "object", "properties": props}  # noqa: E731
STR = {"type": "string"}
INT16 = {"type": "integer", "minimum": 1, "maximum": 6}


def _req(schema: dict, *names: str) -> dict:
    return {**schema, "required": list(names)}


registry.register("investigate", "Run the full incident investigation for a question (resolve, scope, exposure "
                  "paths with npm semver, who to wake, or memory recall). Prefer this for any incident question. Accepts a "
                  "maintainer, advisory, package@version, or a named campaign such as 'Shai-Hulud worm'.",
                  _req(S(question=STR, depth=INT16), "question"))(
    lambda question, depth=None, ctx=None: {"response": ask(
        question, depth=depth or (ctx or {}).get("depth"), fresh=bool((ctx or {}).get("fresh")),
        session=(ctx or {}).get("session", ""))})
registry.register("resolve_entity", catalog.TOOL_DOCS["resolve_entity"], _req(S(text=STR), "text"))(catalog.resolve_entity)
registry.register("incident_scope", catalog.TOOL_DOCS["incident_scope"], S(maintainer=STR, advisory_id=STR))(
    catalog.incident_scope)
registry.register("exposure", catalog.TOOL_DOCS["exposure"],
                  _req(S(package=STR, bad_version=STR, depth=INT16), "package", "bad_version"))(catalog.exposure)
registry.register("who_to_wake", catalog.TOOL_DOCS["who_to_wake"], _req(S(service=STR), "service"))(catalog.who_to_wake)
registry.register("single_points_of_failure", catalog.TOOL_DOCS["single_points_of_failure"],
                  S(limit={"type": "integer", "minimum": 1, "maximum": 20}))(catalog.single_points_of_failure)
registry.register("path_between", catalog.TOOL_DOCS["path_between"],
                  _req(S(service=STR, package=STR), "service", "package"))(catalog.path_between)
registry.register("recall", catalog.TOOL_DOCS["recall"], _req(S(entity=STR), "entity"))(catalog.recall)
# remediation_plan writes to the graph: only the UI's explicit "Generate fix" button triggers it, never the LLM.


class PatientZero:
    name = "patientzero"
    registry = registry
    system_prompt = (
        "You are PatientZero, a supply-chain incident agent for the Acme fleet (synthetic company, real npm data). "
        "Answer ONLY from tool results. Never invent services, packages, people or versions. "
        "Loaded incident: the Sept 8 2025 npm phishing of maintainer qix (chalk, debug and 17 more); for it call "
        "`investigate` with 'maintainer qix was phished'. "
        "For incident or exposure questions (including named campaigns like the Shai-Hulud worm) call `investigate` "
        "with the user's question. Use the other tools for "
        "follow-ups. Finish with at most two plain-text sentences for an on-call engineer: no markdown, no lists.")
    summary_prompt = (
        "Write ONE paragraph (3 to 5 sentences, plain text, no markdown) incident summary for an engineering "
        "channel from this graph result. State how many services are exposed and through which packages, name "
        "the most critical exposed services and their chains, mention honest negatives if any, who is on call, "
        "and the fix (overrides to clean versions). Use ONLY names and numbers present in the JSON. Do not use "
        "em dashes.")
    _known: dict[str, set[str]] = {}

    def route(self, text: str, **opts) -> dict:
        return ask(text, **opts)

    def confident(self, r: dict) -> bool:
        """The router is confident unless it found nothing at all to anchor on. An explicit id or pkg@ver that is
        not in the graph is a confident, honest unknown_entity: the LLM must not be asked to guess."""
        if r["outcome"] != "unknown_entity":
            return True
        ent = r.get("entity") or {}
        # A package-shaped name (left-pad, colors.js, @scope/x) that resolve_entity could not find is a confident,
        # honest unknown: asking the LLM would only add latency (about 1.9 s) and cost.
        return bool(ent.get("unresolved") or ent.get("unmatched"))

    def digest(self, payload) -> dict:
        """Compact tool result for the LLM (a few hundred tokens instead of the full response)."""
        if not isinstance(payload, dict) or "outcome" not in payload:
            from ..core.agent import _summarize_for_llm
            return _summarize_for_llm(payload)
        r = payload
        d = {"outcome": r.get("outcome"), "message": r.get("message"), "depth": r.get("depth")}
        if r.get("exposed"):
            d["exposed"] = [{"service": e["service"], "tier": e.get("tier"), "bad": e.get("exposed_packages"),
                             "path": next((f["path"]["display"] for f in e["findings"] if f["exposed"]), None)}
                            for e in r["exposed"][:15]]
        if r.get("safe"):
            d["safe"] = [{"service": e["service"], "why": e["findings"][0]["path"].get("reason")}
                         for e in r["safe"][:8]]
        if r.get("wake"):
            d["wake"] = [{"service": w["service"], "team": w.get("team_display"), "slack": w.get("slack"),
                          "oncall": [o.get("name") for o in w.get("oncall") or []]} for w in r["wake"][:15]]
        sb = (r.get("memory") or {}).get("seen_before")
        if sb:
            d["seen_before"] = sb.get("text")
        for k in ("path", "maintainers"):
            if r.get(k):
                d[k] = r[k]
        if r.get("spof"):
            d["spof"] = {"maintainers": r["spof"]["maintainers"][:5], "packages": r["spof"]["packages"][:5]}
        import json
        for key in ("safe", "wake", "exposed"):  # shrink long lists until the digest fits ~700 tokens
            while key in d and len(json.dumps(d, default=str)) > 2800 and len(d[key]) > 3:
                d[key] = d[key][:-1]
                d[f"{key}_truncated"] = True
        return d

    def allowed_names(self, r: dict) -> set[str]:
        names: set[str] = set()
        for group in ("exposed", "safe"):
            for e in r.get(group) or []:
                names.add(e["service"])
                for f in e["findings"]:
                    names.add(f["package"])
                    names.update(n.rsplit("@", 1)[0] for n in f["path"]["via"])
        names.update(s["package"] for s in r.get("scope") or [])
        for w in r.get("wake") or []:
            names.update(x for x in (w.get("service"), w.get("team"), w.get("team_display"), w.get("slack"),
                                     w.get("repo")) if x)
            for p in (w.get("oncall") or []) + (w.get("members") or []):
                names.update(x for x in (p.get("name"), p.get("handle")) if x)
        for sc in r.get("scope") or []:
            names.update(x for x in (sc.get("advisory_id"), sc.get("ghsa"), sc.get("cve")) if x)
        names.update(x for x in (r.get("entity_key"), r.get("trigger")) if x)
        ent = r.get("entity") or {}
        names.update(ent.get("maintainers") or [])
        names.update(ent.get("services") or [])  # resolved against the graph by resolve_entity
        if r.get("maintainers"):
            names.update(r["maintainers"].get("maintainers") or [])
            names.update(r["maintainers"].get("had_publish_rights") or [])
            names.add(r["maintainers"].get("pkg") or "")
        if r.get("path"):
            names.update(n.rsplit("@", 1)[0] for n in r["path"]["via"])
        for inc in (r.get("memory") or {}).get("incidents") or []:
            for x in inc.get("exposed") or []:
                names.add(x.get("service") or "")
                names.update(n.rsplit("@", 1)[0] for n in x.get("path") or [])
            names.update(inc.get("services") or [])
            names.add(inc.get("id") or "")
        sb = (r.get("memory") or {}).get("seen_before")
        if sb:
            names.update(x for x in (sb.get("id"), sb.get("entity_key")) if x)
        if r.get("spof"):
            names.update(m["maintainer"] for m in r["spof"]["maintainers"])
            names.update(p["pkg"] for p in r["spof"]["packages"] + r["spof"]["pagerank"])
        return names

    def known_names(self) -> set[str]:
        from .. import tenants
        g = tenants.graph_name()
        if g not in self._known:
            tr = Trace()
            nodes = tr.run("fleet_graph", "fleet_graph_nodes")
            PatientZero._known[g] = {n["id"].rsplit("@", 1)[0].lower() for n in nodes if n["id"]}
        return self._known[g]


DOMAIN = PatientZero()

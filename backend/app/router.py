"""Deterministic intent router: regex/keywords -> fixed tool sequence. No LLM, no generated Cypher."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

from . import config, tools
from .db import Trace

INTENTS = [
    ("single_points_of_failure", re.compile(
        r"single points? of failure|\bspof\b|bus factor|riskiest|most (critical|dangerous|risky)|"
        r"which maintainers?.*(most|biggest)|biggest risk", re.I)),
    ("path_between", re.compile(
        r"\bwhy\b|\bhow (is|does|do)\b.*\b(connect|depend|reach|pull|use|get)|path (from|between|to)|connected to|"
        r"depends? on", re.I)),
    ("recall", re.compile(r"\b(history|recall|remember|past incidents?|previous incidents?|what happened)\b", re.I)),
    ("who_to_wake", re.compile(
        r"who (owns|maintains|do i (wake|page)|should i (wake|page)|to (wake|page)|is on.?call)|\bon.?call\b|"
        r"\bwake\b|\bpage\b|\bowner", re.I)),
]


def local_date(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%b %d %Y, %I:%M %p %Z")
    except (TypeError, ValueError):
        return str(iso)


def classify(text: str) -> str:
    for name, rx in INTENTS:
        if rx.search(text):
            return name
    return "exposure"


def trigger_of(ent: dict) -> tuple[str, str, str] | None:
    """Pick the incident trigger (priority: advisory > pkg@ver > maintainer > bare packages).

    Returns (label, entity_kind, entity_key); entity_key matches what queries/recall.cypher expects.
    """
    if ent["advisories"]:
        a = ent["advisories"][0]
        return f"advisory {a['query']}", "advisory", a["id"]
    if ent["package_versions"]:
        pv = ent["package_versions"][0]
        return pv["key"], "version", pv["key"]
    if ent["maintainers"]:
        m = ent["maintainers"][0]
        return f"maintainer {m}", "maintainer", m
    if ent["packages"]:
        ps = sorted(ent["packages"])
        return "packages " + ", ".join(ps), ("package" if len(ps) == 1 else "packages"), ",".join(ps)
    return None


def scope_for(tr: Trace, ent: dict) -> list[dict]:
    if ent["advisories"]:
        return tools.incident_scope(tr, advisory_id=ent["advisories"][0]["id"])
    if ent["package_versions"]:
        pv = ent["package_versions"][0]
        return tools.incident_scope(tr, package=pv["package"], bad_version=pv["version"])
    if ent["maintainers"]:
        return tools.incident_scope(tr, maintainer=ent["maintainers"][0])
    if ent["packages"]:
        return tools.incident_scope(tr, packages=ent["packages"])
    return []


def base(text: str, intent: str, depth: int) -> dict:
    return dict(query=text, intent=intent, depth=depth, outcome=None, message="", entity=None, trace=[],
                scope=[], exposed=[], safe=[], clean=[], fleet_size=None, wake=[], fix=None, memory=None,
                spof=None, path=None, maintainers=None, graph={"nodes": [], "edges": []},
                trigger=None, entity_kind=None, entity_key=None, exposed_count=0, llm_summary=None)


def ask(text: str, depth: int | None = None, fresh: bool = False, session: str = "",
        generate_fix: bool = False, client_ip: str = "") -> dict:
    t0 = time.perf_counter()
    depth = max(1, min(config.MAX_DEPTH, int(depth or config.DEFAULT_DEPTH)))
    text = (text or "").strip()
    intent = classify(text)
    out = base(text, intent, depth)
    tr = Trace()
    try:
        ent = tools.resolve_entity(text, tr)
        out["entity"] = ent
        if intent == "single_points_of_failure":
            _spof(out, tr)
        elif intent == "recall" and not ent["unresolved"]:
            _recall(out, tr, ent)
        elif not ent["found"]:
            _unknown(out, ent)
        elif intent == "path_between" and ent["services"] and (ent["packages"] or ent["package_versions"]):
            _path(out, tr, ent)
        elif intent == "recall":
            _recall(out, tr, ent)
        elif intent == "who_to_wake" and (ent["services"] or (
                re.search(r"\b(owns|maintains|maintainers?|owner)\b", text, re.I) and ent["packages"]
                and not ent["services"]) or not trigger_of(ent)):
            _wake(out, tr, ent)
        elif ent["unresolved"] and not (ent["advisories"] or ent["package_versions"] or ent["maintainers"]):
            # explicit id / pkg@ver not in graph: be honest, don't fall back to fuzzy token matches
            _unknown(out, ent)
        elif trigger_of(ent):
            _incident(out, tr, ent, depth, fresh, session, generate_fix)
        elif ent["services"]:
            _wake(out, tr, ent)
        else:
            _unknown(out, ent)
    finally:
        out["trace"] = tr.steps
        out["total_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return out


# ----------------------------------------------------------------------------- handlers
def _unknown(out: dict, ent: dict):
    out["intent"] = "resolve"
    out["outcome"] = "unknown_entity"
    what = (", ".join(ent["unresolved"]) + " is not in the graph") if ent["unresolved"] else \
        "Nothing in your question matches a node in the graph"
    out["message"] = (f"{what}, so no traversal was run. PatientZero only answers from "
                      f"FalkorDB: try a maintainer (qix), an advisory (GHSA-4x49-vf9v-38px), a package@version "
                      f"(debug@4.4.2) or a fleet service name.")


def _incident(out, tr, ent, depth, fresh, session, generate_fix):
    trigger, kind, key = trigger_of(ent)
    out.update(trigger=trigger, entity_kind=kind, entity_key=key)
    if not fresh and not generate_fix and kind != "packages":
        mem = tools.recall(tr, key)
        if mem:
            m = mem[0]
            out["outcome"] = "answered_from_memory"
            out["memory"] = {"incidents": [m]}
            out["exposed_count"] = m["exposed_count"] or 0
            out["graph"] = graph_elements_memory(m["exposed"])
            out["message"] = (f"Answered from graph memory: incident {m['id']} on {local_date(m['at'])} "
                              f"({m['trigger']}) found {m['exposed_count']} exposed services at depth "
                              f"{m['depth']}. The patch is on file. Run a fresh traversal to re-check.")
            return
    scope = scope_for(tr, ent)
    out["scope"] = scope
    if not scope:
        out["outcome"] = "not_in_graph"
        out["message"] = (f"{trigger} is in the graph, but no compromised version is recorded for it, so there "
                          f"is nothing to trace. Ask with an explicit version, for example package@x.y.z.")
        return
    fleet = tools.fleet_overview(tr)
    res = tools.exposure(tr, scope, depth, fleet)
    out.update(exposed=res["exposed"], safe=res["safe"], clean=res["clean"], fleet_size=res["fleet_size"])
    out["exposed_count"] = len(res["exposed"])
    out["graph"] = graph_elements(res["exposed"], res["safe"])
    bad = ", ".join(sorted({f"{s['package']}@{s['bad_version']}" for s in scope}))
    if res["exposed"]:
        out["wake"] = tools.who_to_wake(tr, [e["service"] for e in res["exposed"]])
        out["outcome"] = "answered_with_path"
        out["message"] = (f"{len(res['exposed'])} of {res['fleet_size']} services can install a malicious version "
                          f"({trigger}) at depth {depth}. {len(res['safe'])} more reach an affected package, "
                          f"but their ranges exclude the bad version.")
    else:
        out["outcome"] = "not_in_graph"
        out["message"] = (f"No fleet path admits this version at depth {depth} ({bad}). "
                          + (f"{len(res['safe'])} services reach the package, but every range excludes it."
                             if res["safe"] else "No fleet service reaches the affected package at this depth."))
    if generate_fix and res["exposed"]:
        out["fix"] = tools.remediation_plan(tr, trigger=out["query"] or trigger, entity_kind=kind, entity_key=key,
                                            depth=depth, result=res, wake=out["wake"], session=session)
        out["message"] += f" Incident {out['fix']['incident_id']} written to the graph."


def _wake(out, tr, ent):
    out["intent"] = "who_to_wake"
    if ent["services"]:
        out["wake"] = tools.who_to_wake(tr, ent["services"])
        out["outcome"] = "answered_with_path"
        out["message"] = "; ".join(
            f"{w['service']}: {w['team_display']} ({w['slack']}), on-call "
            + (", ".join(f"{o['name']} {o['handle']}" for o in w["oncall"]) or "none") for w in out["wake"])
        return
    if ent["packages"] or ent["package_versions"]:
        pkg = (ent["packages"] or [ent["package_versions"][0]["package"]])[0]
        pm = tools.package_maintainers(tr, pkg)
        out["maintainers"] = pm
        out["outcome"] = "answered_with_path"
        hp = (f" Publish rights on {pm['as_of'][0]} (curated incident data): {', '.join(pm['had_publish_rights'])}."
              if pm["had_publish_rights"] else "")
        out["message"] = (f"{pkg} is maintained on npm today by {', '.join(pm['maintainers']) or 'nobody listed'}."
                          + hp + " (Ask 'who do I wake for <service>' for fleet owners.)")
        return
    # Nothing specific: wake owners of the latest remembered incident.
    incs = tools.list_incidents(tr, 1)
    if incs:
        inc = incs[0]
        out["memory"] = {"incidents": [inc]}
        out["wake"] = tools.who_to_wake(tr, inc["services"])
        out["outcome"] = "answered_from_memory"
        out["message"] = f"From incident {inc['id']} ({local_date(inc['at'])}): wake the owners of {len(inc['services'])} services."
    else:
        _unknown(out, ent)


def _path(out, tr, ent):
    svc = ent["services"][0]
    pkg = ent["packages"][0] if ent["packages"] else ent["package_versions"][0]["package"]
    p = tools.path_between(tr, svc, pkg)
    if p:
        out["outcome"] = "answered_with_path"
        out["path"] = dict(service=svc, package=pkg, **p)
        hops = " → ".join(f"{n}" for n in p["via"])
        out["message"] = f"{svc} reaches {pkg} in {p['hops']} hops: {hops}"
        out["graph"] = graph_elements_path(p["via"], p["requirements"])
    else:
        out["outcome"] = "not_in_graph"
        out["message"] = f"{svc} and {pkg} are both in the graph, but no dependency path connects them."


def _recall(out, tr, ent):
    t = trigger_of(ent)
    key = (ent["services"] and None) or (t[2] if t and t[1] != "packages" else None)
    rows = tools.recall(tr, key) if key else []
    if not key:
        rows = tools.list_incidents(tr, 10)
    out["memory"] = {"incidents": rows}
    if rows:
        out["outcome"] = "answered_from_memory"
        out["message"] = f"{len(rows)} stored incident(s)" + (f" about {key}." if key else " in graph memory.")
    else:
        out["outcome"] = "not_in_graph"
        out["message"] = f"No incidents stored in the graph{' for ' + key if key else ''} yet."


def _spof(out, tr):
    out["intent"] = "single_points_of_failure"
    r = tools.single_points_of_failure(tr, limit=10)
    out["spof"] = r
    out["outcome"] = "answered_with_path" if (r["maintainers"] or r["packages"]) else "not_in_graph"
    top = ", ".join(f"{m['maintainer']} ({m['reach']} services)" for m in r["maintainers"][:3])
    out["message"] = (f"If one of these npm accounts were phished, it would reach the most Acme services: {top}. "
                      f"Ranked by fleet reach, tie-broken by algo.pageRank.")


# ----------------------------------------------------------------------------- graph view
def _node_type(name: str, svc: bool, bad: bool) -> str:
    return "service" if svc else ("affected" if bad else "package")


def graph_elements(exposed: list[dict], safe: list[dict], max_services: int = 40) -> dict:
    nodes, edges = {}, {}
    for status, group in (("exposed", exposed), ("safe", safe)):
        for e in group[:max_services]:
            for f in e["findings"]:
                p = f["path"]
                via, reqs = p["via"], p["requirements"]
                for i, n in enumerate(via):
                    last = i == len(via) - 1
                    t = "service" if i == 0 else ("affected" if last else "package")
                    prev = nodes.get(n, {})
                    st = "exposed" if (f["exposed"] and (i == 0 or last)) else prev.get("status", status if i == 0 else "")
                    nodes[n] = dict(id=n, label=n, type=prev.get("type", t), status=st if not prev.get("status") == "exposed" else "exposed",
                                    bad=f"{f['package']}@{f['bad_version']}" if last else prev.get("bad"))
                for i in range(len(via) - 1):
                    k = (via[i], via[i + 1])
                    ex = bool(f["exposed"])
                    last = i == len(via) - 2
                    old = edges.get(k)
                    edges[k] = dict(id=f"{via[i]}->{via[i + 1]}", source=via[i], target=via[i + 1],
                                    requirement=reqs[i], last=last or (old or {}).get("last", False),
                                    status="exposed" if ex or (old and old["status"] == "exposed") else "safe")
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def graph_elements_path(via: list[str], reqs: list[str]) -> dict:
    nodes = [dict(id=n, label=n, type="service" if i == 0 else ("affected" if i == len(via) - 1 else "package"),
                  status="path") for i, n in enumerate(via)]
    edges = [dict(id=f"{via[i]}->{via[i + 1]}", source=via[i], target=via[i + 1], requirement=reqs[i],
                  status="path", last=i == len(via) - 2) for i in range(len(via) - 1)]
    return {"nodes": nodes, "edges": edges}


def dumps(o) -> str:
    return json.dumps(o, indent=2, default=str)


def graph_elements_memory(exposed: list[dict]) -> dict:
    nodes, edges = {}, {}
    for x in exposed:
        via = x.get("path") or []
        for i, n in enumerate(via):
            nodes[n] = dict(id=n, label=n, type="service" if i == 0 else ("affected" if i == len(via) - 1 else "package"),
                            status="exposed")
        for i in range(len(via) - 1):
            edges[(via[i], via[i + 1])] = dict(id=f"{via[i]}->{via[i + 1]}", source=via[i], target=via[i + 1],
                                               requirement=x.get("requirement") if i == len(via) - 2 else None,
                                               status="exposed", last=i == len(via) - 2)
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}

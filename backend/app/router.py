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


# Named campaigns (Advisory.campaign). Shai-Hulud is loaded only with INCIDENTS=qix,shai-hulud.
CAMPAIGNS = [
    (re.compile(r"shai[\s_-]?hulud|self[\s-]?replicating worm|\bnpm worm\b", re.I), "2025-09-15-shai-hulud", "Shai-Hulud"),
]


def campaign_of(text: str) -> tuple[str, str] | None:
    for rx, cid, label in CAMPAIGNS:
        if rx.search(text):
            return cid, label
    return None


# "X was phished / compromised / hacked": an incident report, so the graph is always traversed fresh.
COMPROMISE_RE = re.compile(
    r"phish|compromis|hack|pwn|hijack|take\s?over|breach|malicious|malware|poison|backdoor|attack|got owned", re.I)
# Talking about a person behind a package ("the guy who maintains chalk got phished").
PERSON_RE = re.compile(r"\b(maintainer|maintains|maintained|author|guy|dev|developer|publisher|account|owner|"
                       r"person|someone|whoever|he|she|they)\b", re.I)
# Plain-English names for the incidents loaded in the graph -> maintainer key. Router first, LLM never needed.
INCIDENT_SYNONYMS = [
    (re.compile(r"(npm|chalk|debug)\s+(phishing|phish|supply.chain)|phishing (attack|campaign|email)|"
                r"sep(t|tember)?\.?\s*8(th)?,?\s*(2025)?|september 2025|"
                r"chalk\W+(and|&|/)\W*debug\s+(attack|compromise|hack|incident|takeover)|"
                r"npmjs\.help|josh junon", re.I), "qix"),
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
    if ent.get("campaign"):
        cid, label = ent["campaign"]
        return f"campaign {label}", "campaign", cid
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
    if ent.get("campaign"):
        return tools.incident_scope(tr, campaign=ent["campaign"][0])
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


def must_traverse(text: str, ent: dict) -> str | None:
    """Why an incident question must be answered by a fresh traversal (never replaced by memory), or None.

    Memory may answer on its own only for a bare entity recall ("qix") at the depth it was stored with."""
    if ent.get("advisories"):
        return "advisory id"
    if ent.get("package_versions"):
        return "package@version"
    if ent.get("campaign"):
        return "named campaign"
    if COMPROMISE_RE.search(text or ""):
        return "compromise report"
    return None


def infer_maintainer(tr: Trace, text: str, ent: dict) -> None:
    """Router synonyms: map natural phrasing of a loaded incident to its maintainer, before any LLM."""
    if ent["maintainers"] or ent["advisories"] or ent["package_versions"] or ent["services"]:
        return
    for rx, npm in INCIDENT_SYNONYMS:
        if rx.search(text):
            rows = [r for r in tools._resolve(tr, npm) if r["kind"] == "Maintainer"]
            if rows:
                ent["maintainers"] = [rows[0]["key"]]
                ent["found"] = True
                ent["inferred"] = f"'{rx.search(text).group(0)}' -> maintainer {rows[0]['key']}"
                tr.compact("resolve_entity")
                return
    if ent["packages"] and COMPROMISE_RE.search(text) and PERSON_RE.search(text):
        for pkg in ent["packages"]:
            pm = tools.package_maintainers(tr, pkg) or {}
            hp = pm.get("had_publish_rights") or []
            if hp:
                ent["maintainers"] = [hp[0]]
                ent["inferred"] = f"the maintainer of {pkg} with publish rights at the time -> {hp[0]}"
                return


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
        infer_maintainer(tr, text, ent)
        if ent.get("inferred"):
            tr.add("resolve_entity", f"router synonym: {ent['inferred']}")
            if intent in ("who_to_wake", "path_between"):
                intent = out["intent"] = "exposure"
        elif intent == "who_to_wake" and COMPROMISE_RE.search(text) and not ent["services"] and (
                ent["maintainers"] or ent["advisories"] or ent["package_versions"]):
            intent = out["intent"] = "exposure"  # "who do I wake, debug@4.4.2 was hacked": an incident
        camp = campaign_of(text)
        if camp and intent in ("exposure", "recall", "who_to_wake") and not ent["services"]:
            ent["campaign"] = list(camp)
            ent["found"] = True
            if intent == "recall":
                _recall(out, tr, ent)
            else:
                _incident(out, tr, ent, depth, fresh, session, generate_fix)
        elif intent == "single_points_of_failure":
            _spof(out, tr)
        elif intent == "recall" and not ent["unresolved"]:
            _recall(out, tr, ent)
        elif not ent["found"]:
            _unknown(out, ent)
        elif intent == "path_between" and ent["services"] and (ent["packages"] or ent["package_versions"]):
            _path(out, tr, ent)
        elif ent["services"] and ent.get("unmatched") and not trigger_of(ent) and (
                intent == "path_between" or COMPROMISE_RE.search(text) or re.search(r"\bexposed\b", text, re.I)):
            ent["unresolved"] = ent["unresolved"] + ent["unmatched"]
            _unknown(out, ent)
            out["message"] = (f"{', '.join(ent['services'])} is in the graph, but {', '.join(ent['unmatched'])} "
                              f"is not, so nothing was traced. " + out["message"].split(", so no traversal was run. ")[-1])
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
    missing = list(dict.fromkeys(ent["unresolved"] + (ent.get("unmatched") or [])))
    what = (", ".join(missing) + (" is" if len(missing) == 1 else " are") + " not in the graph") if missing else \
        "Nothing in your question matches a node in the graph"
    out["message"] = (f"{what}, so no traversal was run. PatientZero only answers from "
                      f"FalkorDB: try a maintainer (qix), an advisory (GHSA-4x49-vf9v-38px), a package@version "
                      f"(debug@4.4.2) or a fleet service name.")


def _incident(out, tr, ent, depth, fresh, session, generate_fix):
    trigger, kind, key = trigger_of(ent)
    out.update(trigger=trigger, entity_kind=kind, entity_key=key)
    mem = tools.recall(tr, key) if kind != "packages" else []
    why = must_traverse(out["query"], ent)
    # Memory answers on its own ONLY for a bare recall of the exact stored trigger at the stored depth.
    exact = [m for m in mem if m.get("entity_key") == key and int(m.get("depth") or 0) == depth]
    if not fresh and not generate_fix and not why and exact:
        m = exact[0]
        out["outcome"] = "answered_from_memory"
        out["memory"] = {"incidents": [m], "seen_before": seen_before(m, key, None)}
        out["exposed_count"] = m["exposed_count"] or 0
        out["graph"] = graph_elements_memory(m["exposed"])
        out["message"] = (f"Answered from graph memory: incident {m['id']} on {local_date(m['at'])} "
                          f"({m['trigger']}) found {m['exposed_count']} exposed services at depth "
                          f"{m['depth']}. The patch is on file. Run a fresh traversal to re-check.")
        return
    if mem:
        tr.add("memory lookup", "memory shown alongside a fresh traversal ("
               + (f"{why} in the question" if why else "fresh requested" if fresh or generate_fix
                  else "different trigger or depth") + ")")
    scope = scope_for(tr, ent)
    out["scope"] = scope
    if not scope and kind == "campaign":
        out["outcome"] = "not_in_graph"
        out["message"] = (f"The {ent['campaign'][1]} campaign is not loaded in this graph (no advisories tagged "
                          f"{key}). Reload with INCIDENTS=qix,shai-hulud to trace it.")
        return
    if not scope:  # a watched advisory where every published version is malicious (no single bad version)
        from .watch.answers import all_versions_scope
        scope, av = all_versions_scope(tr, ent, kind, key, depth)
        if av:
            out["all_versions"] = av
            out["scope"] = scope
            if not scope:
                out["outcome"] = "not_in_graph"
                out["message"] = av["message"]
                return
    if not scope:
        out["outcome"] = "not_in_graph"
        out["message"] = (f"{trigger} is in the graph, but no compromised version is recorded for it, so there "
                          f"is nothing to trace. Ask with an explicit version, for example package@x.y.z.")
        return
    hypothetical = all(sc.get("source") == "user report" for sc in scope)
    out["hypothetical"] = hypothetical
    fleet = tools.fleet_overview(tr)
    res = tools.exposure(tr, scope, depth, fleet)
    out.update(exposed=res["exposed"], safe=res["safe"], clean=res["clean"], fleet_size=res["fleet_size"])
    out["exposed_count"] = len(res["exposed"])
    out["graph"] = graph_elements(res["exposed"], res["safe"])
    bad_all = sorted({f"{s['package']}@{s['bad_version']}" for s in scope})
    bad = ", ".join(bad_all) if len(bad_all) <= 6 else (
        f"{len(bad_all)} bad versions across {len({s['package'] for s in scope})} packages, e.g. "
        + ", ".join(bad_all[:3]))
    if res["exposed"]:
        out["wake"] = tools.who_to_wake(tr, [e["service"] for e in res["exposed"]])
        out["outcome"] = "answered_with_path"
        n_safe = len(res["safe"])
        tail = (f" {plural(n_safe, 'more service')} reach{'es' if n_safe == 1 else ''} an affected package, "
                f"but {'its range excludes' if n_safe == 1 else 'their ranges exclude'} the bad version."
                if n_safe else "")
        if hypothetical:
            out["message"] = (f"No advisory on file for {bad}. If it were compromised, "
                              f"{len(res['exposed'])} of {res['fleet_size']} services could install it at depth "
                              f"{depth}.{tail}")
        else:
            out["message"] = (f"{len(res['exposed'])} of {res['fleet_size']} services can install a malicious "
                              f"version ({trigger}) at depth {depth}.{tail}")
    else:
        out["outcome"] = "not_in_graph"
        if kind == "campaign":
            npk = len({sc["package"] for sc in scope})
            out["message"] = (f"Not hit: none of the {res['fleet_size']} services can install any of the {len(scope)} "
                              f"malicious versions of the {npk} {ent['campaign'][1]} packages at depth {depth}. "
                              + (f"{plural(len(res['safe']), 'service')} reach a listed package, but every range "
                                 f"excludes the bad versions." if res["safe"] else
                                 "No fleet service depends on any of those packages."))
            return
        out["message"] = ((f"No advisory on file for {bad}. " if hypothetical else "")
                          + f"No fleet path admits this version at depth {depth} ({bad}). "
                          + (f"{plural(len(res['safe']), 'service')} reach the package, but every range excludes it."
                             if res["safe"] else "No fleet service reaches the affected package at this depth."))
    if out.get("all_versions"):
        out["message"] = out["all_versions"]["lead"] + " " + out["message"]
    if mem:
        m = mem[0]
        sb = seen_before(m, key, [e["service"] for e in res["exposed"]])
        out["memory"] = {"incidents": mem[:5], "seen_before": sb}
        out["message"] += " " + sb["text"]
    if generate_fix and res["exposed"]:
        out["fix"] = tools.remediation_plan(tr, trigger=out["query"] or trigger, entity_kind=kind, entity_key=key,
                                            depth=depth, result=res, wake=out["wake"], session=session)
        out["message"] += f" Incident {out['fix']['incident_id']} written to the graph."


def seen_before(m: dict, key: str, fresh_services: list[str] | None) -> dict:
    """A compact 'Seen before' record for a remembered Incident, with a diff against the fresh result."""
    before = sorted({x["service"] for x in m.get("exposed") or [] if x.get("service")})
    same = m.get("entity_key") == key
    d = dict(id=m["id"], at=m["at"], at_local=local_date(m["at"]), trigger=m.get("trigger"),
             entity_key=m.get("entity_key"), depth=m.get("depth"), exposed_count=m.get("exposed_count") or 0,
             same_trigger=same, has_patch=bool(m.get("patch")), playbooks=[p["id"] for p in m.get("playbooks") or []
                                                                         if p and p.get("id")])
    rel = "" if same else f" (related: {m.get('trigger')})"
    d["text"] = (f"Seen before: {m['id']} on {d['at_local']}{rel}, {plural(d['exposed_count'], 'service')} "
                 f"exposed at depth {m.get('depth')}" + (", patch on file." if d["has_patch"] else "."))
    if fresh_services is not None and same:
        now = set(fresh_services)
        d["new_since"] = sorted(now - set(before))
        d["no_longer_exposed"] = sorted(set(before) - now)
    return d


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


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
    from . import tenants
    out["message"] = (f"If one of these npm accounts were phished, it would reach the most {tenants.company()} "
                      f"services: {top}. "
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

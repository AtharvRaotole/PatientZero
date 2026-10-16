"""The incident crew. Four deterministic agents run one incident and hand off through FalkorDB.

  Triage ──> Investigator ──┐
     └─────> Responder ─────┴──> Fixer

* Triage resolves the trigger, computes the blast radius, and upserts the Incident (memory that updates over
  time: last_seen, triage_count and a 'what changed since last time' delta).
* Investigator and Responder run concurrently (asyncio tasks on worker threads) once Triage hands off.
* Fixer starts only when both have handed off to it.

Shared state, not shared Python memory: each agent writes (:AgentRun)-[:PRODUCED]->(:Finding) nodes, and the next
agent READS them back with queries/crew.cypher `crew_read_upstream`, which walks the HANDED_OFF_TO edges. Agents
receive nothing from each other except an AgentRun id. Zero LLM calls.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
import time
import uuid
from collections import Counter
from typing import Any, Callable

from .. import playbooks, tenants, tools
from .. import router as qrouter
from ..db import Trace

AGENTS = ("triage", "investigator", "responder", "fixer")
TITLES = {"triage": "Triage", "investigator": "Investigator", "responder": "Responder", "fixer": "Fixer"}
ROLES = {"triage": "Resolve the trigger and size the blast radius",
         "investigator": "Explain the paths, the honest negatives and the single points of failure",
         "responder": "Decide who to wake and draft the pages",
         "fixer": "Build the patch plan and reuse approved playbooks"}
DAG = [("triage", "investigator"), ("triage", "responder"), ("investigator", "fixer"), ("responder", "fixer")]
RUN_RE = re.compile(r"^CREW-[0-9]{8}-[0-9a-f]{8}$")

Emit = Callable[[str, dict], None]


class NoIncident(Exception):
    """Triage could not anchor the question on an incident (unknown entity or nothing recorded)."""


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _j(o: Any) -> str:
    return json.dumps(o, default=str, separators=(",", ":"))


class Crew:
    def __init__(self, query: str, depth: int, tenant: str, session_id: str | None = None,
                 emit: Emit | None = None):
        self.query = query.strip()
        self.depth = tools.clamp_depth(depth)
        self.tenant = tenant
        self.session_id = session_id
        self.emit_cb = emit
        stamp = tools.now_iso()
        self.run_id = f"CREW-{stamp[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        self.agent_ids = {a: f"{self.run_id}-{a}" for a in AGENTS}
        self.incident_id: str | None = None
        self.trace: list[dict] = []
        self.events: list[dict] = []
        self.t0 = time.perf_counter()
        self._seq = itertools.count(1)  # global order of findings and handoffs within the run (thread-safe in CPython)

    # ------------------------------------------------------------------ plumbing
    def emit(self, event: str, data: dict) -> None:
        data = {"run_id": self.run_id, **data}
        self.events.append({"event": event, "data": data})
        if self.emit_cb:
            self.emit_cb(event, data)

    def _tr(self) -> Trace:
        return Trace()

    def _keep(self, tr: Trace, agent: str) -> None:
        for st in tr.steps:
            st["agent"] = agent
        self.trace.extend(tr.steps)

    def finding(self, tr: Trace, agent: str, kind: str, title: str, text: str, data: dict) -> dict:
        fid = f"{self.agent_ids[agent]}-{kind}"
        tr.run(f"crew:{agent}", "crew_add_finding", dict(
            agent_run=self.agent_ids[agent], incident=self.incident_id or "", id=fid, kind=kind, agent=agent,
            title=title, text=text, data=_j(data), at=tools.now_iso(), run_id=self.run_id, seq=next(self._seq)),
               write=True)
        f = {"id": fid, "agent": agent, "kind": kind, "title": title, "text": text, "data": data}
        self.emit("finding", {"agent": agent, "finding": f})
        return f

    def read_upstream(self, tr: Trace, agent: str) -> dict[str, dict]:
        """Findings of the upstream agents, read from the graph along HANDED_OFF_TO. Keyed by finding kind."""
        rows = tr.run(f"crew:{agent}", "crew_read_upstream", {"id": self.agent_ids[agent]},
                      note="inputs read from the graph via HANDED_OFF_TO, not passed in Python")
        out: dict[str, dict] = {}
        for r in rows:
            try:
                data = json.loads(r["data"]) if r.get("data") else {}
            except (TypeError, ValueError):
                data = {}
            out[r["kind"]] = {"agent": r["agent"], "id": r["id"], "title": r["title"], "data": data}
        self.emit("read", {"agent": agent, "query": "crew_read_upstream",
                           "findings": [{"id": v["id"], "agent": v["agent"], "kind": k} for k, v in out.items()]})
        return out

    def handoff(self, tr: Trace, src: str, dst: str, reason: str) -> None:
        tr.run(f"crew:{src}", "crew_handoff", {"from": self.agent_ids[src], "to": self.agent_ids[dst],
                                               "reason": reason, "at": tools.now_iso(), "seq": next(self._seq)},
               write=True)
        self.emit("handoff", {"from": src, "to": dst, "reason": reason})

    def _run_agent(self, agent: str, fn: Callable[[Trace], str]) -> str:
        """Run one agent on the current thread: status in the graph, timing, events. Returns its summary."""
        tenants.set_current(self.tenant)
        tr = self._tr()
        t0 = time.perf_counter()
        tr.run(f"crew:{agent}", "crew_agent_start", {"id": self.agent_ids[agent], "started": tools.now_iso()},
               write=True)
        self.emit("agent_start", {"agent": agent, "agent_run_id": self.agent_ids[agent], "title": TITLES[agent],
                                  "role": ROLES[agent]})
        status, error, summary = "done", None, ""
        try:
            summary = fn(tr)
        except NoIncident as e:
            status, summary = "no_incident", str(e)
        except Exception as e:  # noqa: BLE001
            status, error, summary = "error", f"{type(e).__name__}: {e}", f"failed: {type(e).__name__}"
            raise
        finally:
            ms = _ms(t0)
            try:
                tr.run(f"crew:{agent}", "crew_agent_finish", dict(id=self.agent_ids[agent], status=status, ms=ms,
                                                                   finished=tools.now_iso(), error=error,
                                                                   summary=summary[:400]), write=True)
            finally:
                self._keep(tr, agent)
                self.emit("agent_done", {"agent": agent, "status": status, "ms": ms, "summary": summary,
                                         "error": error})
        return status

    # ------------------------------------------------------------------ agents
    def triage(self, tr: Trace) -> str:
        q = self.query
        ent = tools.resolve_entity(q, tr)
        qrouter.infer_maintainer(tr, q, ent)
        camp = qrouter.campaign_of(q)
        if camp and not ent["services"]:
            ent["campaign"] = list(camp)
        t = qrouter.trigger_of(ent)
        if not t:
            missing = ent.get("unresolved") or ent.get("unmatched") or []
            msg = (f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not in the graph" if missing
                   else "Nothing in the question matches an incident trigger in the graph")
            self.finding(tr, "triage", "unresolved", "No incident to run", msg + ". Nothing was traced.",
                         {"query": q, "unresolved": missing})
            raise NoIncident(msg)
        label, kind, key = t
        scope = qrouter.scope_for(tr, ent)
        if not scope:
            msg = f"{label} is in the graph, but no compromised version is recorded for it"
            self.finding(tr, "triage", "unresolved", "No compromised versions", msg + ".",
                         {"query": q, "trigger": label})
            raise NoIncident(msg)
        fleet = tools.fleet_overview(tr)
        res = tools.exposure(tr, scope, self.depth, fleet)
        exposed = sorted(res["exposed"], key=lambda e: (e.get("tier") or 9, e["service"]))
        now_services = [e["service"] for e in exposed]

        # Memory that updates over time: adopt or create the Incident for (trigger, depth) and diff against it.
        crew_key = f"{key}|d{self.depth}"
        tr.run("crew:triage", "crew_adopt_incident", {"crew_key": crew_key, "key": key, "depth": self.depth},
               write=True)
        at = tools.now_iso()
        slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:40]
        up = tr.run("crew:triage", "crew_upsert_incident", dict(
            crew_key=crew_key, id=f"INC-{at[:10]}-{slug}-{uuid.uuid4().hex[:6]}", at=at, trigger=q, kind=kind,
            key=key, depth=self.depth, services=now_services, run_id=self.run_id), write=True)[0]
        self.incident_id = up["id"]
        before = set(up.get("before") or [])
        is_new = bool(up.get("created"))
        delta = {"first_triage": is_new, "previous_seen": up.get("prev_seen"), "triage_count": up["triage_count"],
                 "newly_exposed": sorted(set(now_services) - before) if not is_new else [],
                 "no_longer_exposed": sorted(before - set(now_services)) if not is_new else [],
                 "unchanged": sorted(before & set(now_services)) if not is_new else []}
        rows = []
        for e in exposed:
            f = next(f for f in e["findings"] if f["exposed"])
            rows.append(dict(service=e["service"], path=f["path"]["via"], requirement=f["path"]["requirement"],
                             pkg=f["package"], advisory=f.get("advisory_id") or "", hops=f["path"]["hops"]))
        tr.run("crew:triage", "crew_set_exposed", {"id": self.incident_id, "rows": rows, "delta": _j(delta)},
               write=True)
        for k in tools.entity_keys_for_link(kind, key) if kind != "campaign" else []:
            tr.run("crew:triage", "crew_link_entity", {"id": self.incident_id, "entity_key": k}, write=True)
        self.emit("incident", {"incident_id": self.incident_id, "created": is_new,
                               "triage_count": up["triage_count"]})

        packages = sorted({s["package"] for s in scope})
        self.finding(tr, "triage", "entity", f"Trigger: {label}",
                     f"{label} resolves to {len(scope)} malicious version{'s' if len(scope) != 1 else ''} across "
                     f"{len(packages)} package{'s' if len(packages) != 1 else ''}.",
                     {"trigger": label, "entity_kind": kind, "entity_key": key, "query": q, "depth": self.depth,
                      "scope_count": len(scope), "packages": packages[:60],
                      "advisories": sorted({s["advisory_id"] for s in scope if s.get("advisory_id")})[:20],
                      "scope": [{k2: s.get(k2) for k2 in ("package", "bad_version", "clean_version", "advisory_id",
                                                          "ghsa")} for s in scope[:40]]})
        slim = [dict(service=e["service"], tier=e.get("tier"), team=e.get("team"),
                     exposed_packages=e["exposed_packages"], min_hops=e.get("min_hops"),
                     findings=[_slim_finding(f) for f in e["findings"] if f["exposed"]]) for e in exposed]
        safe = [dict(service=e["service"], tier=e.get("tier"), team=e.get("team"),
                     reason=e["findings"][0]["path"].get("reason"), path=e["findings"][0]["path"].get("display"),
                     package=e["findings"][0]["package"], requirement=e["findings"][0]["path"].get("requirement"))
                for e in sorted(res["safe"], key=lambda e: (e.get("tier") or 9, e["service"]))]
        n = len(exposed)
        text = (f"{n} of {res['fleet_size']} {tenants.company()} services can install a malicious version at depth "
                f"{self.depth}" + (f"; {len(safe)} more reach an affected package but every range excludes the bad "
                                   f"version." if safe else ".")) if n else (
            f"Not hit: none of the {res['fleet_size']} {tenants.company()} services can install a malicious version "
            f"at depth {self.depth}" + (f"; {len(safe)} reach an affected package but every range excludes it."
                                        if safe else "."))
        self.finding(tr, "triage", "blast_radius", f"Blast radius: {n} of {res['fleet_size']}", text,
                     {"exposed": slim, "safe": safe, "clean_count": len(res["clean"]), "fleet_size": res["fleet_size"],
                      "depth": self.depth, "incident_id": self.incident_id, "company": tenants.company()})
        if is_new:
            dtext = f"First triage of {label} at depth {self.depth}: incident {self.incident_id} created."
        elif delta["newly_exposed"] or delta["no_longer_exposed"]:
            dtext = (f"Re-triage #{up['triage_count']} of {self.incident_id}: "
                     + (f"{len(delta['newly_exposed'])} newly exposed ({', '.join(delta['newly_exposed'][:5])})"
                        if delta["newly_exposed"] else "none newly exposed")
                     + ", " + (f"{len(delta['no_longer_exposed'])} fixed or no longer reachable "
                               f"({', '.join(delta['no_longer_exposed'][:5])})" if delta["no_longer_exposed"]
                               else "none fixed") + f" since {up.get('prev_seen')}.")
        else:
            dtext = (f"Re-triage #{up['triage_count']} of {self.incident_id}: nothing changed since "
                     f"{up.get('prev_seen')}, the same {n} service{'s' if n != 1 else ''} "
                     f"{'are' if n != 1 else 'is'} exposed.")
        self.finding(tr, "triage", "memory_delta", "What changed since last time", dtext,
                     {**delta, "incident_id": self.incident_id})
        return text

    def investigator(self, tr: Trace) -> str:
        up = self.read_upstream(tr, "investigator")
        br = up["blast_radius"]["data"]
        ent = up["entity"]["data"]
        exposed, safe = br["exposed"], br["safe"]
        carriers: Counter = Counter()
        deepest = None
        chains = []
        for e in exposed:
            f = e["findings"][0]
            via = f["path"]["via"]
            if len(via) > 2:
                carriers[via[1].rsplit("@", 1)[0]] += 1
            if not deepest or f["path"]["hops"] > deepest["hops"]:
                deepest = {"service": e["service"], "hops": f["path"]["hops"], "display": f["path"]["display"]}
            chains.append({"service": e["service"], "tier": e.get("tier"), "package": f["package"],
                           "bad_version": f["bad_version"], "hops": f["path"]["hops"],
                           "display": f["path"]["display"], "direct": f.get("direct_exposed", False)})
        shortest = None
        crit = next((e for e in exposed if e.get("tier") == 1), exposed[0] if exposed else None)
        if crit:
            p = tools.path_between(tr, crit["service"], crit["findings"][0]["package"])
            if p:
                shortest = {"service": crit["service"], "package": crit["findings"][0]["package"], **p}
        direct = sum(1 for c in chains if c["direct"])
        text = (f"{len(chains)} exposure chain{'s' if len(chains) != 1 else ''}: {direct} direct, "
                f"{len(chains) - direct} transitive" + (f", deepest {deepest['hops']} hops ({deepest['service']})"
                                                         if deepest else "")
                + (f". Most common carrier: {carriers.most_common(1)[0][0]} "
                   f"({carriers.most_common(1)[0][1]} services)." if carriers else ".")) if chains else \
            "No exposure chains: nothing in the fleet can install a malicious version at this depth."
        self.finding(tr, "investigator", "paths", "Exposure paths", text,
                     {"chains": chains[:25], "carriers": carriers.most_common(5), "deepest": deepest,
                      "shortest_to_critical": shortest})
        neg = ("; ".join(f"{s['service']} (range {s['requirement']} excludes it)" for s in safe[:4])
               + (f" and {len(safe) - 4} more" if len(safe) > 4 else "")) if safe else ""
        ntext = (f"{len(safe)} service{'s' if len(safe) != 1 else ''} reach an affected package but cannot install "
                 f"the bad version: {neg}." if safe else
                 f"No near misses: {br['clean_count']} services never reach an affected package.")
        self.finding(tr, "investigator", "honest_negatives", "Honest negatives", ntext,
                     {"safe": safe, "clean_count": br["clean_count"]})
        spof = tools.single_points_of_failure(tr, limit=5)
        top = spof["maintainers"][:3]
        involved = set()
        if ent.get("entity_kind") == "maintainer":
            involved.add(ent.get("entity_key"))
        stext = ("Riskiest npm accounts for this fleet: "
                 + ", ".join(f"{m['maintainer']} ({m['reach']} services)" for m in top) + "."
                 if top else "No maintainer ranking available.")
        if involved and any(m["maintainer"] in involved for m in spof["maintainers"]):
            rank = next(i for i, m in enumerate(spof["maintainers"], 1) if m["maintainer"] in involved)
            stext += f" The compromised account {ent['entity_key']} ranks #{rank}."
        self.finding(tr, "investigator", "spof", "Single points of failure", stext,
                     {"maintainers": spof["maintainers"][:5], "pagerank": spof["pagerank"][:5]})
        return text

    def responder(self, tr: Trace) -> str:
        up = self.read_upstream(tr, "responder")
        br = up["blast_radius"]["data"]
        ent = up["entity"]["data"]
        services = [e["service"] for e in br["exposed"]]
        wake = tools.who_to_wake(tr, services) if services else []
        by_team: dict[str, dict] = {}
        exposed_by = {e["service"]: e for e in br["exposed"]}
        for w in wake:
            t = by_team.setdefault(w["team"], dict(team=w["team"], team_display=w["team_display"], slack=w["slack"],
                                                   pager=w["pager"], oncall=w["oncall"], services=[], top_tier=9))
            t["services"].append(w["service"])
            t["top_tier"] = min(t["top_tier"], w.get("tier") or 9)
        teams = sorted(by_team.values(), key=lambda t: (t["top_tier"], t["team"]))
        drafts = []
        for t in teams:
            oc = ", ".join(f"{o['name']} ({o['handle']})" for o in t["oncall"]) or "no on-call recorded"
            pkgs = sorted({p for s in t["services"] for p in exposed_by[s]["exposed_packages"]})
            sev = "SEV1" if t["top_tier"] == 1 else ("SEV2" if t["top_tier"] == 2 else "SEV3")
            drafts.append(dict(team=t["team"], channel=t["slack"], pager=t["pager"], severity=sev, text=(
                f"[{sev}] PatientZero: {ent['trigger']}. {len(t['services'])} {t['team_display']} "
                f"service{'s' if len(t['services']) != 1 else ''} can install a malicious npm version: "
                f"{', '.join(t['services'])} (via {', '.join(pkgs[:4])}{'...' if len(pkgs) > 4 else ''}). "
                f"On-call: {oc}. Incident {self.incident_id}. Patch plan follows from the Fixer.")))
        text = (f"Wake {len(teams)} team{'s' if len(teams) != 1 else ''}: "
                + "; ".join(f"{t['team_display']} ({', '.join(o['name'] for o in t['oncall']) or 'no on-call'})"
                            for t in teams) + ".") if teams else "Nobody to wake: no service is exposed."
        self.finding(tr, "responder", "wake", "Who to wake", text, {"teams": teams, "wake": wake})
        self.finding(tr, "responder", "page_drafts", "Page drafts",
                     f"{len(drafts)} page draft{'s' if len(drafts) != 1 else ''} ready, highest severity first. "
                     "Nothing is sent automatically." if drafts else "No pages needed.", {"drafts": drafts})
        return text

    def fixer(self, tr: Trace) -> str:
        up = self.read_upstream(tr, "fixer")
        br = up["blast_radius"]["data"]
        wake = up.get("wake", {}).get("data", {}).get("wake", [])
        paths = up.get("paths", {}).get("data", {})
        if not br["exposed"]:
            text = "Nothing to patch: no service can install a malicious version."
            self.finding(tr, "fixer", "patch_plan", "Patch plan", text, {"per_service": [], "combined": {}})
            return text
        fix = tools.build_fix({"exposed": br["exposed"]}, wake)
        patch = {p["service"]: p["package_json"] for p in fix["per_service"]}
        tr.run("crew:fixer", "crew_set_patch", {"id": self.incident_id, "patch": _j(patch)}, write=True)
        tiers = {e["service"]: e.get("tier") for e in br["exposed"]}
        order = sorted(patch, key=lambda s: (tiers.get(s) or 9, s))
        carrier = (paths.get("carriers") or [[None, 0]])[0]
        text = (f"Install-safe patches for {len(patch)} service{'s' if len(patch) != 1 else ''}, tier 1 first "
                f"({', '.join(order[:3])}{'...' if len(order) > 3 else ''}); {len(fix['specs'])} scoped pin"
                f"{'s' if len(fix['specs']) != 1 else ''}" + (f"; {carrier[0]} is the most common carrier "
                                                              f"({carrier[1]} services)" if carrier[0] else "") + ".")
        self.finding(tr, "fixer", "patch_plan", "Patch plan", text, {
            "order": order, "specs": fix["specs"], "combined": fix["combined"], "pins": fix["pins"],
            "per_service": [{"service": p["service"], "tier": p["tier"], "package_json": p["package_json"]}
                            for p in fix["per_service"]], "incident_id": self.incident_id})
        # Procedural memory: prefer the approved playbook with the most successful reuses.
        cands = playbooks.matching(tr, sorted({tools.spec_package(k) for k in fix["specs"]}))
        if cands:
            best = cands[0]
            shared = [m for m in best["matched"] if m.get("spec") in fix["specs"]]
            agree = bool(shared) and all(fix["specs"][m["spec"]] == m["pin"] for m in shared)
            tr.run("crew:fixer", "playbook_apply", {"incident_id": self.incident_id, "playbook_id": best["id"],
                                                    "at": tools.now_iso()}, write=True)
            uses = best.get("uses_count") or 0
            if agree:
                used = tr.run("crew:fixer", "playbook_use", {"playbook_id": best["id"], "at": tools.now_iso()},
                              write=True)
                uses = used[0]["uses_count"] if used else uses + 1
            ptext = (f"Reusing approved playbook {best['id']} (approved by {best.get('approved_by')}, now used "
                     f"{uses} time{'s' if uses != 1 else ''})" + ("; its pins match today's plan." if agree else
                                                                  "; its pins differ from today's plan, review first."))
            self.finding(tr, "fixer", "playbook", "Playbook reuse", ptext, {
                "playbook_id": best["id"], "uses_count": uses, "agrees": agree, "approved_by": best.get("approved_by"),
                "source_incident": best.get("source_incident"),
                "candidates": [{"id": c["id"], "uses_count": c.get("uses_count") or 0,
                                "matched": len(c["matched"])} for c in cands[:5]]})
        else:
            self.finding(tr, "fixer", "playbook", "Playbook reuse",
                         "No approved playbook covers these packages yet. Approve this incident to create one.",
                         {"playbook_id": None, "candidates": []})
        return text

    # ------------------------------------------------------------------ orchestration
    async def run(self) -> dict:
        tenants.set_current(self.tenant)
        tr = self._tr()
        tr.run("crew", "crew_create_run", dict(
            id=self.run_id, query=self.query[:500], tenant=self.tenant, depth=self.depth,
            session_id=self.session_id, started=tools.now_iso(),
            agents=[{"id": self.agent_ids[a], "agent": a} for a in AGENTS]), write=True)
        self._keep(tr, "crew")
        self.emit("run", {"tenant": self.tenant, "company": tenants.company(), "query": self.query,
                          "depth": self.depth, "session_id": self.session_id,
                          "agents": [{"agent": a, "title": TITLES[a], "role": ROLES[a],
                                      "agent_run_id": self.agent_ids[a]} for a in AGENTS],
                          "dag": [list(e) for e in DAG]})
        status, message = "done", ""
        try:
            st = await asyncio.to_thread(self._run_agent, "triage", self.triage)
            if st != "done":
                status, message = "no_incident", self._summary("triage")
            else:
                tr = self._tr()
                self.handoff(tr, "triage", "investigator", "blast radius ready: explain the paths")
                self.handoff(tr, "triage", "responder", "exposed services ready: decide who to wake")
                self._keep(tr, "crew")
                await asyncio.gather(asyncio.to_thread(self._branch, "investigator", self.investigator,
                                                       "paths and negatives ready: order the fix"),
                                     asyncio.to_thread(self._branch, "responder", self.responder,
                                                       "owners and page drafts ready: attach the patch"))
                await asyncio.to_thread(self._run_agent, "fixer", self.fixer)
                message = self._summary("triage")
        except Exception as e:  # noqa: BLE001
            status, message = "error", f"{type(e).__name__}: {e}"
            self.emit("error", {"message": message})
        tr = self._tr()
        tr.run("crew", "crew_finish", dict(id=self.run_id, status=status, finished=tools.now_iso(),
                                           ms=_ms(self.t0), incident=self.incident_id, message=message[:600]),
               write=True)
        self._keep(tr, "crew")
        result = await asyncio.to_thread(get_run, self.run_id, self.tenant)
        result["trace"] = self.trace
        self.emit("done", result)
        return result

    def _branch(self, agent: str, fn, reason: str) -> None:
        self._run_agent(agent, fn)
        tr = self._tr()
        self.handoff(tr, agent, "fixer", reason)
        self._keep(tr, "crew")

    def _summary(self, agent: str) -> str:
        for ev in reversed(self.events):
            if ev["event"] == "agent_done" and ev["data"]["agent"] == agent:
                return ev["data"].get("summary") or ""
        return ""


def _slim_finding(f: dict) -> dict:
    keep = ("via", "requirements", "requirement", "hops", "exposed", "reason", "display")
    return dict(package=f["package"], bad_version=f["bad_version"], clean_version=f.get("clean_version"),
                advisory_id=f.get("advisory_id"), ghsa=f.get("ghsa"), exposed=True,
                direct_exposed=f.get("direct_exposed", False),
                path={k: f["path"].get(k) for k in keep},
                alternatives=[{k: p.get(k) for k in keep} for p in (f.get("alternatives") or [])[:3]])


def get_run(run_id: str, tenant: str | None = None) -> dict | None:
    """Replay a crew run entirely from the graph: run, agents, findings and the handoff DAG."""
    if tenant:
        tenants.set_current(tenant)
    tr = Trace()
    rows = tr.run("crew", "crew_get_run", {"id": run_id})
    if not rows:
        return None
    r = rows[0]
    agents = tr.run("crew", "crew_get_agents", {"id": run_id})
    for a in agents:
        fs = []
        for f in a.get("findings") or []:
            if not f:
                continue
            try:
                f["data"] = json.loads(f["data"]) if f.get("data") else {}
            except (TypeError, ValueError):
                f["data"] = {}
            fs.append(f)
        a["findings"] = fs
        a["title"] = TITLES.get(a["agent"], a["agent"])
        a["role"] = ROLES.get(a["agent"], "")
    agents.sort(key=lambda a: AGENTS.index(a["agent"]) if a["agent"] in AGENTS else 9)
    handoffs = tr.run("crew", "crew_get_handoffs", {"id": run_id})
    try:
        delta = json.loads(r["last_delta"]) if r.get("last_delta") else None
    except (TypeError, ValueError):
        delta = None
    run = r["run"]
    return {"run_id": run_id, "tenant": run.get("tenant"), "company": tenants.company(run.get("tenant")
                                                                                     if run.get("tenant") in
                                                                                     tenants.TENANTS else None),
            "query": run.get("query"), "depth": run.get("depth"), "session_id": run.get("session_id"),
            "status": run.get("status"), "started": run.get("started"), "finished": run.get("finished"),
            "ms": run.get("ms"), "message": run.get("message"),
            "incident": {"id": r.get("incident_id"), "triage_count": r.get("triage_count"),
                         "first_seen": r.get("first_seen"), "last_seen": r.get("last_seen"), "delta": delta}
            if r.get("incident_id") else None,
            "agents": agents, "handoffs": handoffs, "dag": [list(e) for e in DAG], "replay_trace": tr.steps}

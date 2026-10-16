"""Crew handoffs through graph state, conversation sessions, tenant isolation and memory that updates over time.

Runs against private COPIES of fleet:acme and fleet:globex (GRAPH.COPY), so the real local graphs and their demo
memory are never touched. Skipped when FalkorDB or either tenant graph is missing. Zero LLM calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db, tenants
from app.crew import api as crew_api
from app.crew.engine import DAG, Crew
from app.main import app
from app.semver_util import check

ping = db.ping()
LOADED = ping.get("ok") and all((ping.get("tenants_loaded") or {}).get(t) for t in ("acme", "globex"))
pytestmark = pytest.mark.skipif(not LOADED, reason="FalkorDB with fleet:acme and fleet:globex not available "
                                                   "(TENANT=globex python ingest/load.py)")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QIX = "maintainer qix was phished"
SHAI = "Shai-Hulud"
GLOBEX_SHAI_EXPOSED = {"globex-storefront", "ops-console", "partner-api", "brand-studio"}


@pytest.fixture(scope="module", autouse=True)
def tenant_copies():
    """Point both tenants at fresh copies of their graphs for this module, with memory wiped."""
    suffix = uuid.uuid4().hex[:6]
    orig = dict(tenants.TENANTS)
    c = db.client()
    for t in ("acme", "globex"):
        copy = f"test:{t}-{suffix}"
        c.connection.execute_command("GRAPH.COPY", orig[t], copy)
        g = c.select_graph(copy)
        g.query("MATCH (n) WHERE n:Incident OR n:Finding OR n:CrewRun OR n:AgentRun OR n:Session OR n:Turn "
                "OR n:Playbook DETACH DELETE n")
        tenants.TENANTS[t] = copy
    crew_api.limiter.limit = 10_000
    yield
    for t in ("acme", "globex"):
        try:
            c.select_graph(tenants.TENANTS[t]).delete()
        except Exception:  # noqa: BLE001
            pass
    tenants.TENANTS.clear()
    tenants.TENANTS.update(orig)
    crew_api.limiter.limit = 6
    crew_api.limiter.reset()


client = TestClient(app)


def sid() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


def ask(q, tenant="acme", **kw):
    r = client.post("/api/ask", json={"query": q, "tenant": tenant, "mode": "deterministic", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def crew(q, tenant="acme", **kw) -> dict:
    r = client.post("/api/crew/run?stream=false", json={"query": q, "tenant": tenant, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def g(tenant):
    return db.client().select_graph(tenants.TENANTS[tenant])


def findings(run: dict) -> dict[str, dict]:
    return {f["kind"]: f for a in run["agents"] for f in a["findings"]}


# ------------------------------------------------------------------------------------------------ tenants
def test_tenants_listed_and_validated():
    r = client.get("/api/tenants").json()
    assert {t["id"] for t in r["tenants"]} >= {"acme", "globex"} and r["default"] == "acme"
    assert all(t["loaded"] for t in r["tenants"] if t["id"] in ("acme", "globex"))
    for bad in ("evil", "ACME;DROP", "../x", "a" * 40):
        assert client.get("/api/fleet", params={"tenant": bad}).status_code == 400
        assert client.post("/api/ask", json={"query": "qix", "tenant": bad}).status_code in (400, 422)
        assert client.post("/api/crew/run?stream=false", json={"query": "qix", "tenant": bad}).status_code in (400, 422)
    assert client.get("/api/fleet", headers={"X-Tenant": "nope"}).status_code == 400
    acme = client.get("/api/fleet").json()
    globex = client.get("/api/fleet", headers={"X-Tenant": "globex"}).json()
    assert acme["company"] == "Acme" and len(acme["services"]) == 22
    assert globex["company"] == "Globex" and len(globex["services"]) == 12
    assert not {s["service"] for s in acme["services"]} & {s["service"] for s in globex["services"]}


def test_globex_is_hit_by_shai_hulud_and_acme_is_not():
    a = ask(SHAI, "acme")
    assert a["outcome"] == "not_in_graph" and a["exposed_count"] == 0
    gx = ask(SHAI, "globex")
    assert gx["outcome"] == "answered_with_path"
    assert {e["service"] for e in gx["exposed"]} == GLOBEX_SHAI_EXPOSED
    assert "design-tokens" in {e["service"] for e in gx["safe"]}  # exact pin 4.1.0 excludes 4.1.1 / 4.1.2
    # Every exposure claim checks out against the ingested advisory data and npm semver.
    inc = json.load(open(os.path.join(ROOT, "data", "incidents", "2025-09-15-shai-hulud.json")))
    bad = {c["package"]: set(c["bad_versions"]) for c in inc["compromised"]}
    for e in gx["exposed"]:
        for f in e["findings"]:
            if f["exposed"]:
                assert f["bad_version"] in bad[f["package"]]
                assert check(f["bad_version"], f["path"]["requirement"], f["package"])[0]
    # The qix incident hits Globex differently from Acme.
    assert ask(QIX, "globex")["exposed_count"] != ask(QIX, "acme")["exposed_count"]


# ------------------------------------------------------------------------------------------------ crew
def test_crew_dag_shape_and_graph_writes():
    run = crew(QIX, "globex")
    assert run["status"] == "done", run["message"]
    assert [a["agent"] for a in run["agents"]] == ["triage", "investigator", "responder", "fixer"]
    assert all(a["status"] == "done" and a["ms"] is not None for a in run["agents"])
    edges = {(h["from_agent"], h["to_agent"]) for h in run["handoffs"]}
    assert edges == set(DAG) and len(run["handoffs"]) == 4
    assert all(h["reason"] for h in run["handoffs"])
    fs = findings(run)
    assert {"entity", "blast_radius", "memory_delta", "paths", "honest_negatives", "spof", "wake", "page_drafts",
            "patch_plan", "playbook"} <= set(fs)
    cnt = g("globex").ro_query(
        "MATCH (r:CrewRun {id:$id})-[:RAN]->(a:AgentRun) OPTIONAL MATCH (a)-[:PRODUCED]->(f:Finding) "
        "RETURN count(DISTINCT a), count(f)", {"id": run["run_id"]}).result_set[0]
    assert cnt == [4, len(fs)]
    # Findings hang off the Incident too, and the Incident's EXPOSED edges match the blast radius.
    inc = run["incident"]["id"]
    n = g("globex").ro_query("MATCH (:Incident {id:$id})-[:HAS_FINDING]->(f:Finding {run_id:$r}) RETURN count(f)",
                             {"id": inc, "r": run["run_id"]}).result_set[0][0]
    assert n == len(fs)
    exp = g("globex").ro_query("MATCH (i:Incident {id:$id})-[e:EXPOSED]->() RETURN i.exposed_count, count(e)",
                               {"id": inc}).result_set[0]
    assert exp[0] == exp[1] == len(fs["blast_radius"]["data"]["exposed"]) == 5
    # Responder's wake list covers exactly the exposed services; Fixer patches exactly those.
    exposed = {e["service"] for e in fs["blast_radius"]["data"]["exposed"]}
    assert {w["service"] for w in fs["wake"]["data"]["wake"]} == exposed
    assert set(fs["patch_plan"]["data"]["order"]) == exposed


def test_investigator_and_responder_run_concurrently(monkeypatch):
    orig = Crew.investigator

    def slow(self, tr):
        time.sleep(0.25)
        return orig(self, tr)
    monkeypatch.setattr(Crew, "investigator", slow)
    with tenants.use("globex"):
        c = Crew(QIX, 4, "globex")
        asyncio.run(c.run())
    order = [(e["event"], e["data"].get("agent")) for e in c.events if e["event"] in ("agent_start", "agent_done")]
    pos = {k: i for i, k in enumerate(order)}
    assert pos[("agent_start", "responder")] < pos[("agent_done", "investigator")]
    assert pos[("agent_start", "investigator")] < pos[("agent_done", "responder")]
    assert pos[("agent_start", "fixer")] > max(pos[("agent_done", "investigator")], pos[("agent_done", "responder")])
    assert pos[("agent_done", "triage")] < min(pos[("agent_start", "investigator")], pos[("agent_start", "responder")])


def test_agents_read_upstream_findings_from_the_graph(monkeypatch):
    """Tamper with Triage's Finding in FalkorDB after it is written: downstream agents must see the tampered data,
    which proves they read shared state from the graph rather than from Python memory."""
    orig = Crew.handoff

    def handoff(self, tr, src, dst, reason):
        if (src, dst) == ("triage", "investigator"):
            gr = db.client().select_graph(tenants.graph_name())
            row = gr.ro_query("MATCH (f:Finding {id:$id}) RETURN f.data",
                              {"id": f"{self.agent_ids['triage']}-blast_radius"}).result_set[0][0]
            data = json.loads(row)
            data["exposed"] = [e for e in data["exposed"] if e["service"] == "partner-api"]
            gr.query("MATCH (f:Finding {id:$id}) SET f.data = $d",
                     {"id": f"{self.agent_ids['triage']}-blast_radius", "d": json.dumps(data)})
        return orig(self, tr, src, dst, reason)
    monkeypatch.setattr(Crew, "handoff", handoff)
    with tenants.use("globex"):
        c = Crew(QIX, 4, "globex")
        run = asyncio.run(c.run())
    fs = findings(run)
    assert len(fs["blast_radius"]["data"]["exposed"]) == 1  # what is stored now
    assert [ch["service"] for ch in fs["paths"]["data"]["chains"]] == ["partner-api"]
    assert {w["service"] for w in fs["wake"]["data"]["wake"]} == {"partner-api"}
    assert fs["patch_plan"]["data"]["order"] == ["partner-api"]
    reads = [e["data"] for e in c.events if e["event"] == "read"]
    assert {r["agent"] for r in reads} == {"investigator", "responder", "fixer"}
    fixer_read = next(r for r in reads if r["agent"] == "fixer")
    assert {f["agent"] for f in fixer_read["findings"]} == {"triage", "investigator", "responder"}
    assert all(r["query"] == "crew_read_upstream" for r in reads)


def test_crew_sse_stream_and_replay():
    events, run_id = [], None
    with client.stream("POST", "/api/crew/run", json={"query": SHAI, "tenant": "globex"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        run_id = r.headers["x-crew-run"]
        ev = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                events.append((ev, json.loads(line[6:])))
    kinds = [e for e, _ in events]
    assert kinds[0] == "run" and kinds[-1] == "done"
    assert kinds.count("agent_start") == 4 and kinds.count("agent_done") == 4 and kinds.count("handoff") == 4
    assert kinds.count("read") == 3 and "incident" in kinds and "error" not in kinds
    assert all(d["run_id"] == run_id for _, d in events)
    assert events[0][1]["dag"] == [list(e) for e in DAG]
    done = events[-1][1]
    assert done["status"] == "done" and len(done["agents"]) == 4
    rep = client.get(f"/api/crew/{run_id}", params={"tenant": "globex"})
    assert rep.status_code == 200
    rj = rep.json()
    assert [a["agent"] for a in rj["agents"]] == [a["agent"] for a in done["agents"]]
    assert sum(len(a["findings"]) for a in rj["agents"]) == kinds.count("finding")
    # Isolation and validation on replay.
    assert client.get(f"/api/crew/{run_id}", params={"tenant": "acme"}).status_code == 404
    assert client.get("/api/crew/not-a-run").status_code == 400
    assert run_id in {x["id"] for x in client.get("/api/crew", params={"tenant": "globex"}).json()["runs"]}


def test_crew_unknown_entity_is_honest():
    run = crew("left-pad was compromised", "acme")
    assert run["status"] == "no_incident"
    assert [a["status"] for a in run["agents"]][:1] == ["no_incident"]
    assert run["handoffs"] == [] and run["incident"] is None


def test_crew_rate_limit():
    crew_api.limiter.reset()
    crew_api.limiter.limit = 6
    try:
        hdr = {"X-Forwarded-For": "203.0.113.77"}
        codes = [client.post("/api/crew/run?stream=false", json={"query": "left-pad was compromised"},
                             headers=hdr).status_code for _ in range(7)]
        assert codes[:6] == [200] * 6 and codes[6] == 429
    finally:
        crew_api.limiter.limit = 10_000
        crew_api.limiter.reset()


# ------------------------------------------------------------------------------------------------ memory over time
def test_retriage_updates_incident_with_delta():
    first = crew(SHAI, "globex", depth=5)
    inc = first["incident"]
    assert findings(first)["memory_delta"]["data"]["first_triage"] is True
    gx = g("globex")
    try:
        gx.query("MATCH (:Service {name:'brand-studio'})-[d:DEPENDS_ON]->(:Version {pkg:'@ctrl/tinycolor'}) "
                 "SET d.requirement = '4.1.0'")  # brand-studio pins the clean version: fixed
        second = crew(SHAI, "globex", depth=5)
        d2 = findings(second)["memory_delta"]["data"]
        assert second["incident"]["id"] == inc["id"]
        assert second["incident"]["triage_count"] == inc["triage_count"] + 1
        assert d2["no_longer_exposed"] == ["brand-studio"] and d2["newly_exposed"] == []
        assert "brand-studio" in findings(second)["memory_delta"]["text"]
    finally:
        gx.query("MATCH (:Service {name:'brand-studio'})-[d:DEPENDS_ON]->(:Version {pkg:'@ctrl/tinycolor'}) "
                 "SET d.requirement = '^4.1.0'")
    third = crew(SHAI, "globex", depth=5)
    d3 = findings(third)["memory_delta"]["data"]
    assert d3["newly_exposed"] == ["brand-studio"]
    row = gx.ro_query("MATCH (i:Incident {id:$id}) RETURN i.triage_count, i.last_seen >= i.first_seen, "
                      "i.exposed_count, size([(i)-[:EXPOSED]->(s) | s])", {"id": inc["id"]}).result_set[0]
    assert row[0] == inc["triage_count"] + 2 and row[1] is True and row[2] == row[3] == 4


def test_fixer_prefers_most_reused_playbook():
    a = g("acme")
    ids = []
    for _ in range(2):
        r = client.post("/api/fix", json={"query": QIX, "tenant": "acme"})
        assert r.status_code == 200, r.text
        inc = r.json()["fix"]["incident_id"]
        ap = client.post(f"/api/incidents/{inc}/approve", json={"approved_by": "test"}, params={"tenant": "acme"})
        assert ap.status_code == 200, ap.text
        ids.append(ap.json()["playbook"]["id"])
    a.query("MATCH (pb:Playbook {id:$id}) SET pb.uses_count = 3", {"id": ids[0]})
    run = crew(QIX, "acme")
    pb = findings(run)["playbook"]["data"]
    assert pb["playbook_id"] == ids[0] and pb["agrees"] is True and pb["uses_count"] == 4
    lst = {p["id"]: p for p in client.get("/api/playbooks", params={"tenant": "acme"}).json()["playbooks"]}
    assert lst[ids[0]]["uses_count"] == 4 and lst[ids[0]].get("last_used")
    # Tenant isolation: Globex has no playbooks, recalls nothing about acme's incidents, and its Fixer reuses none.
    assert client.get("/api/playbooks", params={"tenant": "globex"}).json()["playbooks"] == []
    acme_incs = {i["id"] for i in client.get("/api/incidents", params={"tenant": "acme"}).json()["incidents"]}
    globex_incs = {i["id"] for i in client.get("/api/incidents", params={"tenant": "globex"}).json()["incidents"]}
    assert acme_incs and not acme_incs & globex_incs
    gq = json.dumps(ask("qix", "globex"))  # Globex may remember its own crew runs, never Acme's incident or playbook
    assert not any(x in gq for x in acme_incs | set(ids))
    assert findings(crew(QIX, "globex"))["playbook"]["data"]["playbook_id"] is None
    assert ask("qix", "acme")["outcome"] == "answered_from_memory"


def test_approved_incident_in_acme_is_not_recalled_in_globex():
    """Strict isolation on a pristine pair: approve in acme, then globex has no memory of it at all."""
    gx = g("globex")
    gx.query("MATCH (n) WHERE n:Incident OR n:Finding OR n:Playbook OR n:CrewRun OR n:AgentRun DETACH DELETE n")
    r = client.post("/api/fix", json={"query": QIX, "tenant": "acme", "depth": 2})
    inc = r.json()["fix"]["incident_id"]
    assert client.post(f"/api/incidents/{inc}/approve", params={"tenant": "acme"}).status_code == 200
    assert ask("qix", "acme", depth=2)["outcome"] == "answered_from_memory"
    gq = ask("qix", "globex", depth=2)
    assert gq["outcome"] != "answered_from_memory" and inc not in json.dumps(gq)
    assert client.get("/api/incidents", params={"tenant": "globex"}).json()["incidents"] == []
    assert client.get(f"/api/incidents/{inc}/check", params={"tenant": "globex"}).json()["exposed_edges"] == 0
    assert client.post(f"/api/incidents/{inc}/approve", params={"tenant": "globex"}).status_code == 404


# ------------------------------------------------------------------------------------------------ sessions
def test_session_followups_resolve_from_graph():
    s = sid()
    t1 = ask(QIX, "acme", session_id=s)
    assert t1["session"]["turn"] == 1 and t1["exposed_count"] == 13
    t2 = ask("who should I wake for that?", "acme", session_id=s)
    assert t2["followup"]["resolved"] and t2["followup"]["from_turn"] == 1
    assert "qix" in t2["resolved_query"] and t2["query"] == "who should I wake for that?"
    assert {w["service"] for w in t2["wake"]} == {e["service"] for e in t1["exposed"]}
    t3 = ask("and at depth 6?", "acme", session_id=s)
    assert t3["depth"] == 6 and t3["followup"]["resolved"] and t3["session"]["turn"] == 3
    t4 = ask("who to wake for auth-service", "acme", session_id=s)
    assert "followup" not in t4 or not t4.get("followup")
    t5 = ask("who should I wake for that?", "acme", session_id=s)
    assert t5["resolved_query"] == "who should I wake for auth-service" and t5["wake"][0]["service"] == "auth-service"
    h = client.get(f"/api/sessions/{s}", params={"tenant": "acme"}).json()
    assert h["turns"] == 5 and [t["i"] for t in h["history"]] == [1, 2, 3, 4, 5]
    assert {"kind": "Maintainer", "key": "qix"} in h["history"][0]["about"]
    assert h["history"][1]["followup_from"] == 1
    assert {"kind": "Service", "key": "auth-service"} in h["history"][3]["about"]
    # Same id, other tenant: no such session (each tenant's graph holds its own history).
    assert client.get(f"/api/sessions/{s}", params={"tenant": "globex"}).status_code == 404


def test_followup_without_history_is_not_guessed():
    lone = ask("who should I wake for that?", "acme", session_id=sid())
    assert lone["followup"]["resolved"] is False and lone["session"]["turn"] == 1
    plain = ask("who should I wake for that?", "acme")
    assert "followup" not in plain or not plain.get("followup")


def test_session_validation():
    for bad in ("short", "has space in it", "x" * 65, "../../etc/passwd", "a;b<c>d"):
        r = client.post("/api/ask", json={"query": "qix", "session_id": bad})
        assert r.status_code in (400, 422), bad
    assert client.get("/api/sessions/bad id!").status_code in (400, 404)
    assert client.get(f"/api/sessions/{sid()}").status_code == 404


def test_crew_turn_feeds_followups():
    s = sid()
    run = crew(SHAI, "globex", session_id=s)
    assert run["session"]["turn"] == 1
    t2 = ask("who should I wake for that?", "globex", session_id=s)
    assert t2["resolved_query"] == "who should I wake for Shai-Hulud"
    assert {w["service"] for w in t2["wake"]} == GLOBEX_SHAI_EXPOSED
    h = client.get(f"/api/sessions/{s}", params={"tenant": "globex"}).json()
    assert h["history"][0]["kind"] == "crew" and h["history"][0]["crew_run"] == run["run_id"]
    assert any(a["kind"] == "Incident" for a in h["history"][0]["about"])


# ------------------------------------------------------------------------------------------------ concurrency
def test_twenty_concurrent_crew_runs():
    """20 crews at once (10 per tenant) against FalkorDB: no errors, exact node/edge counts, one Incident per
    (tenant, trigger, depth) with triage_count incremented exactly once per run."""
    def count(tenant, key):
        rows = g(tenant).ro_query("MATCH (i:Incident {crew_key:$k}) RETURN i.id, i.triage_count", {"k": key}).result_set
        return rows

    before = {t: (count(t, "qix|d3")[0][1] if count(t, "qix|d3") else 0) for t in ("acme", "globex")}

    async def main():
        crews = [Crew(QIX, 3, t) for t in ("acme", "globex") for _ in range(10)]
        res = await asyncio.gather(*(c.run() for c in crews))
        return crews, res
    crews, res = asyncio.run(main())
    assert all(r["status"] == "done" for r in res), [r["message"] for r in res if r["status"] != "done"]
    for c, r in zip(crews, res):
        cnt = g(c.tenant).ro_query("MATCH (r:CrewRun {id:$id})-[:RAN]->(a:AgentRun) "
                                   "OPTIONAL MATCH (a)-[h:HANDED_OFF_TO]->() RETURN count(DISTINCT a), count(h)",
                                   {"id": c.run_id}).result_set[0]
        assert cnt == [4, 4], (c.run_id, cnt)
        assert sum(len(a["findings"]) for a in r["agents"]) == 10
        assert not any(e["event"] == "error" for e in c.events)
    for t in ("acme", "globex"):
        rows = count(t, "qix|d3")
        assert len(rows) == 1, rows
        assert rows[0][1] == before[t] + 10
        inc = rows[0][0]
        e = g(t).ro_query("MATCH (i:Incident {id:$id}) RETURN i.exposed_count, size([(i)-[:EXPOSED]->(s) | s])",
                          {"id": inc}).result_set[0]
        assert e[0] == e[1] > 0
        # Every run in this tenant triaged that same incident.
        n = g(t).ro_query("MATCH (r:CrewRun)-[:TRIAGED]->(:Incident {id:$id}) RETURN count(r)",
                          {"id": inc}).result_set[0][0]
        assert n == before[t] + 10


# ------------------------------------------------------------------------------------------------ MCP + guards
def test_mcp_tools_and_cross_tenant_guard():
    from mcp.shared.memory import create_connected_server_and_client_session

    from app.mcp_server import mcp

    async def main():
        async with create_connected_server_and_client_session(mcp._mcp_server) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert {"crew_run", "session_history", "list_tenants"} <= names
            lt = json.loads((await c.call_tool("list_tenants", {})).content[0].text)
            assert {t["id"] for t in lt["tenants"]} >= {"acme", "globex"}
            s = sid()
            run = json.loads((await c.call_tool("crew_run", {"query": QIX, "tenant": "globex", "depth": 4,
                                                            "session_id": s})).content[0].text)
            assert run["status"] == "done" and run["session"]["turn"] == 1
            h = json.loads((await c.call_tool("session_history", {"session_id": s, "tenant": "globex"})).content[0].text)
            assert h["found"] and h["history"][0]["crew_run"] == run["run_id"]
            other = json.loads((await c.call_tool("session_history", {"session_id": s, "tenant": "acme"})).content[0].text)
            assert other["found"] is False
    asyncio.run(main())
    with tenants.use("acme"):
        assert tenants.for_tool("acme") == "acme"
        with pytest.raises(ValueError):
            tenants.for_tool("globex")


def test_reset_memory_is_per_tenant():
    s1, s2 = sid(), sid()
    ask(QIX, "acme", session_id=s1)
    ask(QIX, "globex", session_id=s2)
    with tenants.use("globex"):
        from app.db import Trace
        Trace().run("recall", "reset_memory", write=True)
    assert client.get(f"/api/sessions/{s2}", params={"tenant": "globex"}).status_code == 404
    assert client.get(f"/api/sessions/{s1}", params={"tenant": "acme"}).status_code == 200
    assert g("globex").ro_query("MATCH (n) WHERE n:CrewRun OR n:AgentRun OR n:Turn RETURN count(n)").result_set[0][0] == 0

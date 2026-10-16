"""Review 3: LLM answers use the panels of the tool that ran, wake follow-ups lead with people, one incident per
trigger (no self-referential 'Seen before'), clean 503s on any DB failure, crew warm-up, session turn dedupe,
spoof-resistant client IPs. Zero real LLM calls (the OpenAI client is mocked). Runs on private graph copies."""
from __future__ import annotations

import json

import pytest
import redis

from app import config, db
from app.byo import ratelimit
from app.core import agent
from tests.test_crew_sessions_tenants import (LOADED, QIX, ask, client, crew, findings, g, sid,  # noqa: F401
                                              tenant_copies)

live = pytest.mark.skipif(not LOADED, reason="FalkorDB with fleet:acme and fleet:globex not available")


# ------------------------------------------------------------------------------------------------ P1-1 LLM mode
def fake_llm(monkeypatch, calls: list[tuple[str, dict]], final: str):
    """Scripted chat model: the given tool calls (one per turn), then a final sentence. No network, no budget."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(config, "AGENT_MODE", "auto")
    script = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}]}
              for i, (n, a) in enumerate(calls)] + [{"role": "assistant", "content": final}]
    seen = []

    def chat(messages, tools, max_tokens, *, ip="", kind="tools"):
        seen.append([m.get("role") for m in messages])
        return script[min(len(seen) - 1, len(script) - 1)], {"total_tokens": 10}
    monkeypatch.setattr(agent, "_chat", chat)
    return seen


def llm_ask(q, tenant="acme", **kw):
    r = client.post("/api/ask", json={"query": q, "tenant": tenant, "mode": "llm", **kw})
    assert r.status_code == 200, r.text
    return r.json()


NO_TRAVERSAL = "no traversal was run"


@live
def test_llm_spof_fills_panel_and_outcome(monkeypatch):
    seen = fake_llm(monkeypatch, [("single_points_of_failure", {})], "qix has the widest fleet reach.")
    d = llm_ask("which npm account would hurt us most if it got phished?")
    assert seen, "the mocked model was called"
    assert d["mode"] == "llm" and d["intent"] == "single_points_of_failure"
    assert d["outcome"] == "answered_with_path" and d["spof"] and d["spof"]["maintainers"]
    assert NO_TRAVERSAL not in d["message"] and d["llm_summary"] == "qix has the widest fleet reach."
    assert any(s.get("query") == "single_points_of_failure" for s in d["trace"])


@live
def test_llm_who_to_wake_fills_wake(monkeypatch):
    fake_llm(monkeypatch, [("who_to_wake", {"service": "auth-service"})], "Page the auth-service owners.")
    d = llm_ask("someone tell me who owns auth-service and should be paged")
    assert d["intent"] == "who_to_wake" and d["outcome"] == "answered_with_path"
    assert d["wake"][0]["service"] == "auth-service" and d["message"].startswith("Wake 1 team")


@live
def test_llm_path_between_fills_path(monkeypatch):
    fake_llm(monkeypatch, [("path_between", {"service": "img-service", "package": "is-arrayish"})],
             "img-service reaches is-arrayish through sharp.")
    d = llm_ask("how does the image thing end up pulling that arrayish package")
    assert d["intent"] == "path_between" and d["outcome"] == "answered_with_path"
    assert [n.split("@")[0] for n in d["path"]["via"]][:2] == ["img-service", "sharp"] and d["graph"]["nodes"]


@live
def test_llm_exposure_fills_exposed_from_same_rows(monkeypatch):
    fake_llm(monkeypatch, [("exposure", {"package": "debug", "bad_version": "4.4.2", "depth": 4})],
             "Several services can install debug 4.4.2.")
    d = llm_ask("are we in trouble from that debug release last september")
    tool = client.post("/api/tools/exposure", json={"package": "debug", "bad_version": "4.4.2", "depth": 4}).json()
    assert d["outcome"] == "answered_with_path" and d["exposed"] and d["wake"]
    assert {e["service"] for e in d["exposed"]} == set(tool["result"]["exposed_services"])


@live
def test_llm_incident_scope_fills_incident(monkeypatch):
    fake_llm(monkeypatch, [("incident_scope", {"maintainer": "qix"})], "The qix packages reach several services.")
    d = llm_ask("tell me about the big phishing thing")
    assert d["outcome"] == "answered_with_path" and d["exposure" if "exposure" in d else "exposed"]
    assert d["trigger"] == "maintainer qix" and NO_TRAVERSAL not in d["message"]


@live
def test_llm_recall_fills_memory(monkeypatch):
    client.post("/api/fix", json={"query": QIX, "tenant": "acme", "session_id": sid()})
    fake_llm(monkeypatch, [("recall", {"entity": "qix"})], "There is a stored incident about qix.")
    d = llm_ask("what do we remember about that account")
    assert d["intent"] == "recall" and d["outcome"] == "answered_from_memory" and d["memory"]["incidents"]


@live
def test_llm_investigate_keeps_its_response(monkeypatch):
    fake_llm(monkeypatch, [("investigate", {"question": "maintainer qix was phished"})], "Several services are exposed.")
    d = llm_ask("walk me through our exposure please")
    assert d["outcome"] == "answered_with_path" and d["exposed_count"] == 13 and d["intent"] == "exposure"


@live
def test_llm_resolve_only_stays_honest(monkeypatch):
    fake_llm(monkeypatch, [("resolve_entity", {"text": "zzqqxx"})], "Nothing matches that in the graph.")
    d = llm_ask("what about zzqqxx")
    assert d["outcome"] == "unknown_entity" and d["mode"] == "llm"


@live
def test_llm_rejected_sentence_keeps_tool_panels(monkeypatch):
    fake_llm(monkeypatch, [("single_points_of_failure", {})], "Bob Madeup at @nobody owns everything.")
    d = llm_ask("which npm account would hurt us most if it got phished?")
    assert d["llm_summary"] is None and d["llm_rejected"] and d["spof"] and d["outcome"] == "answered_with_path"


# ------------------------------------------------------------------------------------------------ P1-2 wake
@live
def test_wake_followup_leads_with_people():
    s = sid()
    t1 = ask(QIX, "acme", session_id=s)
    t2 = ask("who should I wake for that?", "acme", session_id=s)
    assert t2["followup"]["resolved"] and t2["intent"] == "who_to_wake"
    assert t2["message"].startswith("Wake ") and " Why: " in t2["message"]
    assert {w["service"] for w in t2["wake"]} == {e["service"] for e in t1["exposed"]}
    h = client.get(f"/api/sessions/{s}", params={"tenant": "acme"}).json()["history"]
    assert h[1]["intent"] == "who_to_wake"


@live
def test_explicit_wake_for_version_leads_with_people():
    d = ask("who should I wake for debug 4.4.2?", "acme")
    assert d["intent"] == "who_to_wake" and d["message"].startswith("Wake ") and d["wake"]


@live
def test_wake_for_campaign_not_hit_says_nobody():
    d = ask("who should I wake for Shai-Hulud?", "acme")
    assert d["intent"] == "who_to_wake" and d["message"].startswith("Nobody to wake: ")


# ------------------------------------------------------------------------------------------------ P1-3 incidents
def _incidents(tenant, key):
    return g(tenant).ro_query("MATCH (i:Incident {entity_key:$k}) RETURN i.id, i.depth, i.triage_count, "
                              "size(i.history), i.last_delta", {"k": key}).result_set


@live
def test_one_incident_per_trigger_across_depths_fix_and_crew():
    s = sid()
    f4 = client.post("/api/fix", json={"query": QIX, "tenant": "acme", "depth": 4, "session_id": s}).json()["fix"]
    f2 = client.post("/api/fix", json={"query": QIX, "tenant": "acme", "depth": 2, "session_id": s}).json()["fix"]
    run = crew(QIX, "acme", depth=6, session_id=s)
    rows = _incidents("acme", "qix")
    assert len(rows) == 1, rows
    assert f2["incident_id"] == f4["incident_id"] == run["incident"]["id"] == rows[0][0]
    assert rows[0][1] == 6 and rows[0][3] >= 3
    delta = json.loads(rows[0][4])
    assert delta["previous_depth"] == 2 and delta["depth"] == 6 and delta["source"] == "crew"
    md = findings(run)["memory_delta"]
    assert "depth 2 -> 6" in md["text"]
    n_exp = g("acme").ro_query("MATCH (i:Incident {id:$id})-[:EXPOSED]->(s) RETURN count(s)",
                               {"id": rows[0][0]}).result_set[0][0]
    assert n_exp == 14 == run["incident"].get("exposed_count", 14)


@live
def test_seen_before_never_cites_own_session_or_crew():
    s = sid()
    crew(QIX, "acme", depth=4, session_id=s)
    mine = ask(QIX, "acme", session_id=s)
    assert not ((mine.get("memory") or {}).get("seen_before")), mine["memory"]
    other = ask(QIX, "acme", session_id=sid())
    sb = other["memory"]["seen_before"]
    assert sb["id"] == _incidents("acme", "qix")[0][0]
    client.post("/api/fix", json={"query": QIX, "tenant": "acme", "session_id": s})
    again = ask(QIX, "acme", session_id=s)
    assert not ((again.get("memory") or {}).get("seen_before"))


# ------------------------------------------------------------------------------------------------ P1-5 DB down
class _Broken:
    def __init__(self, exc):
        self.exc = exc

    def select_graph(self, _):
        return self

    def query(self, *a, **k):
        raise self.exc

    ro_query = query

    def list_graphs(self):
        raise self.exc

    @property
    def connection(self):
        return self

    def ping(self):
        raise self.exc


AUTH = redis.exceptions.AuthenticationError("invalid username-password pair or user is disabled.")
SECRET_BITS = ("username-password", "AuthenticationError", "Connection refused", "Error 111", "Traceback")


@pytest.mark.parametrize("exc", [AUTH, redis.exceptions.ConnectionError("Error 111 connecting to x:1. Connection refused.")])
def test_every_route_returns_clean_503_when_db_fails(monkeypatch, exc):
    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setattr(db, "_db", _Broken(exc))
    c = TestClient(app, raise_server_exceptions=False)
    calls = [("get", "/api/fleet", None), ("get", "/api/fleet/graph", None), ("get", "/api/incidents", None),
             ("get", "/api/timeline", None), ("get", "/api/crew", None), ("get", "/api/playbooks", None),
             ("get", "/api/byo/scans", None), ("get", "/api/sessions/test-abc", None),
             ("get", "/api/crew/CREW-20261016-0123abcd", None),
             ("post", "/api/ask", {"query": "qix"}), ("post", "/api/ask", {"query": "qix", "session_id": "session-0001"}),
             ("post", "/api/fix", {"query": QIX}), ("post", "/api/crew/run?stream=false", {"query": QIX}),
             ("post", "/api/tools/exposure", {"package": "debug", "bad_version": "4.4.2"})]
    for method, path, body in calls:
        r = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
        assert r.status_code == 503, (path, r.status_code, r.text)
        j = r.json()
        assert j["error"] == "database_unavailable", (path, j)
        assert not any(b in r.text for b in SECRET_BITS), (path, r.text)
    h = c.get("/health").json()
    assert h["falkordb"] == "down" and not any(b in json.dumps(h) for b in SECRET_BITS)


def test_crew_sse_error_event_is_clean(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setattr(db, "_db", _Broken(AUTH))
    c = TestClient(app, raise_server_exceptions=False)
    with c.stream("POST", "/api/crew/run", json={"query": QIX}, headers={"Accept": "text/event-stream"}) as r:
        body = "".join(r.iter_text())
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        assert "event: error" in body and "database_unavailable" in body
    assert not any(b in body for b in SECRET_BITS), body


def test_generic_500_never_echoes_exception(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    monkeypatch.setattr(main, "_ask", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret-internal-detail")))
    r = TestClient(main.app, raise_server_exceptions=False).post("/api/ask", json={"query": "qix"})
    assert r.status_code == 500 and r.json()["error"] == "internal_error" and "secret" not in r.text


@live
def test_crew_warmup_is_read_only_and_safe(monkeypatch):
    from app.crew.engine import WARM, warm_up
    before = {t: g(t).ro_query("MATCH (n) RETURN count(n)").result_set[0][0] for t in ("acme", "globex")}
    w = warm_up()
    assert w["status"] == "ok" and w["tenants"]["acme"]["exposed"] == 13 and w["tenants"]["globex"]["queries"] > 3
    assert {t: g(t).ro_query("MATCH (n) RETURN count(n)").result_set[0][0] for t in ("acme", "globex")} == before
    assert client.get("/health").json()["crew_warmup"]["status"] == "ok"
    monkeypatch.setattr(db, "_db", _Broken(AUTH))
    assert warm_up()["status"] == "database_unavailable" and WARM["status"] == "database_unavailable"


# ------------------------------------------------------------------------------------------------ P2 sessions
@live
def test_replay_plus_crew_is_one_turn():
    s = sid()
    a = ask(QIX, "acme", session_id=s, depth=4)
    run = crew(QIX, "acme", session_id=s, depth=4)
    assert run["session"]["turn"] == a["session"]["turn"] == 1 and run["session"]["deduped"] is True
    h = client.get(f"/api/sessions/{s}", params={"tenant": "acme"}).json()
    assert h["turns"] == 1 and h["history"][0]["crew_run"] == run["run_id"]
    assert set(h["history"][0]["kinds"]) == {"ask", "crew"}
    b = ask(QIX, "acme", session_id=s, depth=6)  # a different depth is a new question
    assert b["session"]["turn"] == 2 and not b["session"]["deduped"]


# ------------------------------------------------------------------------------------------------ P2 client IP
class _Req:
    def __init__(self, headers, peer="10.0.0.5"):
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.client = type("C", (), {"host": peer})()


def test_client_ip_prefers_railway_header_and_ignores_spoofed_xff():
    spoof = {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "198.51.100.9"}
    assert ratelimit.client_ip(_Req(spoof), header="x-real-ip", hops=0) == "198.51.100.9"
    assert ratelimit.client_ip(_Req(spoof), header="", hops=0) == "10.0.0.5"          # no proxy: socket peer
    assert ratelimit.client_ip(_Req({"X-Real-IP": "not-an-ip"}), header="x-real-ip", hops=0) == "10.0.0.5"
    xff = {"X-Forwarded-For": "6.6.6.6, 203.0.113.4, 10.1.1.1"}
    assert ratelimit.client_ip(_Req(xff), header="", hops=1) == "10.1.1.1"
    assert ratelimit.client_ip(_Req(xff), header="", hops=2) == "203.0.113.4"
    assert ratelimit.client_ip(_Req({"X-Forwarded-For": "6.6.6.6"}), header="", hops=2) == "10.0.0.5"


def test_rate_limiter_with_zero_limit_never_crashes():
    rl = ratelimit.RateLimiter(0, 60)
    assert rl.check("x") > 0


# ------------------------------------------------------------------------------------------------ BYO tenancy
def test_byo_graph_names_are_per_tenant():
    from app import tenants
    from app.byo import scan
    with tenants.use("globex"):
        assert scan.tenant_prefix() == "byo:globex:"
    assert scan.tenant_prefix("acme") == "byo:acme:"
    name = "byo:globex:testscope0001"
    db.client().select_graph(name).query("CREATE (:ScanMeta {x:1})")
    try:
        assert name in scan.list_byo_graphs("globex") and name not in scan.list_byo_graphs("acme")
        assert name in scan.list_byo_graphs(all_tenants=True)
        acme = client.get("/api/byo/scans", params={"tenant": "acme"}).json()["scans"]
        assert all(s["graph"].startswith("byo:acme:") for s in acme)
    finally:
        db.client().select_graph(name).delete()


# ------------------------------------------------------------------------------------------------ pre-run reuse
@live
def test_llm_investigate_after_prerun_is_reused_not_repeated(monkeypatch):
    from app.mcp_agent.domain import McpDomain, McpRegistry
    counts = {"route": 0, "investigate": 0}
    real_route, real_call = McpDomain.route, McpRegistry.call

    def route(self, *a, **k):
        counts["route"] += 1
        return real_route(self, *a, **k)

    def call(self, name, *a, **k):
        counts[name] = counts.get(name, 0) + 1
        return real_call(self, name, *a, **k)
    monkeypatch.setattr(McpDomain, "route", route)
    monkeypatch.setattr(McpRegistry, "call", call)
    seen = []
    script = [{"role": "assistant", "content": None, "tool_calls": [{"id": "c0", "type": "function", "function": {
        "name": "investigate", "arguments": json.dumps({"question": QIX})}}]},
              {"role": "assistant", "content": "13 services are exposed."}]
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(config, "AGENT_MODE", "auto")
    monkeypatch.setattr(agent, "_chat", lambda m, t, n, **k: (seen.append(json.loads(json.dumps(m))) or
                                                              script[len(seen) - 1], {"total_tokens": 7}))
    d = llm_ask(QIX)
    assert counts["route"] == 1 and counts["investigate"] == 0, counts  # one traversal in total
    assert d["outcome"] == "answered_with_path" and d["exposed_count"] == 13 and d["llm_tokens"] == 14
    assert any(s.get("reused") for s in d["trace"])
    pointer = seen[1][-1]
    assert pointer["role"] == "tool" and "same_as_tool_call" in pointer["content"] and len(pointer["content"]) < 120
    # A different question still runs investigate for real.
    script[0]["tool_calls"][0]["function"]["arguments"] = json.dumps({"question": "debug@4.4.2"})
    seen.clear()
    llm_ask("maintainer qix was phished, what now")
    assert counts["investigate"] == 1

"""Integration tests against the live graph (fleet:acme). Skipped if FalkorDB or the graph is missing.

Maps to SPEC section 9 acceptance criteria. Incidents created here are deleted afterwards.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app

ping = db.ping()
pytestmark = pytest.mark.skipif(not (ping["ok"] and ping.get("graph_loaded")),
                                reason=f"FalkorDB graph {config.GRAPH_NAME} not available")
client = TestClient(app)
QIX = "maintainer qix was phished"


def ask(q, **kw):
    r = client.post("/api/ask", json={"query": q, "fresh": True, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def tools_run(d):
    """Tools that actually ran Cypher (agent/LLM notes excluded)."""
    return [s["tool"] for s in d["trace"] if s.get("cypher")]


@pytest.fixture
def cleanup():
    ids = []
    yield ids
    g = db.graph()
    for i in ids:
        g.query("MATCH (i:Incident {id:$id}) OPTIONAL MATCH (f:Finding {incident:$id}) DETACH DELETE i, f", {"id": i})


def test_health():
    d = client.get("/health").json()
    assert d["falkordb"] == "up" and d["graph_loaded"]


def test_ac4_qix_answered_with_path():
    d = ask(QIX)
    assert d["outcome"] == "answered_with_path"
    assert len(d["exposed"]) >= 5
    for e in d["exposed"]:
        f = next(f for f in e["findings"] if f["exposed"])
        assert f["path"]["via"][0] == e["service"]
        assert f["path"]["via"][-1].split("@")[0] == f["package"] or f["path"]["via"][-1].startswith(f["package"] + "@")
        assert f["path"]["requirement"]
    for t in ("resolve_entity", "incident_scope", "exposure", "who_to_wake"):
        assert t in tools_run(d)
    assert all("ms" in s and "rows" in s for s in d["trace"])


def test_ac5_depth_contrast():
    d1, d4 = ask(QIX, depth=1), ask(QIX, depth=4)
    assert len(d4["exposed"]) > len(d1["exposed"])
    names1 = {e["service"] for e in d1["exposed"]}
    names4 = {e["service"] for e in d4["exposed"]}
    assert "img-service" not in names1 and "img-service" in names4


def test_ac6_debug_honest_negative():
    d = ask("debug@4.4.2")
    exposed = {e["service"] for e in d["exposed"]}
    assert "checkout-api" not in exposed and "lint-service" in exposed
    safe = {e["service"]: e for e in d["safe"]}
    assert safe["checkout-api"]["findings"][0]["path"]["reason"] == "debug 2.6.9 (exact) does not satisfy 4.4.2"


@pytest.mark.parametrize("q", ["left-pad@1.3.0", "GHSA-xxxx-xxxx-xxxx", "is zzz-not-a-package-qq@9.9.9 affected?"])
def test_ac7_unknown_entity(q):
    d = ask(q)
    assert d["outcome"] == "unknown_entity"
    assert set(tools_run(d)) == {"resolve_entity"}
    assert d["exposed"] == [] and d["safe"] == []


def test_ac8_not_in_graph():
    d = ask("ansi-regex@6.2.1")
    assert d["outcome"] == "not_in_graph"
    assert "No fleet path admits this version at depth 4" in d["message"]


def test_ac9_fix_and_writeback(cleanup):
    r = client.post("/api/fix", json={"query": QIX, "session": "pytest"})
    assert r.status_code == 200, r.text
    d = r.json()
    fix = d["fix"]
    cleanup.append(fix["incident_id"])
    assert len(fix["per_service"]) == len(d["exposed"])
    for p in fix["per_service"]:
        o = json.loads(p["overrides_json"])["overrides"]
        assert o
        assert p["team"] and p["oncall"] and "path:" in p["draft"] and "MAL-" in p["draft"]
    comb = json.loads(fix["combined_json"])["overrides"]
    assert comb["debug@^4"] == "4.4.3" and "debug" not in comb  # scoped to the compromised major (P1-2)
    ship = next(p for p in fix["per_service"] if p["service"] == "shipping-tracker")
    assert ship["package_json"]["dependencies"] == {"debug": "^4.4.3"}  # direct dep bumped, not overridden
    assert ship["package_json"]["overrides"]["debug@^4"] == "^4.4.3"
    assert fix["pins"]["debug"] == "4.4.3"
    chk = client.get(f"/api/incidents/{fix['incident_id']}/check").json()
    assert chk["exposed_edges"] == len(d["exposed"])
    # AC10 (memory part): a new session recalls it before traversing.
    m = client.post("/api/ask", json={"query": "qix", "session": "other"}).json()
    assert m["outcome"] == "answered_from_memory"
    assert "exposure" not in tools_run(m)
    assert m["memory"]["incidents"][0]["exposed_count"] == len(d["exposed"])


def test_ac11_spof():
    d = ask("who is our single point of failure?")
    ms = d["spof"]["maintainers"]
    assert len(ms) >= 5
    assert {"qix", "sindresorhus"} & {m["maintainer"] for m in ms[:3]}
    assert any("algo.pageRank" in (s["cypher"] or "") for s in d["trace"])


def test_ac12_path_between():
    d = ask("why is img-service connected to is-arrayish?")
    assert d["outcome"] == "answered_with_path"
    pkgs = [n.split("@")[0] for n in d["path"]["via"]]
    assert pkgs == ["img-service", "sharp", "color", "color-string", "simple-swizzle", "is-arrayish"]
    assert d["graph"]["nodes"]


def test_ac13_tool_endpoint_matches_ask():
    t = client.post("/api/tools/exposure", json={"package": "debug", "bad_version": "4.4.2", "depth": 4}).json()
    d = ask("debug@4.4.2")
    assert sorted(t["result"]["exposed_services"]) == sorted(e["service"] for e in d["exposed"])


def test_ac15_exposure_fast():
    d = ask(QIX, depth=4)
    ms = next(s["ms"] for s in d["trace"] if s["tool"] == "exposure")
    assert ms < 500


def test_who_to_wake_and_owner():
    d = ask("who do I wake for checkout-api")
    assert d["wake"][0]["team"] == "payments" and d["wake"][0]["oncall"]
    d = ask("who owns debug")
    assert "qix" in d["maintainers"]["maintainers"]


def test_bare_packages():
    d = ask("chalk and debug were compromised, are we exposed?")
    assert d["outcome"] == "answered_with_path"
    assert {s["package"] for s in d["scope"]} == {"chalk", "debug"}


def test_validation_errors():
    assert client.post("/api/ask", json={"query": ""}).status_code == 422
    assert client.post("/api/ask", json={"query": "qix", "depth": 9}).status_code == 422
    assert client.post("/api/tools/nope", json={}).status_code == 404
    assert client.post("/api/tools/exposure", json={"package": "debug"}).status_code == 400


def test_timeline_events_from_graph():
    r = client.get("/api/timeline")
    assert r.status_code == 200
    ev = r.json()["events"]
    kinds = {e["kind"] for e in ev}
    # Advisories tagged with a campaign (qix: 19, Shai-Hulud: 203) collapse into one 'campaign' event each; only
    # untagged advisories appear one by one. Every kind is one the UI renders (frontend TimelineEvent).
    assert kinds <= {"compromise", "advisory", "campaign", "incident", "playbook", "watch", "watch_replay"}
    assert not any(e["title"] == "osv-watch" for e in ev)  # live-watch advisories are watch events, not a campaign
    assert {"compromise", "campaign"} <= kinds
    qix = next(e for e in ev if e["kind"] == "campaign" and e["title"] == "2025-09-08-qix")
    assert qix["count"] == 19 and str(qix["at"]).startswith("2025-09-08") and len(qix["items"]) <= 12
    assert not any(e["kind"] == "advisory" and e["items"][0] in qix["items"] for e in ev)
    assert all(isinstance(e["title"], str) and isinstance(e["count"], int) and isinstance(e["items"], list)
               for e in ev)
    comp = next(e for e in ev if e["kind"] == "compromise")
    assert comp["title"] == "qix" and comp["count"] == 19 and str(comp["at"]).startswith("2025-09-08")
    assert [str(e["at"]) for e in ev] == sorted(str(e["at"]) for e in ev)


def test_health_reports_llm_budget():
    b = client.get("/health").json()["llm_budget"]
    assert b["daily_cap"] >= 1 and b["per_ip_per_min"] >= 1 and "calls" in b


def test_campaign_not_loaded_is_honest():
    # The default test graph loads only the qix incident; Shai-Hulud must say so, not guess.
    d = ask("Were we hit by the Shai-Hulud worm?")
    assert d["outcome"] in ("not_in_graph", "answered_with_path")
    assert d["trigger"] == "campaign Shai-Hulud"
    if not d["scope"]:
        assert "INCIDENTS=qix,shai-hulud" in d["message"]
    else:
        assert len(d["message"]) < 600

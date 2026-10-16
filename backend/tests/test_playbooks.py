"""Approve-then-remember (procedural memory). Needs FalkorDB with fleet:acme; creates and then deletes its own
incidents and playbooks only."""
import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

ping = db.ping()
pytestmark = pytest.mark.skipif(not (ping["ok"] and ping.get("graph_loaded")), reason="FalkorDB fleet:acme not available")
client = TestClient(app)


@pytest.fixture
def created():
    ids = {"incidents": [], "playbooks": []}
    yield ids
    g = db.graph()
    g.query("MATCH (pb:Playbook) WHERE pb.id IN $ids DETACH DELETE pb", {"ids": ids["playbooks"]})
    g.query("MATCH (i:Incident) WHERE i.id IN $ids OPTIONAL MATCH (i)-[:HAS_FINDING]->(f) DETACH DELETE f, i",
            {"ids": ids["incidents"]})


def fix(q, created):
    r = client.post("/api/fix", json={"query": q, "depth": 4})
    assert r.status_code == 200, r.text
    f = r.json()["fix"]
    created["incidents"].append(f["incident_id"])
    return f


def test_approve_then_reuse(created):
    f1 = fix("maintainer qix was phished", created)
    r = client.post(f"/api/incidents/{f1['incident_id']}/approve", json={"approved_by": "atharv"})
    assert r.status_code == 200, r.text
    body = r.json()
    pb = body["playbook"]
    created["playbooks"].append(pb["id"])
    assert body["created"] is True
    assert pb["overrides"]["debug"] == "4.4.3" and pb["approved_by"] == "atharv"
    assert pb["trigger_kind"] == "maintainer_compromise" and pb["steps"]
    assert pb["incident_id"] == f1["incident_id"]
    # idempotent
    again = client.post(f"/api/incidents/{f1['incident_id']}/approve").json()
    assert again["created"] is False and again["playbook"]["id"] == pb["id"]
    # listed, linked to packages and advisories by graph edges
    listed = {p["id"]: p for p in client.get("/api/playbooks").json()["playbooks"]}
    assert "debug" in listed[pb["id"]]["packages"] and "MAL-2025-46974" in listed[pb["id"]]["advisories"]
    # a later incident touching debug reuses it
    f2 = fix("debug@4.4.2", created)
    msgs = [p for p in f2["playbooks"] if p["id"] == pb["id"]]
    assert msgs and msgs[0]["agrees_with_proposed_fix"] is True
    assert "Applying approved playbook" in msgs[0]["message"]
    reused = {p["id"]: p for p in client.get("/api/playbooks").json()["playbooks"]}[pb["id"]]["reused_by"]
    assert f2["incident_id"] in reused
    # recall exposes it
    rec = client.post("/api/ask", json={"query": "qix", "session": "pb-test"}).json()
    assert pb["id"] in json.dumps(rec)
    m = client.get("/api/playbooks/match", params={"packages": "debug,left-pad"}).json()
    assert any(p["id"] == pb["id"] for p in m["playbooks"])


def test_approve_unknown_incident_404():
    r = client.post("/api/incidents/INC-does-not-exist/approve")
    assert r.status_code == 404 and r.json()["error"] == "incident_not_found"
    assert client.post("/api/incidents/bad%20id!/approve").status_code == 400

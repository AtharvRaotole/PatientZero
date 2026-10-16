"""P0-1 regression: after a Generate fix writes an Incident, incident queries must still traverse fresh.

Memory answers on its own only for a bare recall of the exact stored trigger at the stored depth (AC10); every
other incident question gets a fresh traversal with the remembered incident attached as `memory.seen_before`.
Creates one Incident and deletes it afterwards.
"""
import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app

ping = db.ping()
pytestmark = pytest.mark.skipif(not (ping["ok"] and ping.get("graph_loaded")),
                                reason=f"FalkorDB graph {config.GRAPH_NAME} not available")
client = TestClient(app)


@pytest.fixture(scope="module")
def incident():
    r = client.post("/api/fix", json={"query": "maintainer qix was phished", "session": "pytest-p0"})
    assert r.status_code == 200, r.text
    inc = r.json()["fix"]["incident_id"]
    yield inc
    db.graph().query("MATCH (i:Incident {id:$id}) OPTIONAL MATCH (i)-[:HAS_FINDING]->(f) "
                     "OPTIONAL MATCH (f2:Finding {incident:$id}) DETACH DELETE f, f2, i", {"id": inc})


def ask(q, **kw):
    r = client.post("/api/ask", json={"query": q, "session": "new", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_debug_442_is_its_own_answer_after_fix(incident):
    d = ask("debug@4.4.2")
    assert d["outcome"] == "answered_with_path"
    assert d["exposed_count"] == 10 and len(d["exposed"]) == 10
    safe = {s["service"]: s for s in d["safe"]}
    assert "checkout-api" in safe  # honest negative: express pins debug 2.6.9
    assert "lint-service" in {e["service"] for e in d["exposed"]}
    sb = d["memory"]["seen_before"]
    assert sb["id"] == incident and sb["same_trigger"] is False
    assert sb["text"].startswith("Seen before: " + incident)


@pytest.mark.parametrize("q", ["maintainer qix was phished", "chalk@5.6.1", "GHSA-4x49-vf9v-38px",
                               "the guy who maintains chalk got phished"])
def test_incident_queries_traverse_after_fix(incident, q):
    d = ask(q)
    assert d["outcome"] in ("answered_with_path", "not_in_graph"), d["message"]
    assert d["outcome"] != "answered_from_memory"
    assert any(s["tool"] == "exposure" and s.get("cypher") for s in d["trace"])
    assert d["memory"] and d["memory"]["seen_before"]["id"] == incident


def test_qix_phished_has_paths_and_diff(incident):
    d = ask("maintainer qix was phished")
    assert d["exposed_count"] == 13 and all(e["findings"][0]["path"]["via"] for e in d["exposed"])
    sb = d["memory"]["seen_before"]
    assert sb["same_trigger"] and sb["new_since"] == [] and sb["no_longer_exposed"] == []


def test_explicit_depth_is_honoured_after_fix(incident):
    assert ask("qix", depth=1)["exposed_count"] == 1
    assert ask("maintainer qix was phished", depth=2)["exposed_count"] == 7
    d = ask("qix", depth=6)
    assert d["outcome"] == "answered_with_path" and d["depth"] == 6 and d["exposed_count"] == 14


def test_bare_recall_still_answers_from_memory(incident):
    """AC10: a bare `qix` at the stored depth answers from memory, with the date and a fresh-traversal hint."""
    d = ask("qix")
    assert d["outcome"] == "answered_from_memory"
    assert d["exposed_count"] == 13 and "fresh traversal" in d["message"]
    assert d["memory"]["incidents"][0]["id"] == incident
    assert ask("qix", fresh=True)["outcome"] == "answered_with_path"

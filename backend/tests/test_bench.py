"""Concurrency benchmark: a tiny real run (mixed reads and writes), cleanup of the scratch graph, the API contract."""
import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.bench import runner

try:
    LIVE = config.GRAPH_NAME in db.client().list_graphs()
except Exception:  # noqa: BLE001
    LIVE = False
live = pytest.mark.skipif(not LIVE, reason="FalkorDB with fleet:acme not running")


def test_percentiles():
    xs = list(range(1, 101))
    assert runner._pct(xs, 50) in (50, 51) and runner._pct(xs, 99) == 99 and runner._pct([], 50) is None
    s = runner._stats([10.0, 20.0, 30.0], 1, 2.0)
    assert s["ops"] == 3 and s["errors"] == 1 and s["throughput_ops_s"] == 1.5 and s["max_ms"] == 30.0


@live
def test_small_run_reads_writes_and_cleans_up():
    r = runner.run(clients=4, duration_s=1.5, label="test", seed=1)
    assert r["overall"]["ops"] > 0 and r["overall"]["errors"] == 0
    assert r["reads"]["ops"] > 0 and r["writes"]["ops"] > 0
    assert r["events_written"] == r["by_op"]["write_event"]["ops"]  # every timed write landed
    assert r["scratch_deleted"] is True and r["scratch_graph"] not in db.client().list_graphs()
    assert r["overall"]["p50_ms"] <= r["overall"]["p95_ms"] <= r["overall"]["p99_ms"] <= r["overall"]["max_ms"]


@live
def test_multiprocess_run():
    r = runner.run(clients=4, duration_s=1.0, procs=2, label="test", seed=2)
    assert r["procs"] == 2 and r["overall"]["ops"] > 0 and r["overall"]["errors"] == 0 and r["scratch_deleted"]


def test_bench_run_requires_admin(monkeypatch):
    from app.main import app
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secret-test-token")
    c = TestClient(app)
    assert c.post("/api/bench/run", json={"clients": 2, "duration_s": 1}).status_code == 401
    assert c.post("/api/bench/run", json={"clients": 500, "duration_s": 1},
                  headers={"X-Admin-Token": "secret-test-token"}).status_code == 422  # bounded


@live
def test_bench_last_is_public_and_hides_host():
    from app.main import app
    c = TestClient(app)
    j = c.get("/api/bench/last").json()
    assert "last" in j and "history" in j
    if j["last"]:
        assert "host" not in j["last"]


def test_bench_last_states_what_the_number_measures(monkeypatch):
    """The in-process ops/s never travels without its scope and the end-to-end HTTP number."""
    from app.bench import api as bench_api
    from app.main import app
    fake = {"id": "B-x", "label": "server", "clients": 30, "procs": 2, "retry_busy": True, "read_graph": "fleet:acme",
            "scratch_graph": "bench:B-x", "mix": {"read_exposure_incident": 40, "write_event": 20, "read_x": 40},
            "overall": {"throughput_ops_s": 740.6}, "host": "secret-host"}
    monkeypatch.setattr(bench_api.runner, "last", lambda: {"last": fake, "history": []})
    j = TestClient(app).get("/api/bench/last").json()
    sc = j["last"]["scope"]
    assert sc["kind"] == "in_process_db" and sc["retry_busy"] is True and "host" not in j["last"]
    assert "740.6 ops/s is measured in process" in sc["summary"] and "scratch graph bench:B-x" in sc["summary"]
    assert "retry on FalkorDB queue-full" in sc["summary"] and "49.4 req/s at 30 clients" in sc["summary"]
    assert j["numbers"] == {"in_process_ops_s": 740.6, "in_process_clients": 30, "http_req_s": 49.4,
                            "http_clients": 30}
    assert j["http"]["errors"] == 0 and j["http"]["requests"] == 1009
    assert "\u2014" not in sc["summary"]

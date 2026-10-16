"""Live OSV watch: index parsing, normalisation, bounded polls, verdicts, replay and the HTTP contract.

Network is mocked for polls (fake OSV index + records in the real OSV shape); graph work runs against a tiny
throwaway graph, so the dev graph and the real watermark are never touched. Needs FalkorDB.
"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.watch import core, osv

try:
    db.client().ping() if hasattr(db.client(), "ping") else db.client().connection.ping()
    LIVE = True
except Exception:  # noqa: BLE001
    LIVE = False


def rec(mid, pkg, versions=None, intro_zero=False, withdrawn=None, aliases=()):
    af = {"package": {"name": pkg, "ecosystem": "npm"}}
    if versions:
        af["versions"] = versions
    if intro_zero:
        af["ranges"] = [{"type": "SEMVER", "events": [{"introduced": "0"}]}]
    r = {"id": mid, "summary": f"Malicious code in {pkg} (npm)", "aliases": list(aliases),
         "published": "2026-10-16T10:00:00Z", "modified": "2026-10-16T10:05:00Z", "affected": [af]}
    if withdrawn:
        r["withdrawn"] = withdrawn
    return r


def test_normalize_versions_all_versions_and_withdrawn():
    a = osv.normalize(rec("MAL-2026-1", "evil", versions=["1.0.1", "1.0.0"]))[0]
    assert a.bad_versions == ["1.0.0", "1.0.1"] and not a.all_versions
    b = osv.normalize(rec("MAL-2026-2", "evil2", intro_zero=True))[0]
    assert b.all_versions and b.row("t")["bad_version"] == "*"
    assert osv.normalize(rec("MAL-2026-3", "x", versions=["1.0.0"], withdrawn="2026-10-16T00:00:00Z")) == []
    assert osv.normalize({"id": "GHSA-xxxx", "affected": []}) == []  # only MAL ids
    pypi = rec("MAL-2026-4", "p", versions=["1"])
    pypi["affected"][0]["package"]["ecosystem"] = "PyPI"
    assert osv.normalize(pypi) == []


def test_index_head_parsing_drops_partial_line_and_non_mal(monkeypatch):
    body = (b"2026-10-16T13:30:04Z,MAL-2026-17901\n2026-10-16T09:56:02Z,GHSA-cx2f-j9fh-8g68\n"
            b"2026-10-16T07:30:04Z,MAL-2026-17896\n2026-09-24T09:00:44Z,MA")
    monkeypatch.setattr(osv, "_get_capped", lambda url, cap, headers=None, timeout=None: (206, body, {"etag": '"e1"'}))
    h = osv.fetch_index_head(nbytes=len(body))
    assert h.entries == [("2026-10-16T13:30:04Z", "MAL-2026-17901"), ("2026-10-16T07:30:04Z", "MAL-2026-17896")]
    assert h.etag == '"e1"'
    monkeypatch.setattr(osv, "_get_capped", lambda url, cap, headers=None, timeout=None: (304, b"", {}))
    assert osv.fetch_index_head(etag='"e1"').unchanged


def test_record_fetch_rejects_bad_ids_and_oversize(monkeypatch):
    with pytest.raises(osv.FetchError):
        osv.fetch_record("../../etc/passwd")
    monkeypatch.setattr(osv, "RECORD_MAX_BYTES", 10)
    monkeypatch.setattr(osv, "_get_capped", lambda url, cap, headers=None, timeout=None: (200, b"x" * 11, {}))
    with pytest.raises(osv.FetchError):
        osv.fetch_record("MAL-2026-1")


def test_capped_get_really_caps():
    import httpx

    def handler(request):
        return httpx.Response(200, content=b"a" * 5000)
    real = httpx.Client

    class C(real):
        def __init__(self, **kw):
            super().__init__(transport=httpx.MockTransport(handler), **kw)
    import app.watch.osv as m
    old = m.httpx.Client
    m.httpx.Client = C
    try:
        status, body, _ = osv._get_capped("https://example.test/x", 100)
    finally:
        m.httpx.Client = old
    assert status == 200 and len(body) <= 101


# ----------------------------------------------------------------------------- graph-backed
pytestmark_live = pytest.mark.skipif(not LIVE, reason="FalkorDB not running")


@pytest.fixture
def tiny_graph():
    name = f"watchtest:{uuid.uuid4().hex[:8]}"
    g = db.client().select_graph(name)
    g.query("""CREATE (t:Team {name:'core', display:'Core', slack:'#core', pager:'pd-core'}),
                      (:Person {name:'Ann', handle:'@ann', email:'ann@example.com', role:'eng'})-[:ONCALL_FOR]->(t),
                      (a:Service {name:'svc-a', tier:1}), (b:Service {name:'svc-b', tier:2}), (c:Service {name:'svc-c', tier:3}),
                      (t)-[:OWNS]->(a), (t)-[:OWNS]->(b), (t)-[:OWNS]->(c),
                      (d1:Version {key:'debug@4.4.1', name:'debug@4.4.1', pkg:'debug', version:'4.4.1', unpublished:false, malicious:false}),
                      (d0:Version {key:'debug@4.3.0', name:'debug@4.3.0', pkg:'debug', version:'4.3.0', unpublished:false, malicious:false}),
                      (e:Version {key:'evil-all@2.0.0', name:'evil-all@2.0.0', pkg:'evil-all', version:'2.0.0', unpublished:false, malicious:false}),
                      (a)-[:DEPENDS_ON {requirement:'^4.4.0'}]->(d1),
                      (b)-[:DEPENDS_ON {requirement:'4.3.0'}]->(d0),
                      (c)-[:DEPENDS_ON {requirement:'^2.0.0'}]->(e)""")
    yield {"t1": name}
    g.delete()


def fake_osv(monkeypatch, entries, records, etag='"v1"'):
    calls = {"index": 0, "records": []}

    def head(etag=None, nbytes=0):
        calls["index"] += 1
        return osv.IndexHead(206, entries, len(entries), 1234, '"v1"')

    def fetch(ids, deadline_s=60):
        calls["records"].append(list(ids))
        return {i: records[i] for i in ids if i in records}, {i: "HTTP 500" for i in ids if i not in records}
    monkeypatch.setattr(osv, "fetch_index_head", head)
    monkeypatch.setattr(osv, "fetch_records", fetch)
    return calls


@pytestmark_live
def test_poll_ingests_checks_and_records_verdicts(monkeypatch, tiny_graph):
    key = f"patientzero:watch:test:{uuid.uuid4().hex[:6]}"
    entries = [("2026-10-16T12:00:00Z", "MAL-2026-90003"), ("2026-10-16T11:00:00Z", "MAL-2026-90002"),
               ("2026-10-16T10:00:00Z", "MAL-2026-90001")]
    records = {"MAL-2026-90001": rec("MAL-2026-90001", "debug", versions=["4.4.2"], aliases=["GHSA-test-0001"]),
               "MAL-2026-90002": rec("MAL-2026-90002", "evil-all", intro_zero=True),
               "MAL-2026-90003": rec("MAL-2026-90003", "nobody-uses-this", versions=["1.0.0"])}
    fake_osv(monkeypatch, entries, records)
    try:
        s = core.poll(source="test", graphs=tiny_graph, state_key=key)
        assert s["new_advisories"] == 3 and s["checked"] == 3 and s["errors"] == 0 and s["hits"] == 2
        assert s["watermark"] == "2026-10-16T12:00:00Z" and s["ms"] > 0
        f = core.feed("t1", graphs=tiny_graph)
        by = {e["advisory"]: e for e in f["events"]}
        assert by["MAL-2026-90001"]["verdict"] == "hit" and by["MAL-2026-90001"]["exposed_services"] == ["svc-a"]
        assert by["MAL-2026-90001"]["reachable_services"] == ["svc-a", "svc-b"]  # svc-b pins 4.3.0: not admitted
        assert by["MAL-2026-90002"]["verdict"] == "hit" and by["MAL-2026-90002"]["all_versions"] is True
        assert by["MAL-2026-90003"]["verdict"] == "not_hit"
        assert all(e["replay"] is False for e in f["events"])
        assert f["last_poll"]["checked"] == 3
        g = db.client().select_graph(tiny_graph["t1"])
        n = g.ro_query("MATCH (a:Advisory {campaign:'osv-watch'}) RETURN count(a)").result_set[0][0]
        assert n == 3
        hits = g.ro_query("MATCH (:WatchEvent)-[:HITS]->(s:Service) RETURN collect(DISTINCT s.name)").result_set[0][0]
        assert sorted(hits) == ["svc-a", "svc-c"]
        mal = g.ro_query("MATCH (v:Version {key:'debug@4.4.2'}) RETURN v.malicious, v.unpublished").result_set[0]
        assert mal == [True, True]
        # Second poll with the same index: nothing new, nothing re-fetched.
        s2 = core.poll(source="test", graphs=tiny_graph, state_key=key)
        assert s2["checked"] == 0 and "no new" in s2["note"]
    finally:
        db.client().connection.delete(key)


@pytestmark_live
def test_poll_is_bounded_and_failed_fetches_are_retried(monkeypatch, tiny_graph):
    key = f"patientzero:watch:test:{uuid.uuid4().hex[:6]}"
    core.save_state(key, {"watermark": "2026-10-01T00:00:00Z"})
    entries = [(f"2026-10-16T10:{i:02d}:00Z", f"MAL-2026-8{i:04d}") for i in range(30, 0, -1)]
    records = {i: rec(i, f"pkg-{i.lower()}", versions=["1.0.0"]) for _, i in entries if i != "MAL-2026-80005"}
    calls = fake_osv(monkeypatch, entries, records)
    try:
        s = core.poll(max_batch=10, source="test", graphs=tiny_graph, state_key=key)
        assert len(calls["records"][0]) == 10  # bounded batch, oldest first
        assert s["errors"] == 1 and s["checked"] == 9
        assert s["watermark"] == "2026-10-16T10:04:00Z"  # stops before the failed record, so it is retried
        assert s["remaining"] == 20
    finally:
        db.client().connection.delete(key)


@pytestmark_live
def test_replay_is_labelled_and_hits_debug_in_acme():
    if config.GRAPH_NAME not in db.client().list_graphs():
        pytest.skip("fleet:acme not loaded")
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/watch/replay", json={"advisory": "debug@4.4.2"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["replay"] is True and j["advisory"] == "MAL-2025-46974" and "REPLAY" in j["note"]
    acme = next(e for e in j["events"] if e["tenant"] == "acme")
    assert acme["verdict"] == "hit" and acme["replay"] is True and acme["exposed_count"] >= 10
    assert acme["paths"] and acme["paths"][0]["via"][0] == acme["paths"][0]["service"]
    f = c.get("/api/watch/feed", params={"tenant": "acme", "limit": 5}).json()
    assert any(e["advisory"] == "MAL-2025-46974" and e["replay"] for e in f["events"])
    assert c.post("/api/watch/replay", json={"advisory": "nope@0.0.0"}).status_code == 400


def test_poll_endpoint_requires_admin_token(monkeypatch):
    from app.main import app
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secret-test-token")
    c = TestClient(app)
    assert c.post("/api/watch/poll").status_code == 401
    assert c.post("/api/watch/poll", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_replay_rate_limited(monkeypatch):
    from app.main import app
    from app.watch import api
    monkeypatch.setattr(api.REPLAY_LIMIT, "limit", 0)
    c = TestClient(app)
    r = c.post("/api/watch/replay", json={"advisory": "debug@4.4.2"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_unknown_tenant_is_400():
    from app.main import app
    c = TestClient(app)
    assert c.get("/api/watch/feed", params={"tenant": "nope"}).status_code == 400


@pytestmark_live
def test_stream_over_real_server_sends_hello_and_poll_events():
    """SSE through a real uvicorn process (TestClient buffers streams). A replay must arrive as `event: watch`."""
    import os
    import socket
    import subprocess
    import sys
    import time

    import httpx
    if config.GRAPH_NAME not in db.client().list_graphs():
        pytest.skip("fleet:acme not loaded")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, WATCH_INTERVAL_S="0", AGENT_MODE="deterministic", OPENAI_API_KEY="", ADMIN_TOKEN="")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        got = []
        with httpx.stream("GET", base + "/api/watch/stream", params={"tenant": "acme"}, timeout=15) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            fired = False
            t0 = time.time()
            for line in r.iter_lines():
                got.append(line)
                if line.startswith("event: hello") and not fired:
                    fired = True
                    assert httpx.post(base + "/api/watch/replay", json={"advisory": "chalk@5.6.1"}, timeout=10).status_code == 200
                if "MAL-2025-46969" in line or time.time() - t0 > 12:
                    break
        text = "\n".join(got)
        assert "event: hello" in text and "event: watch" in text and '"replay": true' in text
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytestmark_live
def test_watcher_covers_every_tenant_graph():
    tenants = pytest.importorskip("app.tenants")
    have = set(db.client().list_graphs())
    want = {t for t, g in tenants.TENANTS.items() if g in have}
    assert set(core.tenant_graphs()) == want
    if {"acme", "globex"} <= set(tenants.TENANTS) and {"fleet:acme", "fleet:globex"} <= have:
        assert core.tenant_graphs() == {"acme": "fleet:acme", "globex": "fleet:globex"} | {
            t: g for t, g in core.tenant_graphs().items() if t not in ("acme", "globex")}


@pytestmark_live
def test_timeline_shows_watch_polls_not_a_fake_campaign(monkeypatch, tiny_graph):
    key = f"patientzero:watch:test:{uuid.uuid4().hex[:6]}"
    entries = [("2026-10-16T10:00:00Z", "MAL-2026-90011"), ("2026-10-16T10:01:00Z", "MAL-2026-90012")]
    records = {"MAL-2026-90011": rec("MAL-2026-90011", "debug", versions=["4.4.2"]),
               "MAL-2026-90012": rec("MAL-2026-90012", "nobody-uses-this", versions=["1.0.0"])}
    fake_osv(monkeypatch, entries, records)
    try:
        core.poll(source="test", graphs=tiny_graph, state_key=key)
    finally:
        db.client().connection.delete(key)
    rows = core.GraphRun(tiny_graph["t1"]).run("timeline_events")
    assert not any(r["kind"] == "campaign" and r["title"] == "osv-watch" for r in rows)
    w = [r for r in rows if r["kind"] == "watch"]
    assert len(w) == 1 and w[0]["count"] == 2 and w[0]["title"].startswith("hit: 1 of 2")
    assert "debug (MAL-2026-90011): hit" in w[0]["items"] and "nobody-uses-this (MAL-2026-90012): not hit" in w[0]["items"]


@pytestmark_live
def test_ask_about_all_versions_malware(monkeypatch, tiny_graph):
    """'Every published version is malicious': clear answer whether or not a fleet service depends on it."""
    tenants = pytest.importorskip("app.tenants")
    key = f"patientzero:watch:test:{uuid.uuid4().hex[:6]}"
    entries = [("2026-10-16T10:00:00Z", "MAL-2026-90021"), ("2026-10-16T10:01:00Z", "MAL-2026-90022")]
    records = {"MAL-2026-90021": rec("MAL-2026-90021", "evil-all", intro_zero=True),
               "MAL-2026-90022": rec("MAL-2026-90022", "lonely-evil", intro_zero=True)}
    fake_osv(monkeypatch, entries, records)
    try:
        core.poll(source="test", graphs=tiny_graph, state_key=key)
    finally:
        db.client().connection.delete(key)
    monkeypatch.setitem(tenants.TENANTS, "wtest", tiny_graph["t1"])
    from app.router import ask
    with tenants.use("wtest"):
        hit = ask("evil-all", depth=4)
        by_id = ask("MAL-2026-90021", depth=4)
        miss = ask("lonely-evil", depth=4)
    assert hit["outcome"] == "answered_with_path" and [e["service"] for e in hit["exposed"]] == ["svc-c"]
    assert hit["message"].startswith("Every published version of evil-all is malicious (MAL-2026-90021, OSV)")
    assert "1 of 3 services" in hit["message"] and hit["wake"]
    assert by_id["outcome"] == "answered_with_path"
    assert miss["outcome"] == "not_in_graph" and "No fleet service depends on lonely-evil" in miss["message"]
    assert "no compromised version is recorded" not in miss["message"]

"""Bring-your-own-repo scans. Unit tests run offline; graph tests need FalkorDB with fleet:acme loaded.

Network: scans use the committed cache in data/raw/byo and data/raw/depsdev first. The GitHub test is
skipped when GitHub is unreachable and no cached copy exists.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.byo import scan, sources
from app.byo.api import SCAN_LIMIT
from app.byo.ratelimit import RateLimiter
from app.byo.sources import ByoError, candidate_versions, lockfile_versions, parse_github_url
from app.main import app

client = TestClient(app)
ping = db.ping()
live = pytest.mark.skipif(not (ping["ok"] and ping.get("graph_loaded")), reason="FalkorDB fleet:acme not available")


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    SCAN_LIMIT.reset()
    yield
    SCAN_LIMIT.reset()


# ----------------------------------------------------------------------------- unit
@pytest.mark.parametrize("url,expected", [
    ("https://github.com/expressjs/express", ("expressjs", "express", None)),
    ("github.com/axios/axios.git", ("axios", "axios", None)),
    ("https://github.com/expressjs/express/tree/4.x", ("expressjs", "express", "4.x")),
    ("vercel/ms", ("vercel", "ms", None)),
])
def test_parse_github_url(url, expected):
    assert parse_github_url(url) == expected


@pytest.mark.parametrize("url", ["https://gitlab.com/a/b", "javascript:alert(1)", "https://github.com/onlyowner",
                                 "https://github.com/a/b/../../etc", "file:///etc/passwd", ""])
def test_parse_github_url_rejects(url):
    with pytest.raises(ByoError) as e:
        parse_github_url(url)
    assert e.value.status == 400


def test_candidate_versions_prefers_pre_attack_and_sorts_semver():
    vs = {"versions": [{"versionKey": {"version": v}, "publishedAt": p} for v, p in
                       [("4.3.9", "2024-01-01"), ("4.3.10", "2024-06-01"), ("4.4.3", "2025-09-13"), ("5.0.0", "2025-01-01")]]}
    c, note = candidate_versions(vs, "^4.3.0", "2025-09-08T00:00:00Z")
    assert c == ["4.3.10", "4.3.9"] and "before 2025-09-08" in note
    c, _ = candidate_versions(vs, "^4.4.0", "2025-09-08T00:00:00Z")
    assert c == ["4.4.3"]  # nothing matched before the attack, so fall back to newer releases
    assert candidate_versions(vs, "^9", "2025-09-08T00:00:00Z")[0] == []


def test_lockfile_versions_v3_and_v1():
    v3 = {"lockfileVersion": 3, "packages": {"": {"name": "x"}, "node_modules/debug": {"version": "4.4.1"},
                                             "node_modules/a/node_modules/debug": {"version": "2.6.9"}}}
    assert lockfile_versions(v3) == {"debug": ["2.6.9", "4.4.1"]}
    v1 = {"lockfileVersion": 1, "dependencies": {"a": {"version": "1.0.0", "dependencies": {"debug": {"version": "4.4.2"}}}}}
    assert lockfile_versions(v1) == {"a": ["1.0.0"], "debug": ["4.4.2"]}
    assert lockfile_versions(None) == {}


def test_rate_limiter_window():
    rl = RateLimiter(2, 60)
    assert rl.check("ip") == 0 and rl.check("ip") == 0
    assert rl.check("ip") > 0
    assert rl.check("other") == 0


def test_direct_deps_cap_and_validation():
    pkg = {"dependencies": {f"p{i}": "^1.0.0" for i in range(70)}, "devDependencies": {"Bad Name!": "1"}}
    deps, skipped = scan.direct_deps(pkg, True)
    assert len(deps) == scan.MAX_DIRECT and len(skipped) == 10


# ----------------------------------------------------------------------------- API validation (no graph needed)
def test_scan_requires_exactly_one_input():
    assert client.post("/api/byo/scan", json={}).status_code == 422
    assert client.post("/api/byo/scan", json={"github_url": "a/b", "package_json": "{}"}).status_code == 422


def test_scan_rejects_bad_json_and_bad_url():
    r = client.post("/api/byo/scan", json={"package_json": "{not json"})
    assert r.status_code == 400 and r.json()["error"] == "bad_package_json"
    r = client.post("/api/byo/scan", json={"github_url": "https://example.com/x/y"})
    assert r.status_code == 400 and r.json()["error"] == "bad_github_url"
    r = client.post("/api/byo/scan", json={"package_json": json.dumps({"name": "empty"})})
    assert r.status_code == 422 and r.json()["error"] == "no_dependencies"


def test_scan_size_limit():
    big = json.dumps({"dependencies": {"a": "1"}, "pad": "x" * (sources.MAX_PACKAGE_JSON + 10)})
    assert client.post("/api/byo/scan", json={"package_json": big}).status_code == 422  # pydantic max_length


def test_rate_limit_429():
    body = {"package_json": "{not json"}
    codes = [client.post("/api/byo/scan", json=body).status_code for _ in range(SCAN_LIMIT.limit + 1)]
    assert codes[:-1] == [400] * SCAN_LIMIT.limit and codes[-1] == 429
    r = client.post("/api/byo/scan", json=body)
    assert r.json()["error"] == "rate_limited" and "Retry-After" in r.headers


# ----------------------------------------------------------------------------- live graph
def _scan(**body):
    r = client.post("/api/byo/scan", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@live
def test_pasted_express4_is_clean_with_honest_negative():
    d = _scan(package_json=json.dumps({"name": "my-api", "dependencies": {"express": "4.21.2"}}))
    assert d["scan_id"].startswith("byo:") and d["outcome"] == "not_in_graph"
    assert d["exposed"] == []
    neg = [x for x in d["not_exposed"] if x["package"] == "debug"]
    assert neg and "2.6.9" in neg[0]["reason"] and "does not satisfy 4.4.2" in neg[0]["reason"]
    assert d["graph_name"] != "fleet:acme"


@live
def test_pasted_deep_chain_exposed_and_isolated_graph():
    d = _scan(package_json=json.dumps({"name": "imgsvc", "dependencies": {"sharp": "0.33.5", "eslint": "9.9.0"}}))
    assert d["outcome"] == "answered_with_path"
    pk = {x["package"]: x for x in d["exposed"]}
    assert "simple-swizzle" in pk and "debug" in pk
    sw = pk["simple-swizzle"]
    assert [n.split("@")[0] for n in sw["package_path"]][1:] == ["sharp", "color", "color-string", "simple-swizzle"]
    assert sw["requirement"] == "^0.2.2" and sw["bad_version"] == "0.2.3" and sw["advisory"] == "MAL-2025-46978"
    assert d["overrides"]["overrides"]["debug@^4"] == "4.4.3"  # transitive (eslint): scoped override
    assert d["graph"]["nodes"] and d["graph"]["edges"] and d["subgraph"] == d["graph"]
    # isolated tenant graph exists and is listed; fleet graph untouched
    assert d["graph_name"] in db.client().list_graphs()
    assert any(s["graph"] == d["graph_name"] for s in client.get("/api/byo/scans").json()["scans"])
    n = db.client().select_graph("fleet:acme").ro_query("MATCH (s:Service {name:'imgsvc'}) RETURN count(s)").result_set[0][0]
    assert n == 0


@live
def test_ttl_cleanup_deletes_expired(monkeypatch):
    d = _scan(package_json=json.dumps({"name": "ttl-test", "dependencies": {"express": "4.21.2"}}))
    assert d["graph_name"] in scan.list_byo_graphs()
    deleted = scan.cleanup(now=10 ** 12)  # far future: everything expired
    assert d["graph_name"] in deleted and d["graph_name"] not in scan.list_byo_graphs()


def _github_ok() -> bool:
    if (sources.CACHE / "github" / "expressjs__express__4.x" / "meta.json").exists():
        return True
    try:
        return httpx.get("https://raw.githubusercontent.com/expressjs/express/4.x/package.json", timeout=5).status_code == 200
    except httpx.HTTPError:
        return False


@live
@pytest.mark.skipif(not _github_ok(), reason="GitHub unreachable and no cached copy")
def test_github_express_4x_runtime_clean():
    d = _scan(github_url="https://github.com/expressjs/express/tree/4.x", include_dev=False)
    assert d["repo"] == "github.com/expressjs/express" and d["exposed"] == []
    assert any("debug 2.6.9" in x["reason"] for x in d["not_exposed"])


def test_scan_graph_down_is_503(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "FALKORDB_URL", "")
    monkeypatch.setattr(config, "FALKORDB_PORT", 1)
    monkeypatch.setattr(db, "_db", None)
    try:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/byo/scan", json={"package_json": json.dumps({"dependencies": {"express": "4.21.2"}})})
        assert r.status_code == 503 and r.json()["error"] == "graph_unavailable"
        assert c.get("/api/byo/scans").status_code == 503
    finally:
        db._db = None


def test_direct_dependency_cap_is_visible():
    from app.byo import scan
    pkg = {"dependencies": {f"pkg-{i}": "^1.0.0" for i in range(scan.MAX_DIRECT + 5)}}
    deps, skipped = scan.direct_deps(pkg, True)
    assert len(deps) == scan.MAX_DIRECT and len(skipped) == 5

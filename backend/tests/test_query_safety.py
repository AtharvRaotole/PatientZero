"""Regression: FalkorDB 6.0.1 panics (src/reply.rs, `Option::unwrap()` on None) and takes the whole server down
when a RETURN clause has an aggregate AND two or more grouping keys, and a grouping key after the first is null.
Minimal reproduction on an empty graph: `MATCH (n:N) RETURN n.name AS x, n.missing AS a, count(n)`.
who_to_wake hit it through Team nodes without display/slack/pager. 4.22.1 is unaffected.

Two guards: a static check that no query in queries/*.cypher uses the construct (aggregate in WITH instead), and a
live run of every query that used to, on a graph whose nodes lack every optional property."""
import importlib.util
import os
import re
import uuid

import pytest

from app import db
from app.db import CATALOGUE, Trace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AGG = re.compile(r"\b(collect|count|sum|min|max|avg|stDev|percentileCont|percentileDisc)\s*\(", re.I)


def _split_top(s: str) -> list[str]:
    out, depth, cur, quote = [], 0, "", None
    for ch in s:
        if quote:
            cur += ch
            quote = None if ch == quote else quote
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


def crashing_returns(cypher: str) -> list[list[str]]:
    """Grouping keys of each RETURN that mixes an aggregate with 2+ grouping keys."""
    cypher = "\n".join(line for line in cypher.splitlines() if not line.strip().startswith("//"))
    hits = []
    for seg in re.split(r"\bUNION(?:\s+ALL)?\b", cypher, flags=re.I):
        rets = list(re.finditer(r"\bRETURN\b(\s+DISTINCT)?", seg, re.I))
        if not rets:
            continue
        body = re.split(r"\b(?:ORDER\s+BY|SKIP|LIMIT)\b", seg[rets[-1].end():], flags=re.I)[0]
        items = [i.strip() for i in _split_top(body) if i.strip()]
        keys = [i for i in items if not AGG.search(i)]
        if len(keys) >= 2 and len(keys) < len(items):
            hits.append(keys)
    return hits


def test_detector_catches_the_crashing_shape():
    assert crashing_returns("MATCH (n:N) RETURN n.name AS x, n.missing AS a, count(n)")
    assert crashing_returns("MATCH (t) RETURN t.a, t.b, collect(DISTINCT {k: t.c}) AS l ORDER BY t.a")
    assert not crashing_returns("MATCH (n:N) WITH n, count(n) AS c RETURN n.name AS x, n.missing AS a, c")
    assert not crashing_returns("MATCH (n:N) RETURN n.missing AS a, count(n)")


def test_no_query_returns_aggregates_with_several_grouping_keys():
    bad = {name: crashing_returns(q["cypher"]) for name, q in CATALOGUE.items()}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"aggregate in WITH, not in a multi-key RETURN (crashes FalkorDB 6.0.1): {bad}"


def test_team_defaults_at_build_time():
    spec = importlib.util.spec_from_file_location("ingest_load", os.path.join(ROOT, "ingest", "load.py"))
    load = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(load)
    assert load.team_defaults({"name": "core"}) == {"name": "core", "display": "core", "slack": "#team-core",
                                                     "pager": "core"}
    full = {"name": "p", "display": "Payments", "slack": "#pay", "pager": "pd"}
    assert load.team_defaults(full) == full


ping = db.ping()


@pytest.fixture
def sparse_graph():
    """Every node carries only its key: no Team display/slack/pager, no Service tier/env/repo, no Incident fields."""
    name = f"sparsetest:{uuid.uuid4().hex[:8]}"
    g = db.client().select_graph(name)
    g.query("""CREATE (t:Team {name:'core'}), (s:Service {name:'svc'}), (t)-[:OWNS]->(s),
                      (:Person {name:'Ann'})-[:ONCALL_FOR]->(t),
                      (m:Maintainer {npm:'mx'})-[:MAINTAINS]->(p:Package {name:'pkg'})-[:HAS_VERSION]->(v:Version {key:'pkg@1.0.0'}),
                      (s)-[:DEPENDS_ON]->(v), (v)-[:REQUIRES]->(:Version {key:'dep@1.0.0'}), (a:Advisory {id:'ADV-1', ids:['ADV-1']})-[:AFFECTS]->(v),
                      (i:Incident {id:'inc-1'})-[:ABOUT]->(m), (i)-[:EXPOSED]->(s),
                      (r:CrewRun {id:'run-1'})-[:RAN]->(:AgentRun)""")
    yield name
    g.delete()


@pytest.mark.skipif(not ping["ok"], reason="FalkorDB not running")
def test_formerly_crashing_queries_run_on_sparse_nodes(sparse_graph):
    tr = Trace(graph_name=sparse_graph)
    w = tr.run("who_to_wake", "who_to_wake", {"services": ["svc"]})
    assert w == [{"service": "svc", "tier": None, "env": None, "runtime": None, "repo": None, "team": "core",
                  "team_display": "core", "slack": "#team-core", "pager": "core",
                  "oncall": [{"name": "Ann", "handle": None, "email": None}],
                  "members": [{"name": None, "handle": None, "role": None}]}]
    assert tr.run("fleet", "fleet_overview")[0]["team"] == "core"
    assert tr.run("recall", "list_incidents", {"limit": 5})[0]["services"] == ["svc"]
    rec = tr.run("recall", "recall", {"entity_key": "mx"})
    assert rec[0]["id"] == "inc-1" and rec[0]["about_kind"] == "Maintainer" and rec[0]["patch"] is None
    assert tr.run("watch", "watch_replay_lookup", {"q": "ADV-1"})[0]["bad_versions"] == []
    assert tr.run("crew", "crew_count_check", {"id": "run-1"}) == [{"agents": 1, "findings": 0, "handoffs": 0}]
    spof = tr.run("spof", "single_points_of_failure", {"limit": 5})
    assert spof[0]["maintainer"] == "mx" and spof[0]["compromised_in_incident"] is False
    assert db.client().connection.ping()  # the server is still up

"""Run every query in queries/*.cypher against the loaded graph and check the data-level acceptance criteria
(AC1-3, AC5, AC6, AC7, AC8, AC11, AC12, AC15 and memory write/recall for AC9/AC10). Exits non-zero on failure.

Run:  python ingest/verify.py
"""
import json, os, sys, time, uuid
sys.path.insert(0, os.path.dirname(__file__))
from load import connect, GRAPH
from pz_queries import load_queries, classify

Q = load_queries()
g = connect().select_graph(GRAPH)
fails = []


def run(name, params=None, write=False):
    t = time.time()
    res = (g.query if write else g.ro_query)(Q[name], params or {})
    ms = (time.time() - t) * 1000
    hdr = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    rows = [dict(zip(hdr, r)) for r in res.result_set]
    print(f"  [{name}] {len(rows)} rows, {ms:.1f} ms")
    return rows, ms


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print(f"queries loaded: {sorted(Q)}")
print("\nAC1-3 graph sanity")
check(g.ro_query("MATCH (s:Service) RETURN count(s)").result_set[0][0] >= 15, "AC1 >=15 services")
check(g.ro_query("MATCH (a:Advisory)-[:AFFECTS]->(v:Version) WHERE a.campaign IS NULL OR a.campaign = '2025-09-08-qix' RETURN count(DISTINCT v)").result_set[0][0] == 19, "AC2 19 affected versions")
r = g.ro_query("MATCH (:Maintainer {npm:'qix'})-[h:HAD_PUBLISH_RIGHTS]->(p) RETURN count(p), sum(CASE WHEN h.source IS NULL THEN 1 ELSE 0 END)").result_set[0]
check(r[0] == 19 and r[1] == 0, "AC3 19 HAD_PUBLISH_RIGHTS edges with source")

print("\nresolve_entity")
def resolve(text):
    pkg, ver = (text.split("@", 1) + [""])[:2] if "@" in text[1:] else ("", "")
    rows, _ = run("resolve_entity", {"text": text, "pkg": pkg, "version": ver})
    return rows
for t, kind in [("qix", "Maintainer"), ("GHSA-4x49-vf9v-38px", "Advisory"), ("GHSA-8mgj-vmr8-frr6", "Advisory"),
                ("debug@4.4.2", "Version"), ("img-service", "Service"), ("chalk", "Package")]:
    rows = resolve(t)
    check(any(x["kind"] == kind for x in rows), f"resolve '{t}' -> {kind} {[x['key'] for x in rows]}")
check(resolve("left-pad@1.3.0") == [], "AC7 left-pad@1.3.0 -> unknown_entity (0 rows)")
check(resolve("GHSA-xxxx-xxxx-xxxx") == [], "AC7 GHSA-xxxx-xxxx-xxxx -> unknown_entity")

print("\nincident_scope")
scope, _ = run("incident_scope_by_maintainer", {"maintainer": "qix"})
check(len(scope) == 19, "qix scope = 19 bad versions")
bad = {r["pkg"]: r["bad_version"] for r in scope}
s2, _ = run("incident_scope_by_advisory", {"advisory_id": "GHSA-4x49-vf9v-38px"})
check(len(s2) == 1 and s2[0]["pkg"] == "debug" and s2[0]["clean_version"] == "4.4.3", "GHSA-4x49 -> debug 4.4.2 / fixed 4.4.3")
s3, _ = run("incident_scope_by_version", {"pkg": "debug", "version": "4.4.2"})
check(len(s3) == 1 and s3[0]["advisory"] == "MAL-2025-46974", "debug@4.4.2 -> MAL-2025-46974")

print("\nexposure (qix scope) by depth")
counts, sample = {}, {}
for d in range(1, 7):
    rows, ms = run("exposure", {"pkgs": list(bad), "depth": d})
    exp, safe = classify(rows, bad)
    counts[d] = sorted({r["service"] for r in exp})
    sample[d] = (exp, safe)
    if d == 4:
        check(ms < 500, f"AC15 exposure depth 4 in {ms:.0f} ms (<500)")
for d in counts:
    print(f"  depth {d}: {len(counts[d])} exposed {counts[d]}")
check(len(counts[4]) > len(counts[1]), "AC5 depth4 > depth1")
check("img-service" in counts[4] and "img-service" not in counts[2], "AC5 img-service only at deeper depth")
check(len(counts[4]) >= 5, "AC4 >=5 exposed services at depth 4")

print("\nAC6 debug@4.4.2")
rows, _ = run("exposure", {"pkgs": ["debug"], "depth": 4})
exp, safe = classify(rows, {"debug": "4.4.2"})
es = {r["service"] for r in exp}
check("checkout-api" not in es, "checkout-api NOT exposed: " + "; ".join(sorted({r["reason"] for r in safe if r["service"] == "checkout-api"})))
check("lint-service" in es, "lint-service exposed")

print("\nAC8 ansi-regex@6.2.1")
rows, _ = run("exposure", {"pkgs": ["ansi-regex"], "depth": 6})
exp, safe = classify(rows, {"ansi-regex": "6.2.1"})
check(len(rows) > 0 and not exp, f"ansi-regex in graph ({len(rows)} paths) but no range admits 6.2.1 -> not_in_graph")

print("\nwho_to_wake")
w, _ = run("who_to_wake", {"services": counts[4]})
check(len(w) == len(counts[4]) and all(x["oncall"][0]["handle"] for x in w), "every exposed service has team + on-call")

print("\nAC11 single_points_of_failure")
spof, ms = run("single_points_of_failure", {"limit": 10})
for r in spof[:5]:
    print(f"    {r['maintainer']:16} reach={r['reach']} pagerank={r['pagerank']:.4f} pkgs={r['packages_in_fleet']}")
check(len(spof) >= 5 and any(r["maintainer"] in ("qix", "sindresorhus") for r in spof[:3]), "qix/sindresorhus in top 3")
run("top_packages_pagerank", {"limit": 5}); run("package_reach", {"limit": 5})

print("\nAC12 path_between img-service is-arrayish")
p, _ = run("path_between", {"service": "img-service", "pkg": "is-arrayish"})
p2, _ = run("path_between_shortestpath", {"service": "img-service", "pkg": "is-arrayish"})
print("   ", p and " -> ".join(p[0]["via"]))
check(p and [n.split("@")[0] for n in p[0]["via"][1:]] == ["sharp", "color", "color-string", "simple-swizzle", "is-arrayish"], "SPpaths hop list")
check(p2 and p2[0]["via"] == p[0]["via"], "shortestPath fallback agrees")

print("\nmemory: remediation_plan writes + recall (test incident is deleted afterwards)")
iid = "verify-" + uuid.uuid4().hex[:8]
exp4 = sample[4][0]
first = {}
for r in exp4:
    first.setdefault(r["service"], r)
try:
    run("create_incident", {"id": iid, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "trigger": "maintainer qix was phished",
                            "entity_kind": "Maintainer", "entity_key": "qix", "depth": 4,
                            "exposed_count": len(first), "patch": json.dumps({})}, write=True)
    run("link_incident_entity", {"id": iid, "entity_key": "qix"}, write=True)
    run("link_exposed", {"id": iid, "rows": [{"service": s, "path": r["via"], "requirement": r["requirement"], "pkg": r["pkg"],
                                             "advisory": "", "hops": r["hops"]} for s, r in first.items()]}, write=True)
    run("add_findings", {"id": iid, "at": "now", "session": "verify", "rows": [{"service": s, "text": r["reason"]} for s, r in first.items()]}, write=True)
    n = g.ro_query("MATCH (i:Incident {id:$id})-[e:EXPOSED]->() RETURN count(e)", {"id": iid}).result_set[0][0]
    check(n == len(first), f"AC9 EXPOSED edges == exposed count ({n})")
    for key in ["qix", "debug@4.4.2", "MAL-2025-46974", "debug"]:
        rec, _ = run("recall", {"entity_key": key})
        check(any(x["id"] == iid for x in rec), f"recall '{key}' finds the incident")
    rec, _ = run("recall", {"entity_key": "express"})
    check(not any(x["id"] == iid for x in rec), "recall 'express' does not")
finally:
    g.query("MATCH (i:Incident {id:$id}) OPTIONAL MATCH (i)-[:HAS_FINDING]->(f) DELETE f, i", {"id": iid})

print("\nfleet_overview")
run("fleet_overview"); run("graph_stats")

print("\nSample exposed paths (depth 4, first per service):")
for s, r in sorted(first.items()):
    print(f"  {s:22} {' -> '.join(r['via'])}   [{r['reason']}]")
print("\nSample honest negatives:")
for s in ["checkout-api", "ci-runner", "realtime-gateway", "build-service"]:
    for r in sample[6][1]:
        if r["service"] == s:
            print(f"  {s:22} {' -> '.join(r['via'])}   [{r['reason']}]"); break

print(f"\n{'ALL CHECKS PASSED' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)

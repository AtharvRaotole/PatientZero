"""Load PatientZero's graph into FalkorDB from the cached data only (no network).

Inputs  : data/fleet/acme.json            (SYNTHETIC Acme fleet: services, teams, people)
          data/incidents/2025-09-08-qix.json (curated incident: 19 bad versions, sources)
          data/raw/depsdev/*.json          (resolved dependency graphs, deps.dev v3)
          data/raw/osv/*.json              (OSV advisories; aliases + summaries)
          data/raw/npm/*.latest.json       (current npm maintainers)
Output  : graph $FALKORDB_GRAPH (default 'fleet:<tenant>'), wiped and rebuilt (idempotent).

Tenants : TENANT=acme (default) reads data/fleet/acme.json into fleet:acme; TENANT=globex reads
          data/fleet/globex.json into fleet:globex. Each tenant is its own FalkorDB graph (multigraph), so
          incidents, playbooks, sessions and crew runs never leak between tenants.

Run:  python ingest/load.py           (env: TENANT, FALKORDB_HOST, FALKORDB_PORT, FALKORDB_USERNAME,
                                         FALKORDB_PASSWORD, FALKORDB_GRAPH)
      TENANT=globex python ingest/load.py
"""
import json, os, re, sys, time
from pathlib import Path
from falkordb import FalkorDB

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
TENANT = os.getenv("TENANT", "acme").strip().lower() or "acme"
GRAPH = os.getenv("FALKORDB_GRAPH") or f"fleet:{TENANT}"
# Optional extra incidents, comma separated. 'qix' is always loaded; 'shai-hulud' adds the Sept 2025 worm
# (data/incidents/2025-09-15-shai-hulud.json, built by ingest/fetch_shai_hulud.py). Off by default for acme so
# the SPEC acceptance counts (19 affected versions) hold. Globex's story is the Shai-Hulud contrast, so every
# tenant other than acme loads it by default.
def fleet_file(tenant: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", tenant):
        raise ValueError(f"bad tenant name {tenant!r}")
    return DATA / "fleet" / f"{tenant}.json"


def incidents_for(tenant: str) -> set[str]:
    raw = os.getenv("INCIDENTS")
    if raw is None:
        raw = "qix" if tenant == "acme" else "qix,shai-hulud"
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


INCIDENTS = incidents_for(TENANT)


def connect():
    kw = dict(host=os.getenv("FALKORDB_HOST", "localhost"), port=int(os.getenv("FALKORDB_PORT", "6379")))
    if os.getenv("FALKORDB_USERNAME"):
        kw["username"] = os.getenv("FALKORDB_USERNAME")
    if os.getenv("FALKORDB_PASSWORD"):
        kw["password"] = os.getenv("FALKORDB_PASSWORD")
    return FalkorDB(**kw)


def safe(name):
    return name.replace("/", "__")


def locked_version(req):
    return re.search(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", req).group(0)


def vkey(pkg, ver):
    return f"{pkg}@{ver}"


def chunks(rows, n=500):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def build(tenant: str = TENANT):
    fleet = json.loads(fleet_file(tenant).read_text())
    incident = json.loads((DATA / "incidents" / "2025-09-08-qix.json").read_text())

    versions = {}   # key -> {pkg, version, unpublished, malicious}
    requires = {}   # (from_key, to_key) -> requirement
    svc_deps = []   # {service, key, requirement}

    for s in fleet["services"]:
        for dep, req in s["dependencies"].items():
            ver = locked_version(req)
            g = json.loads((RAW / "depsdev" / f"{safe(dep)}@{ver}.json").read_text())
            if g.get("_status") == 404:
                print(f"WARN no deps.dev graph for {dep}@{ver}", file=sys.stderr)
                continue
            keys = []
            for n in g["nodes"]:
                vk = n["versionKey"]
                k = vkey(vk["name"], vk["version"])
                keys.append(k)
                versions.setdefault(k, {"key": k, "pkg": vk["name"], "version": vk["version"],
                                        "unpublished": False, "malicious": False})
            svc_deps.append({"service": s["name"], "key": keys[0], "requirement": req})
            for e in g["edges"]:
                requires[(keys[e["fromNode"]], keys[e["toNode"]])] = e["requirement"]

    # Advisories: curated incident rows, enriched with OSV raw (aliases, summary, published)
    advisories = []
    for c in incident["compromised"]:
        osv = json.loads((RAW / "osv" / f"{c['mal_id']}.json").read_text())
        osv_versions = [v for a in osv["affected"] for v in a.get("versions", [])]
        assert osv_versions == [c["bad_version"]], (c, osv_versions)  # curated == OSV
        aliases = sorted(set(c["aliases"]) | set(osv.get("aliases", [])))
        ghsa = [a for a in aliases if a.startswith("GHSA-")]
        cve = [a for a in aliases if a.startswith("CVE-")]
        # prefer the richer GHSA summary when cached
        summary = osv.get("summary", "")
        for g in ghsa:
            p = RAW / "osv" / f"{g}.json"
            if p.exists():
                gs = json.loads(p.read_text()).get("summary")
                if gs and "account takeover" in gs:
                    summary = gs
        k = vkey(c["package"], c["bad_version"])
        versions[k] = {"key": k, "pkg": c["package"], "version": c["bad_version"],
                       "unpublished": True, "malicious": True}
        advisories.append({
            "id": c["mal_id"], "aliases": aliases, "ids": [x.upper() for x in [c["mal_id"], *aliases]], "ghsa": ghsa[0] if ghsa else None,
            "cve": cve[0] if cve else None, "summary": summary,
            "published": osv.get("published"), "url": c["osv_url"], "pkg": c["package"], "campaign": incident["id"],
            "bad_version": c["bad_version"], "fixed_version": c["clean_version"],
            "key": k, "source": c["source"]})

    # Current npm maintainers (public npm usernames only; emails dropped)
    maint = []
    pkgs = sorted({v["pkg"] for v in versions.values()})
    for p in pkgs:
        f = RAW / "npm" / f"{safe(p)}.latest.json"
        if not f.exists():
            continue
        for m in (json.loads(f.read_text()).get("maintainers") or []):
            if m.get("name"):
                maint.append({"npm": m["name"], "pkg": p})

    return fleet, incident, versions, requires, svc_deps, advisories, maint, pkgs


def load(db=None, tenant: str = TENANT, graph: str | None = None):
    fleet, incident, versions, requires, svc_deps, advisories, maint, pkgs = build(tenant)
    if db is None:
        url = os.getenv("FALKORDB_URL")
        db = FalkorDB.from_url(url) if url else connect()
    graph = graph or (GRAPH if tenant == TENANT else f"fleet:{tenant}")
    g = db.select_graph(graph)
    t0 = time.time()
    try:
        g.delete()
    except Exception:
        pass  # graph did not exist
    q = g.query
    for stmt in ["CREATE INDEX FOR (p:Package) ON (p.name)",
                 "CREATE INDEX FOR (v:Version) ON (v.key)",
                 "CREATE INDEX FOR (v:Version) ON (v.pkg)",
                 "CREATE INDEX FOR (s:Service) ON (s.name)",
                 "CREATE INDEX FOR (m:Maintainer) ON (m.npm)",
                 "CREATE INDEX FOR (a:Advisory) ON (a.id)",
                 "CREATE INDEX FOR (t:Team) ON (t.name)",
                 "CREATE INDEX FOR (p:Person) ON (p.handle)",
                 "CREATE INDEX FOR (i:Incident) ON (i.id)",
                 "CREATE INDEX FOR (pb:Playbook) ON (pb.id)"]:
        q(stmt)

    # people / teams / services (synthetic)
    q("UNWIND $rows AS r CREATE (:Team {name:r.name, display:r.display, slack:r.slack, pager:r.pager, synthetic:true})",
      {"rows": fleet["teams"]})
    q("""UNWIND $rows AS r MATCH (t:Team {name:r.team})
         CREATE (p:Person {name:r.name, handle:r.handle, email:r.email, role:r.role, synthetic:true})-[:MEMBER_OF]->(t)
         FOREACH (_ IN CASE WHEN r.oncall THEN [1] ELSE [] END | CREATE (p)-[:ONCALL_FOR {rotation:'primary'}]->(t))""",
      {"rows": [{**p, "oncall": bool(p.get("oncall"))} for p in fleet["people"]]})
    q("""UNWIND $rows AS r MATCH (t:Team {name:r.team})
         CREATE (t)-[:OWNS]->(:Service {name:r.name, tier:r.tier, env:r.env, runtime:r.runtime, repo:r.repo,
                                       description:r.description, synthetic:true})""",
      {"rows": fleet["services"]})

    # packages + versions
    for c in chunks([{"name": p} for p in pkgs]):
        q("UNWIND $rows AS r CREATE (:Package {name:r.name})", {"rows": c})
    for c in chunks(list(versions.values())):
        q("""UNWIND $rows AS r MATCH (p:Package {name:r.pkg})
             CREATE (p)-[:HAS_VERSION]->(:Version {key:r.key, name:r.key, pkg:r.pkg, version:r.version,
                                                   unpublished:r.unpublished, malicious:r.malicious})""",
          {"rows": c})

    # dependency edges (requirement kept on every edge; weight for algo.SPpaths)
    q("""UNWIND $rows AS r MATCH (s:Service {name:r.service}), (v:Version {key:r.key})
         CREATE (s)-[:DEPENDS_ON {requirement:r.requirement, weight:1}]->(v)""", {"rows": svc_deps})
    for c in chunks([{"a": a, "b": b, "req": req} for (a, b), req in requires.items()]):
        q("""UNWIND $rows AS r MATCH (a:Version {key:r.a}), (b:Version {key:r.b})
             CREATE (a)-[:REQUIRES {requirement:r.req, weight:1}]->(b)""", {"rows": c})

    # advisories + curated incident publish rights
    q("""UNWIND $rows AS r MATCH (v:Version {key:r.key})
         CREATE (a:Advisory {id:r.id, aliases:r.aliases, ids:r.ids, ghsa:r.ghsa, cve:r.cve, summary:r.summary,
                             published:r.published, url:r.url, pkg:r.pkg, bad_version:r.bad_version,
                             fixed_version:r.fixed_version, campaign:r.campaign})-[:AFFECTS]->(v)""", {"rows": advisories})
    m = incident["maintainer"]
    q("CREATE (:Maintainer {npm:$npm, name:$name, github:$gh})", {"npm": m["npm"], "name": m["name"], "gh": m["github"]})
    q("""UNWIND $rows AS r MATCH (m:Maintainer {npm:$npm}), (p:Package {name:r.package})
         CREATE (m)-[:HAD_PUBLISH_RIGHTS {asOf:$asOf, source:r.source, incident:$inc}]->(p)""",
      {"rows": incident["compromised"], "npm": m["npm"], "asOf": incident["date"], "inc": incident["id"]})
    for c in chunks(maint):
        q("""UNWIND $rows AS r MERGE (m:Maintainer {npm:r.npm}) WITH m, r
             MATCH (p:Package {name:r.pkg}) CREATE (m)-[:MAINTAINS {source:'registry.npmjs.org/latest'}]->(p)""",
          {"rows": c})

    q("CREATE INDEX FOR (s:Session) ON (s.id)")
    q("CREATE INDEX FOR (r:CrewRun) ON (r.id)")
    q("CREATE INDEX FOR (a:AgentRun) ON (a.id)")
    if "shai-hulud" in incidents_for(tenant):
        load_shai_hulud(g)
    stats(g, time.time() - t0, graph)


def load_shai_hulud(g):
    """One Advisory per OSV MAL record, AFFECTS every listed bad version (unpublished). No clean versions and
    no single maintainer, so no HAD_PUBLISH_RIGHTS edge; scope it by Advisory.campaign.

    Idempotent (MERGE only): safe to run again on a live graph without touching memory or other data."""
    f = DATA / "incidents" / "2025-09-15-shai-hulud.json"
    if not f.exists():
        print("WARN INCIDENTS includes shai-hulud but the incident file is missing; run ingest/fetch_shai_hulud.py",
              file=sys.stderr)
        return
    inc = json.loads(f.read_text())
    rows = []
    for c in inc["compromised"]:
        aliases = c.get("aliases") or []
        rows.append(dict(id=c["mal_id"], ids=[x.upper() for x in [c["mal_id"], *aliases]], aliases=aliases,
                         ghsa=next((a for a in aliases if a.startswith("GHSA-")), None), pkg=c["package"],
                         bad_versions=c["bad_versions"], published=c.get("published"), url=c["osv_url"],
                         summary=f"Malicious code in {c['package']} (npm), Shai-Hulud worm", campaign=inc["id"]))
    g.query("""UNWIND $rows AS r MERGE (p:Package {name:r.pkg})
               MERGE (a:Advisory {id:r.id})
                 ON CREATE SET a.ids = r.ids, a.aliases = r.aliases, a.ghsa = r.ghsa, a.cve = null,
                               a.summary = r.summary, a.published = r.published, a.url = r.url, a.pkg = r.pkg,
                               a.bad_version = r.bad_versions[size(r.bad_versions)-1],
                               a.bad_versions = r.bad_versions, a.fixed_version = null, a.campaign = r.campaign
               WITH a, p, r UNWIND r.bad_versions AS bv
               MERGE (v:Version {key: r.pkg + '@' + bv})
                 ON CREATE SET v.name = r.pkg + '@' + bv, v.pkg = r.pkg, v.version = bv, v.unpublished = true,
                               v.malicious = true
                 ON MATCH SET v.malicious = true
               MERGE (p)-[:HAS_VERSION]->(v)
               MERGE (a)-[:AFFECTS]->(v)""", {"rows": rows})
    print(f"  + shai-hulud: {len(rows)} advisories, {sum(len(r['bad_versions']) for r in rows)} bad versions")


def stats(g, secs=None, name=None):
    nodes = g.ro_query("MATCH (n) RETURN labels(n)[0] AS l, count(n) ORDER BY l").result_set
    rels = g.ro_query("MATCH ()-[r]->() RETURN type(r) AS t, count(r) ORDER BY t").result_set
    print(f"graph '{name or GRAPH}' loaded" + (f" in {secs:.1f}s" if secs else ""))
    print("  nodes: " + ", ".join(f"{l}={c}" for l, c in nodes) + f"  (total {sum(c for _, c in nodes)})")
    print("  edges: " + ", ".join(f"{t}={c}" for t, c in rels) + f"  (total {sum(c for _, c in rels)})")


if __name__ == "__main__":
    load()

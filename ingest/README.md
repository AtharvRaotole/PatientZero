# PatientZero data ingest

Builds graph **`fleet:acme`** in FalkorDB from public data (deps.dev, OSV, npm registry) plus a
**synthetic** company fleet. Every API response is cached and committed under `data/raw/`, so loading the
graph never touches the network.

## Run

```bash
# FalkorDB must be up on localhost:6379 (scripts/start-falkordb.sh)
python3 -m venv ingest/.venv && ingest/.venv/bin/pip install -r ingest/requirements.txt
ingest/.venv/bin/python ingest/fetch.py      # optional: only fetches what's missing in data/raw (add --refresh to re-download)
ingest/.venv/bin/python ingest/load.py       # wipes and rebuilds graph fleet:acme (idempotent, under 1 s)
ingest/.venv/bin/python ingest/verify.py     # runs every queries/*.cypher query and checks the data-level ACs
```

Env: `FALKORDB_HOST` (default `localhost`), `FALKORDB_PORT` (`6379`), `FALKORDB_USERNAME`, `FALKORDB_PASSWORD`
(FalkorDB Cloud), `FALKORDB_GRAPH` (`fleet:acme`).

## Inputs

| File | What it is | Source |
|---|---|---|
| `data/incidents/2025-09-08-qix.json` | **Curated** ground truth: maintainer `qix` (Josh Junon), 19 malicious versions, MAL ids, aliases, clean versions | debug-js/debug#1005, GHSA pages, OSV. `load.py` asserts every row matches the cached OSV record |
| `data/fleet/acme.json` | **SYNTHETIC** Acme fleet: 22 services, 8 teams, 16 people (handles, on-call) | Invented. Only the npm package names and versions are real |
| `data/raw/osv/*.json` | OSV `query_<pkg>.json` (POST /v1/query) and `<ID>.json` (GET /v1/vulns/{id}) for the MAL and GHSA ids | https://api.osv.dev |
| `data/raw/depsdev/<pkg>@<ver>.json` | Resolved dependency graph for each direct dependency at its pinned version | `https://api.deps.dev/v3/systems/npm/packages/{pkg}/versions/{ver}:dependencies` |
| `data/raw/npm/<pkg>.latest.json` | Current `maintainers` for every package in the graph (982) | `https://registry.npmjs.org/{pkg}/latest` |
| `data/raw/npm/<pkg>.packument.json` | Full packument for the 20 compromised packages: the bad version is in `time` but not in `versions` (unpublished) | `https://registry.npmjs.org/{pkg}` |

Scoped package names are stored with `/` replaced by `__` in file names.

## Graph model (`fleet:acme`)

```
(:Team {name, display, slack, pager})-[:OWNS]->(:Service {name, tier, env, runtime, repo, description})
(:Person {name, handle, email, role})-[:MEMBER_OF]->(:Team)
(:Person)-[:ONCALL_FOR {rotation}]->(:Team)
(:Service)-[:DEPENDS_ON {requirement, weight}]->(:Version)          // direct dep, version from package.json pin
(:Version)-[:REQUIRES {requirement, weight}]->(:Version)            // deps.dev resolved edge, declared range kept
(:Package {name})-[:HAS_VERSION]->(:Version {key:'pkg@ver', name:'pkg@ver', pkg, version, unpublished, malicious})
(:Advisory {id:MAL-id, ids, aliases, ghsa, cve, summary, published, url, pkg, bad_version, fixed_version})
        -[:AFFECTS]->(:Version {unpublished:true, malicious:true})
(:Maintainer {npm})-[:MAINTAINS {source}]->(:Package)                // today's registry
(:Maintainer {npm:'qix'})-[:HAD_PUBLISH_RIGHTS {asOf:'2025-09-08', source, incident}]->(:Package)   // curated, 19 edges
// written by the app (remediation_plan.cypher):
(:Incident {id, at, trigger, entity_kind, entity_key, depth, exposed_count, patch})-[:ABOUT]->(Maintainer|Advisory|Version|Package)
(:Incident)-[:EXPOSED {path, via_requirement, pkg, advisory, hops}]->(:Service)
(:Incident)-[:HAS_FINDING]->(:Finding {text, at, session, incident})-[:ABOUT]->(:Service)
```

Indexes: `Package(name)`, `Version(key)`, `Version(pkg)`, `Service(name)`, `Maintainer(npm)`, `Advisory(id)`,
`Team(name)`, `Person(handle)`, `Incident(id)`.

`Advisory.id` is the OSV **MAL** id (primary key). `Advisory.ids` is the upper-cased list of id plus aliases
(GHSA, CVE), so lookups use `toUpper($x) IN a.ids`. debug has two GHSA ids, and both resolve.

## Exposure rule (assumption)

The malicious versions were unpublished within hours, so today's deps.dev resolution never contains them.
Each resolved edge keeps the **declared semver range**. A service is **exposed** to `pkg@bad` if a
dependency path (up to depth N) ends in an edge into `pkg` whose `requirement` admits `bad` under npm semver
(`nodesemver.satisfies(bad, requirement, loose=True)`). In other words, a fresh install during the attack
window *could* have pulled it. Lockfiles are not modelled: the fleet has no lockfiles, and this is the
worst case. Other caveats:
- Resolution is deps.dev's *current* resolution of each pinned direct dependency. Version nodes are shared
  across services, so path enumeration can, rarely, mix edges from two different roots' resolutions.
- Only runtime dependencies (what deps.dev resolves) are included. devDependencies are not.

## Queries

`queries/*.cypher` hold one file per tool. Named blocks start with `// @query <name>`, and
`ingest/pz_queries.py::load_queries()` parses them. `classify()` applies the semver rule.
FalkorDB v6.0.1 notes: a variable-length bound can't be a parameter, so `exposure` uses `*1..6` plus
`length(p) <= $depth`. `exists(<pattern>)` isn't supported, so the queries use pattern predicates or
`size([pattern | x])`. Verified procedures: `algo.pageRank('Version','REQUIRES')` and
`algo.SPpaths({sourceNode, targetNode, relTypes, maxLen, weightProp, pathCount})`, which yields `path, pathWeight`.

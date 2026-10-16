# FIRST BRIEF: "PatientZero", a supply-chain incident agent on FalkorDB
Owner: Research/Product. Builder (FullStack) owns code and final scope calls. The full SPEC.md comes next; this brief is enough to start now.

## ⏰ DEADLINE: Sat Oct 18, 11:59 PM IST = **Sat Oct 18, 2:29 PM ET** (18:29 UTC)
About 63 h from brief time (Thu Oct 15, 11:40 PM ET). **Internal code freeze: Sat Oct 18, 8:00 AM ET.** After that it's only video, README, and deploy.

## 📦 REQUIRED SUBMISSION ARTIFACTS (rule 10, https://www.wemakedevs.org/hackathons/falkordb/rules)
1. Public GitHub repo
2. README with setup instructions **and the graph data model** (labels and relationships diagram)
3. The **Cypher queries and graph algorithms** the product depends on (put them in `queries/*.cypher` and link them from the README)
4. **Demo video** of the working project (target 2:00)
5. **Live deployment** (preferred), or complete local setup instructions
6. The track(s) entered: **Track 1, Agents That Act on Connected Data** (prize: iPhone 18 Pro). Optionally also name Track 2 if memory is solid (the rules allow multiple tracks).
7. **Disclose AI coding assistant use** in the README (rule 11). Make sure Atharv can explain the model, the queries, and the architecture (rule 12).

Hard rule: FalkorDB must do real work. "If the product still works once FalkorDB is taken out, the graph is decoration." Our product is traversals and algorithms. **No Postgres or SQLite for core data.**

## One-line pitch
**When an npm maintainer gets phished, PatientZero tells you in seconds which of your services are hit, exactly which dependency chain got them there, who to wake, and hands you the fix.** It's built on real public data and a real attack (the Sept 8 2025 chalk/debug takeover).

## Why this wins (judge lens)
- It's Track 1 verbatim: "security triage", multi-hop reasoning, graph algorithms as agent tools, tool selection through MCP, actions based on the graph, explainable results.
- It goes beyond the organiser's own reference demo ("Blast Radius" in their blog): real ecosystem data from deps.dev, OSV, and npm instead of a toy graph, a real incident replay, and a concrete action (a patch plus notifications) instead of just an answer.
- The graph is unavoidable: transitive dependency exposure is a variable-length path, and "which maintainer is our single point of failure" is PageRank or centrality. That makes it impossible to fake with a table.

## ⚠️ Constraints (from the parent agent)
- **No LLM API key on the box.** The core journey MUST work with zero LLM. The agent is a **deterministic intent router plus a fixed catalogue of human-written, parameterised Cypher tools**, which is exactly the pattern the organisers recommend. Optional enhancement (ASSUMPTION, only if a key appears): with a user-supplied `LLM_API_KEY` (OpenAI-compatible, e.g. a free-tier Groq or OpenRouter key), the LLM only picks a tool and writes one summary sentence. That sentence is validated so every package or service name it mentions must come from the graph result. Otherwise fall back to the templated sentence.
- **No Docker.** FalkorDB runs as a module in redis-server, e.g. `redis-server --port 6379 --loadmodule /path/to/falkordb.so`. Module binaries are on https://github.com/FalkorDB/FalkorDB/releases (v6.0.2 has no assets; v6.0.1 has `falkordb-rhel9-x64.so`). Fallback: `pip install falkordblite` (embedded FalkorDB). Deployment option: FalkorDB Cloud free tier (100 MB, no card; https://docs.falkordb.com/cloud/tiers/free) plus any host for the API and UI. FalkorDB Browser won't exist without Docker, so our own UI must draw the paths.

## Core user journey (the demo)
1. The user opens PatientZero. It shows the "Acme" fleet: about 20 synthetic company services, each with real npm dependencies at real versions, ingested into FalkorDB (graph `fleet:acme`).
2. The user types: **"maintainer qix was phished, what's hit?"** (or pastes `GHSA-4x49-vf9v-38px`, or `debug@4.4.2`).
3. The agent resolves the entity against the graph. If it's unknown, it says so and runs no query. It then runs tools that are shown live in a **trace panel** (tool, args, ms, rows): `advisory_lookup`, then `exposure`, then `who_to_wake`.
4. The result is a list of affected services, each with its **dependency path drawn as a graph** (e.g. `checkout-api → express → debug@^4.3.4 ⚠ 4.4.2`) and a **depth slider**. At depth 1 only 2 services are hit; at depth 5, 11 are, including ones nobody knew depended on `debug`. That's the 15-second "graph wins" moment.
5. **Action:** "Generate fix". For each service the agent produces an `npm overrides` patch pinning safe versions, plus a notification draft to the owning team and on-call person. Each line cites the path and the OSV advisory. It writes `(:Incident)` and `(:Finding)` nodes back to the graph.
6. **Proactive:** "Who is our single point of failure?" This runs PageRank or betweenness over Maintainer→Package→Service and ranks the maintainers whose account takeover would hit the most services.
7. **Memory:** the user reloads or opens a new session and asks again. The answer is "Answered from memory: incident #1 on <date>, 11 services, patch generated". Asking about a package that's not in the graph gives an honest "not in graph" answer.

## Must-have features (max 3)
1. **Exposure triage with paths:** take an advisory, package@version, or maintainer and return the affected fleet services with full multi-hop paths, a depth control, owners and on-call, and a criticality rank. All of it is graph queries and `algo.*`.
2. **Explainable action plus memory:** generate per-service remediation (npm `overrides` JSON) and notification drafts, each citing its path and the advisory. Persist `Incident` and `Finding` nodes and recall them in later sessions.
3. **Agent with a tool catalogue over MCP and web:** a no-LLM intent router and the same tools exposed as an MCP server (Python `mcp` SDK / FastMCP), so Claude Desktop or Cursor can drive it. The web chat shows the tool trace panel and the honest outcomes ("unknown entity" / "not in graph").

## Graph model (v1)
```
(:Service {name, tier, repo})-[:DEPENDS_ON {requirement, locked}]->(:Package {name})
(:Package)-[:HAS_VERSION]->(:Version {name, version})
(:Version)-[:REQUIRES {requirement}]->(:Package)        // from deps.dev edges
(:Maintainer {name})-[:MAINTAINS]->(:Package)           // from npm registry
(:Advisory {id, summary, published, url})-[:AFFECTS]->(:Version)   // from OSV
(:Team {name})-[:OWNS]->(:Service); (:Person {name})-[:MEMBER_OF]->(:Team); (:Person)-[:ONCALL_FOR]->(:Team)   // synthetic
(:Incident {id, at, query})-[:HIT {path}]->(:Service); (:Finding {text, at, session})-[:ABOUT]->(:Service|:Package)
```
Key idea: **exposure = a path from a Service to a Package whose last requirement range satisfies the malicious version** (semver check in app code, e.g. `semver` / `packaging`-style lib). Fresh installs during the attack window would have pulled it. Store `weight` on edges for `algo.SPpaths`.

## Recommended stack
- FalkorDB (redis module) with the Python client `falkordb` (PyPI 1.8.0). Use `ro_query` for reads and parameterised queries only.
- Backend: Python 3.11+, FastAPI, uvicorn, httpx, `semantic_version` or `nodesemver` for npm ranges, `mcp` (FastMCP) for the MCP server. Run `pip install falkordb fastapi uvicorn httpx node-semver mcp`.
- Frontend: a single page with React and Vite (or plain HTML), **cytoscape.js** for path graphs, and Tailwind. Keep it to one screen: a chat box, a trace panel, a graph view, and a fix panel.
- Optional LLM: an OpenAI-compatible HTTP call behind `LLM_API_KEY`, off by default.
- Data (public, no auth, verified working Oct 15):
  - deps.dev resolved graph: `GET https://api.deps.dev/v3/systems/npm/packages/{pkg}/versions/{ver}:dependencies` returns nodes and edges with `requirement`. Docs: https://docs.deps.dev/api/v3/
  - OSV: `POST https://api.osv.dev/v1/query` `{"package":{"name":"debug","ecosystem":"npm"},"version":"4.4.2"}` returns GHSA-4x49-vf9v-38px (malware after account takeover, Sept 8 2025). Docs: https://google.github.io/osv.dev/api/
  - npm registry maintainers: `GET https://registry.npmjs.org/{pkg}`, field `.maintainers`
  - **Pull the exact list of compromised packages and versions from OSV/GHSA, not from memory.** It includes chalk, debug, ansi-styles, strip-ansi, supports-color, and others.
- **Cache all API responses to `data/raw/*.json` and commit them** so the demo and deploy never depend on live APIs.

## First 2 hours (Builder)
1. (0:00–0:20) Get FalkorDB running (redis module or falkordblite). Smoke test: `GRAPH.QUERY t "RETURN 1"`. Scaffold the repo: `ingest/`, `api/`, `mcp_server/`, `web/`, `queries/`, `data/`.
2. (0:20–1:00) `ingest/fetch.py`: define the Acme fleet in `data/fleet.yaml` (about 20 services, each with 3–6 real deps at real versions, chosen so several are transitively exposed to the compromised packages). Fetch deps.dev graphs, npm maintainers, and OSV advisories, then cache them.
3. (1:00–1:30) `ingest/load.py`: load into graph `fleet:acme` with `MERGE` and indexes on `Package(name)`, `Service(name)`, and `Version(name,version)`. Check in a REPL that `MATCH p=(s:Service)-[*1..6]->(:Package {name:'debug'}) RETURN s.name, length(p)` returns multiple services with depths > 1.
4. (1:30–2:00) Write the first 3 tools as functions plus a FastAPI endpoint: `resolve_entity`, `exposure(package, bad_version, depth)` returning paths via `nodes(path)`, and `who_to_wake(service)`. Return JSON `{outcome, rows, paths, trace}`, where `outcome` is one of `answered_with_path | answered_from_memory | not_in_graph | unknown_entity`.
Then: UI graph view and trace panel, then fix generation and Incident write-back, then PageRank SPOF, then the MCP server, then deploy, then README and video.

## Explicit exclusions
No LLM-generated Cypher. No GitHub auth or scanning private repos. No ecosystems other than npm. No user accounts. No real notifications sent (drafts only). No GraphRAG over documents.

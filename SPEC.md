# SPEC: PatientZero, a supply-chain incident agent on FalkorDB
**Status: FROZEN v1, Thu Oct 15 2026, 11:58 PM ET.** Owner: Research/Product. The Builder (FullStack) owns code and makes final scope calls to hit the deadline. After this freeze, changes go into a "Changelog" section at the bottom instead of rewrites.

---
## ⏰ Deadline, freeze, artifacts (read first)
| | Time |
|---|---|
| **Submissions close** | **Sat Oct 18, 11:59 PM IST = Sat Oct 18, 2:29 PM ET (18:29 UTC)** |
| **Code freeze (internal)** | **Sat Oct 18, 8:00 AM ET**. After this, only bug fixes, README, video, and deploy |
| Video recorded by | Sat Oct 18, 11:00 AM ET |
| Submitted by | Sat Oct 18, 1:30 PM ET (one hour of buffer) |

**Required submission artifacts** (rule 10, https://www.wemakedevs.org/hackathons/falkordb/rules):
1. Public source-code repository
2. README with setup instructions **and the graph data model**
3. The **Cypher queries and graph algorithms** the product depends on (`queries/*.cypher`, linked from the README)
4. **Demo video** of the working project
5. **Live deployment** (preferred), or complete local setup instructions
6. **Track(s) entered**: Track 1 "Agents That Act on Connected Data" (primary). Optionally also Track 2 (the rules allow several; Atharv decides at submit time)
7. Also required by rule 11: **disclose AI coding assistant use** in the README.

Hard rule 3: if the product still works with FalkorDB removed, it's disqualifying decoration. Every core answer must come from a Cypher traversal or `algo.*` call.

---
## 1. Target user and concrete problem
**User:** a security or platform engineer at a company running 10–100 JavaScript services, at the moment an npm supply-chain alert drops.

**Problem:** On **Sept 8 2025**, the npm account of maintainer **Josh Junon (npm `qix`, GitHub `Qix-`)** was phished through a fake "2FA reset" email from `support@npmjs.help`. The attacker published malicious versions of **18 packages**, including `chalk`, `debug`, and `ansi-styles`, with roughly 2 billion weekly downloads combined. The payload was a browser crypto-wallet drainer.

Every engineering org then had to answer, by hand:
- Which of our services can pull these versions?
- Through which chain?
- Who owns them?
- What do we pin?

`npm audit` works one repo at a time and only after advisories land. It can't explain the transitive path across a whole fleet, and it can't tell you which maintainer is your single point of failure. Those are **multi-hop graph questions**.

## 2. One-sentence pitch
**PatientZero is an agent that, the moment an npm maintainer is compromised, traverses your whole fleet's dependency graph in FalkorDB to show which services are exposed, the exact chain that exposes each one, who to wake, and the ready-to-merge fix, and it remembers the incident next time.**

## 3. Judge lens (why this wins Track 1)
Judging criteria are **not published**. These are assumptions taken from the organiser blog: https://www.wemakedevs.org/blogs/how-to-build-an-agent-that-reasons-over-a-graph
- **The graph does the work:** exposure is a variable-length path plus a semver check on the last edge. Single point of failure is reach or centrality over Maintainer→Package→Service. Neither is possible without the graph.
- **Track 1 checklist, verbatim:** multi-hop reasoning ✓, graph queries and algorithms as agent tools ✓, tool selection through MCP ✓, actions based on what the graph shows (fix plus notification drafts plus an Incident written back) ✓, explainable results (path on every row) ✓.
- **The two moves the blog recommends:**
  - The depth 1 → depth 4 contrast that reveals hidden exposure.
  - An honest "not in graph" or "unknown" answer.
- **Beats the organiser's own "Blast Radius" demo:** real public data, a real incident replay, a real action, and nuance ("express pins `debug@2.6.9` exactly, so it's NOT exposed").
- **Trust:** the agent never writes Cypher. It uses a fixed, parameterised tool catalogue, and entities are resolved against the graph first.

## 4. Verified incident data (Sept 8 2025 npm compromise)
Phished maintainer: **Josh Junon, npm username `qix`** (https://www.npmjs.com/~qix), GitHub `Qix-`. Sources:
- Qix-'s own statement listing "All affected packages" in debug-js/debug#1005: https://github.com/debug-js/debug/issues/1005
- GHSA-4x49-vf9v-38px
- Socket's write-up "npm author qix compromised": https://socket.dev/blog/npm-author-qix-compromised-in-major-supply-chain-attack

Every version below was confirmed in OSV on 2026-10-15 via `POST https://api.osv.dev/v1/query`. Each one is now **unpublished** from the registry; its `time` entry remains, but it is missing from `versions`.

| # | Package | Malicious version | OSV MAL id | GHSA / CVE (where present) | Fixed / clean version | Current npm maintainers (today) |
|---|---|---|---|---|---|---|
| 1 | debug | 4.4.2 | MAL-2025-46974 | GHSA-4x49-vf9v-38px / CVE-2025-59144 | 4.4.3 | qix, tootallnate |
| 2 | chalk | 5.6.1 | MAL-2025-46969 | GHSA-2v46-p5h4-248w | 5.6.2 | sindresorhus |
| 3 | ansi-styles | 6.2.2 | MAL-2025-46967 | GHSA-p5rr-crjh-x7gr | 6.2.3 | sindresorhus |
| 4 | strip-ansi | 7.1.1 | MAL-2025-46980 | GHSA-vfjc-p7x3-q864 | 7.1.2 | sindresorhus |
| 5 | ansi-regex | 6.2.1 | MAL-2025-46966 | GHSA-jvhh-2m83-6w29 | 6.2.2 | sindresorhus |
| 6 | supports-color | 10.2.1 | MAL-2025-46981 | GHSA-pj3j-3w3f-j752 | 10.2.2 | sindresorhus |
| 7 | wrap-ansi | 9.0.1 | MAL-2025-46983 | GHSA-2rv4-jp6r-xgq7 | 9.0.2 | sindresorhus |
| 8 | slice-ansi | 7.1.1 | MAL-2025-46979 | GHSA-9xjj-cmqc-578p | 7.1.2 | sindresorhus |
| 9 | chalk-template | 1.1.1 | MAL-2025-46970 | GHSA-3jjr-pvq7-4jq5 | 1.1.2 | sindresorhus |
| 10 | supports-hyperlinks | 4.1.1 | MAL-2025-46982 | GHSA-hggr-35mp-qcxg | 4.1.2 | sindresorhus |
| 11 | has-ansi | 6.0.1 | MAL-2025-46976 | GHSA-jff9-gjh4-j359 | 6.0.2 | sindresorhus |
| 12 | color-convert | 3.1.1 | MAL-2025-46971 | GHSA-pxx3-g568-hxr4 / CVE-2025-59162 | 3.1.2 | qix |
| 13 | color-name | 2.0.1 | MAL-2025-46972 | GHSA-5fvm-p68v-5wmh / CVE-2025-59145 | 2.0.2 | dfcreative, moox, dy |
| 14 | color-string | 2.1.1 | MAL-2025-46973 | GHSA-286p-vc9p-p5qv / CVE-2025-59142 | 2.1.2 | qix |
| 15 | color | 5.0.1 | MAL-2025-46985 | GHSA-qrmh-qg46-72pp / CVE-2025-59143 | 5.0.2 | qix |
| 16 | is-arrayish | 0.3.3 | MAL-2025-46977 | GHSA-frh7-2f84-v9mw / CVE-2025-59331 | 0.3.4 | qix |
| 17 | simple-swizzle | 0.2.3 | MAL-2025-46978 | GHSA-9g9j-rggx-7fmg / CVE-2025-59141 | 0.2.4 | qix |
| 18 | error-ex | 1.3.3 | MAL-2025-46975 | GHSA-6jp5-hh4c-8c5h / CVE-2025-59330 | 1.3.4 | qix, sindresorhus |
| 19 | backslash | 0.2.1 | MAL-2025-46968 | GHSA-53mq-f4w3-f7qv / CVE-2025-59140 | 0.2.2 | qix |

Notes:
- Qix-'s own list names 18 packages. `error-ex@1.3.3` was added later (he says he missed it in the issue thread on Sept 15). That makes **19 qix packages** in OSV.
- Some GHSA ids differ between the per-repo advisory and the global malware advisory. For example, debug has both GHSA-4x49-vf9v-38px and GHSA-8mgj-vmr8-frr6. **Store OSV MAL ids as the primary key** and keep any aliases you get from the OSV `aliases` field.
- The same campaign also hit `proto-tinker-wc@0.1.87` (MAL-2025-46984, a different maintainer) and the duckdb and prebid packages the next day. **Excluded** from the qix replay; it can be mentioned as an extension.
- ⚠️ **Modelling gotcha:** the npm registry's *current* `maintainers` field does **not** show `qix` on the chalk family, because Sindre removed him after the attack. You **must not** derive qix's reach from today's registry. Load a curated file, `data/incidents/2025-09-08-qix.json`, containing the 19 rows above with `source` URLs. Create `(:Maintainer {npm:'qix'})-[:HAD_PUBLISH_RIGHTS {asOf:'2025-09-08', source:'https://github.com/debug-js/debug/issues/1005'}]->(:Package)`. Current maintainers can still be loaded as `MAINTAINS` for the single-point-of-failure ranking.

## 5. Core user journey (one complete loop)
1. **Fleet loaded:** the home screen shows the "Acme" fleet in graph `fleet:acme`. It has 15–20 synthetic services, each with real npm direct dependencies at real versions, and transitive graphs loaded from deps.dev.
2. **Alert:** the user types `maintainer qix was phished` (or `GHSA-4x49-vf9v-38px`, or `debug@4.4.2`).
3. **Resolve:** the agent resolves the entity against the graph. If it's unknown, it returns `unknown_entity` and runs no further tools.
4. **Investigate:** the agent runs its tools in this order: `incident_scope` (the 19 bad versions), then `exposure` (paths), then `who_to_wake`. The trace panel shows each call.
5. **See:** a ranked list of exposed services. Each row has its path, rendered as a graph and as text, e.g. `img-service → sharp@0.33.5 → color@4.2.3 → color-string@1.9.1 → simple-swizzle ^0.2.2 ⚠ 0.2.3`. There's a **depth control**, and **non-exposed paths are shown with the reason** (e.g. `express → debug 2.6.9 (exact): range excludes 4.4.2`).
6. **Act:** clicking **Generate fix** produces, per service:
   - an npm `overrides` JSON pinning each bad package to its clean version from the table;
   - a notification draft to the owner team and on-call person, citing path + MAL/GHSA id.

   It writes an `(:Incident)` node and `[:EXPOSED {path}]` edges back to the graph.
7. **Prevent:** `who is our single point of failure?` returns maintainers and packages ranked by how many fleet services they can reach (algorithm-backed).
8. **Remember:** after a server restart and a new session, `qix` returns `answered_from_memory` with the incident date, counts, and patch, read from the graph.

## 6. Must-have features (max 3)
1. **Exposure triage with paths and semver truth.**
   - Inputs: maintainer, advisory id, or `package@version`.
   - The agent finds every fleet service with a dependency path to an affected package, up to depth N (1–6).
   - A path is **exposed** only if the requirement range on its *last edge* is satisfied by the malicious version, using npm semver semantics.
   - Returns paths (`nodes(path)`), owners, on-call, and a count.
2. **Explainable action plus incident memory.**
   - Per-service `overrides` patch and notification draft, each citing its path and advisory.
   - Persists `Incident`, `EXPOSED`, and `Finding` nodes in FalkorDB.
   - The next session recalls them before traversing (outcome `answered_from_memory`).
3. **Agent with a fixed tool catalogue, over web UI and MCP.**
   - A deterministic intent router with no LLM required.
   - The same tools are exposed through an MCP server.
   - The web UI is one page with chat, a trace panel (tool, args, ms, rows), a path graph (cytoscape.js), and a fix panel.
   - Honest outcomes are always explicit.

**Outcome enum (exact strings):**
- `answered_with_path`: the graph held the relationships, and the paths are shown.
- `answered_from_memory`: a stored Incident or Finding answered it, with its date.
- `not_in_graph`: the entity exists in the graph, but no exposure path connects it to the fleet at this depth (or no range admits the bad version).
- `unknown_entity`: the name/id isn't in the graph, so no traversal runs.

### Tool catalogue (human-written Cypher, parameterised, args validated against the graph)
| Tool | Args | Does |
|---|---|---|
| `resolve_entity` | `text` | Maps input to a Maintainer, Advisory, Package, or Version node, or returns `unknown_entity` |
| `incident_scope` | `maintainer` or `advisory_id` | Returns the bad Versions in scope, with clean versions |
| `exposure` | `package, bad_version, depth≤6` (or a scope list) | Variable-length path Service→…→Package, last-edge requirement, and an exposed flag |
| `who_to_wake` | `service` | Team, on-call person, and service tier |
| `single_points_of_failure` | `limit≤20` | Maintainers and packages ranked by fleet reach, plus `algo.pageRank` on the package graph |
| `path_between` | `service, package` | Shortest path via `algo.SPpaths` (or `shortestPath`) |
| `remediation_plan` | `incident_id` | Builds the overrides and drafts, and writes the Incident/EXPOSED/Finding nodes |
| `recall` | `entity` | Prior Incidents and Findings with dates |

Build note: confirm the exact signatures of `algo.pageRank` and `algo.SPpaths` against https://docs.falkordb.com/algorithms for the module version you run (v6.0.1). The blog's examples are `CALL algo.pageRank('Service','DEPENDS_ON')` and `CALL algo.SPpaths({...})`. If an algorithm misbehaves, fall back to a reach-count query and keep the algorithm call for PageRank only.

## 7. Graph model (v1)
```
(:Service {name, tier, repo})-[:DEPENDS_ON {requirement}]->(:Version {pkg, version})   // direct deps (resolved)
(:Package {name})-[:HAS_VERSION]->(:Version {pkg, version, unpublished:bool})
(:Version)-[:REQUIRES {requirement}]->(:Version)          // deps.dev resolved edge: fromNode->toNode, requirement kept
(:Maintainer {npm})-[:MAINTAINS]->(:Package)              // current registry
(:Maintainer {npm})-[:HAD_PUBLISH_RIGHTS {asOf, source}]->(:Package)   // curated incident file
(:Advisory {id, aliases, summary, published, url})-[:AFFECTS]->(:Version {unpublished:true})
(:Team {name})-[:OWNS]->(:Service); (:Person {name, handle})-[:MEMBER_OF]->(:Team); (:Person)-[:ONCALL_FOR]->(:Team)
(:Incident {id, at, trigger, depth, exposed_count})-[:EXPOSED {path, via_requirement}]->(:Service)
(:Finding {text, at, session})-[:ABOUT]->(:Service|:Package|:Maintainer)
```
**Exposure query (core):**

```
MATCH p = (s:Service)-[:DEPENDS_ON|REQUIRES*1..$depth]->(v:Version {pkg:$pkg})
WITH s, p, last(relationships(p)) AS r
RETURN s.name, [n IN nodes(p) | coalesce(n.name, n.pkg+'@'+n.version)] AS via, r.requirement AS req, length(p) AS hops
```

The app then checks `satisfies($bad_version, req)`, using the Python `node-semver` package (`nodesemver`) for npm semantics.

Note: resolved nodes point at the version resolved *today*. Exposure is decided by the **range on the edge into the affected package**, not by the resolved version. That's the "a fresh install during the attack window would have pulled it" rule. State this assumption in the README.

Indexes: `CREATE INDEX FOR (p:Package) ON (p.name)`, `CREATE INDEX FOR (v:Version) ON (v.pkg)`, plus `Service(name)` and `Maintainer(npm)`.

## 8. Recommended stack and data sources
- **FalkorDB v6.0.1** module on redis-server, with no Docker. Start it with `scripts/start-falkordb.sh` (it already exists in the repo and works: "Module 'graph' loaded"). Graph name: `fleet:acme`. Multi-tenant bonus, if time allows: `fleet:<tenant>`.
- **Backend:** Python 3.13, FastAPI, uvicorn, `falkordb` (PyPI), `httpx`, `node-semver`, and `mcp` (FastMCP) for the MCP server (stdio). Use `ro_query` for every read tool.
- **Frontend:** a single page (React+Vite or a plain static page served by FastAPI), **cytoscape.js** for the paths, and Tailwind or simple CSS.
- **No LLM required.** The intent router uses regex and keywords:
  - maintainer names
  - `GHSA-`, `MAL-`, `CVE-` ids
  - `pkg@ver`
  - "single point of failure", "who", and "why" phrases

  *Optional (assumption, off by default):* an `LLM_API_KEY` for an OpenAI-compatible endpoint, used only to choose a tool and phrase one sentence. The sentence is rejected if it names any package or service not in the result.
- **Deployment:** FalkorDB Cloud free tier (100 MB, no card; https://docs.falkordb.com/cloud/tiers/free), loaded with the same ingest, plus the API and UI on any free host (Render, Railway, or Fly). Fallback: complete local setup instructions plus the video (allowed by the rules).
- **Data sources:** public, no auth, verified Oct 15 2026. **Cache everything to `data/raw/` and commit it.**
  - deps.dev v3 resolved dependency graph: `https://api.deps.dev/v3/systems/npm/packages/{urlencoded pkg}/versions/{ver}:dependencies` (nodes plus edges with `requirement`). Docs: https://docs.deps.dev/api/v3/
  - OSV: `POST https://api.osv.dev/v1/query` and `GET https://api.osv.dev/v1/vulns/{id}`. Docs: https://google.github.io/osv.dev/api/
  - npm registry: `https://registry.npmjs.org/{pkg}` (`maintainers`, `time`, `versions`). An unpublished version appears in `time` but not in `versions`.
  - Incident ground truth: https://github.com/debug-js/debug/issues/1005 and the GHSA pages (table above).
- **Suggested fleet** (real direct deps; exposure paths checked on deps.dev today). Builder: final picks are yours.
  - `img-service`: sharp@0.33.5. Path: sharp → color ^4.2.3 → color-string ^1.9.0 → simple-swizzle **^0.2.2 ⚠ 0.2.3** → is-arrayish **^0.3.1 ⚠ 0.3.3**. This is the deep path, 4–5 hops.
  - `cli-tools`: ora@8.1.0. ora → chalk **^5.3.0 ⚠ 5.6.1**; strip-ansi **^7.1.0 ⚠ 7.1.1**. Counter-example: strip-ansi → ansi-regex ^6.2.2 is **NOT** exposed to 6.2.1.
  - `ci-runner`: lint-staged@15.2.10. chalk ~5.3.0 is **safe**, debug ~4.3.6 is **safe**, wrap-ansi **^9.0.0 ⚠ 9.0.1**, slice-ansi **^7.1.0 ⚠ 7.1.1**.
  - `lint-service`: eslint@9.9.0. eslint → debug **^4.3.2 ⚠ 4.4.2**.
  - `test-harness`: mocha@10.7.3 → debug **^4.3.5 ⚠**. Also jest@29.7.0 → @babel/core → debug **^4.1.0 ⚠** (depth 2–3).
  - `realtime-gateway`: socket.io@4.7.5. socket.io-adapter → debug **~4.4.1 ⚠ 4.4.2**, while socket.io → debug ~4.3.2 is safe. Mixed result, which demos well.
  - `checkout-api`: express@4.21.2 → debug **2.6.9 exact: NOT exposed**. This is the honest negative.
  - `web-frontend`: next@14.2.5 has no affected packages, so it's clean.
  - `build-service`: webpack@5.94.0 → jest-worker → supports-color ^8.0.0, which is safe from 10.2.1.
  - Add 6–10 more services so the fleet looks real. Owners, teams, and on-call people are synthetic.

## 9. Acceptance criteria (Builder-testable)
Run them against a freshly seeded graph `fleet:acme`. Each AC has an id for the reviewer.

| ID | Input / action | Expected |
|---|---|---|
| AC1 | `scripts/start-falkordb.sh` then `redis-cli GRAPH.QUERY fleet:acme "MATCH (s:Service) RETURN count(s)"` | Returns ≥15 |
| AC2 | Graph sanity: `MATCH (a:Advisory)-[:AFFECTS]->(v:Version) RETURN count(DISTINCT v)` | 19 (the table in §4) |
| AC3 | `MATCH (:Maintainer {npm:'qix'})-[:HAD_PUBLISH_RIGHTS]->(p) RETURN count(p)` | 19, with a `source` property on every edge |
| AC4 | Ask `maintainer qix was phished` (UI and `POST /api/ask`) | `outcome=answered_with_path`. ≥5 exposed services, each with a non-empty `via` path list whose last hop is one of the 19 packages, plus the requirement string. The trace lists `resolve_entity`, `incident_scope`, `exposure`, `who_to_wake` with ms and row counts |
| AC5 | Same query at depth=1, then depth=4 (UI control or `depth` param) | The exposed count at depth 4 is **strictly greater** than at depth 1. `img-service` (via sharp → … → simple-swizzle) appears only at depth ≥3 |
| AC6 | Ask `debug@4.4.2` | `checkout-api` is **not** listed as exposed. If shown, it's marked safe with reason `debug 2.6.9 (exact) does not satisfy 4.4.2`. `lint-service` is exposed |
| AC7 | Ask `left-pad@1.3.0` (or `GHSA-xxxx-xxxx-xxxx`) | `outcome=unknown_entity`. Trace shows only `resolve_entity`, with no traversal and no invented services |
| AC8 | Ask about a package that's in the graph but where no fleet range admits the bad version (e.g. `ansi-regex@6.2.1` if the only edges are `^6.2.2`, or whatever the seeded data gives) | `outcome=not_in_graph`, with the sentence "No fleet path admits this version at depth N", which is distinct from "safe" |
| AC9 | Click **Generate fix** after AC4 | For every exposed service: valid JSON `{"overrides": {"<pkg>": "<clean version>"}}` using clean versions from §4 (e.g. `"debug": "4.4.3"`), plus a draft message naming the owner team, on-call person, path, and MAL/GHSA id. Graph check: `MATCH (i:Incident)-[e:EXPOSED]->(s) RETURN count(e)` equals the exposed count |
| AC10 | Restart the API **and** redis (persistence enabled via `--save`), open a new session, ask `qix` | `outcome=answered_from_memory`, with the incident date, exposed count, and a link or button to re-run a fresh traversal |
| AC11 | Ask `who is our single point of failure?` | A ranked list (≥5 rows) of maintainers or packages with fleet-reach counts. The trace shows an `algo.*` call or the reach query. `qix` or `sindresorhus` rank in the top 3 |
| AC12 | Ask `why is img-service connected to is-arrayish?` | `path_between` returns the hop list (sharp → color → color-string → simple-swizzle → is-arrayish), drawn in the graph view |
| AC13 | MCP: run the MCP server over stdio and list its tools (MCP Inspector `npx @modelcontextprotocol/inspector`, or a Python `mcp` client script in `scripts/`) | Lists the 8 tools in §6. Calling `exposure` with `{"package":"debug","bad_version":"4.4.2","depth":4}` returns the same rows as the API |
| AC14 | Kill redis, then ask anything | A clear error message ("graph unavailable"). The product doesn't answer from anywhere else, which proves FalkorDB is load-bearing |
| AC15 | Response time | `exposure` across the whole fleet at depth 4 takes <500 ms on the box, as shown in the trace |
| AC16 | README | It contains: setup (no-Docker and Cloud), a graph model diagram/table, a link to `queries/*.cypher` listing every query and algorithm, the exposure-by-range assumption, the track(s), an AI-assistant disclosure, the data sources with URLs, a demo video link, and a deployment link |

## 10. Explicit exclusions
- No LLM-generated Cypher, ever. No LLM is required for any acceptance criterion.
- npm only. No PyPI, Maven, etc. No lockfile upload or GitHub scanning in v1 (stretch only if all ACs pass: paste a `package.json` to add a service).
- No auth, no user accounts, no real notifications sent (drafts only).
- No GraphRAG over documents, no vector search.
- No malware analysis or payload detection. Advisories are the ground truth.
- No campaigns other than the qix incident in the demo (proto-tinker-wc, duckdb, and prebid are mentioned as extensions).
- No personal data: maintainers appear only as public npm usernames. Fleet people and teams are synthetic.

## 11. Two-minute demo script
| Time | Screen | Voiceover |
|---|---|---|
| 0:00–0:12 | Headline about the Sept 8 2025 npm attack, then the PatientZero home with the fleet graph | "Sept 8, 2025: one phished npm maintainer, 18 packages, 2 billion weekly downloads. Every company had to answer: are we hit, and how? PatientZero answers that in seconds, using FalkorDB." |
| 0:12–0:30 | Type `maintainer qix was phished`. The trace panel fills: resolve, incident_scope, exposure, who_to_wake | "The agent never writes queries. It picks from human-written Cypher tools and resolves every name against the graph first." |
| 0:30–0:50 | Depth control at 1 shows N services; drag to 4 shows M services. `img-service` lights up via sharp → color → color-string → simple-swizzle | "At one hop we see the obvious ones. At four hops the graph finds img-service, exposed through a chain nobody on the team would have guessed." |
| 0:50–1:05 | Click `checkout-api`: debug 2.6.9 (exact), safe | "It's precise, not paranoid. Express pins debug exactly, so the malicious 4.4.2 could never resolve. The semver range on the last edge decides." |
| 1:05–1:25 | Generate fix: overrides JSON and the Slack-style draft to the owner and on-call, each citing path and GHSA | "Then it acts: a ready-to-merge overrides patch per service, and a page to the right on-call engineer, each line traced to a graph path and an advisory." |
| 1:25–1:40 | Ask `who is our single point of failure?` and see the PageRank/reach ranking | "And it's proactive: graph algorithms rank which maintainer account, if phished, would hit most of our fleet." |
| 1:40–1:52 | Restart, new session, ask `qix` and get `answered_from_memory`. Then ask `left-pad@1.3.0` and get `unknown_entity` | "It remembers the incident across sessions, and it tells you honestly when something isn't in the graph." |
| 1:52–2:00 | The same `exposure` tool called from Claude Desktop or Cursor over MCP, then the repo URL | "The same tools work over MCP. PatientZero: FalkorDB as the context your security agent acts on." |

## 12. Submission checklist (complete by Sat Oct 18, 1:30 PM ET)
- [ ] Repo is public, with a clean commit history (no secrets, no `.venv`, no `.falkordb/*.so`, no dump files)
- [ ] README: pitch, screenshot/GIF, setup (no-Docker script plus Cloud), graph model, `queries/*.cypher` link, algorithms used, the exposure-by-range assumption, data sources with URLs, track(s), AI-assistant disclosure, video link, live URL
- [ ] `queries/` holds every Cypher query the tools use, one file per tool
- [ ] `data/raw/` is cached and committed, so the demo runs offline. `data/incidents/2025-09-08-qix.json` lists sources
- [ ] Demo video ≤ ~2 min (YouTube unlisted or Loom), with the §11 script
- [ ] Live deployment URL works from an incognito browser, or the local setup is verified from a clean clone
- [ ] Track(s) named in the submission form
- [ ] Atharv can explain the graph model, the exposure query, the semver rule, and the architecture (rule 12)
- [ ] Optional: blog post for the AirPods side prize (we can repurpose the README and §1)

## Changelog
- v1 (2026-10-15 23:58 ET): frozen. Replaced the brief's placeholder demo numbers with real deps.dev-checked paths. Verified the 19 compromised versions and the maintainer `qix`. Added the `HAD_PUBLISH_RIGHTS` curated edge. Expanded the outcomes and the tool catalogue.

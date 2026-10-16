# Contract: MCP tool selection, live OSV watch, concurrency benchmark

For the frontend. Owner: backend/app/mcp_agent, backend/app/watch, backend/app/bench (queries/watch.cypher,
queries/bench.cypher). All endpoints accept `?tenant=` (or `X-Tenant`) where noted; unknown tenants are 400.

## 1. MCP tool selection (visible in /api/ask and /api/fix)
The agent is an MCP client of our own MCP server (`app/mcp_server.py`, same object that `python -m app.mcp_server`
serves over stdio). The deterministic router runs inside an MCP chain (`app/mcp_agent/gateway.py`): every graph tool
it picks is a separate tools/call on one shared in-memory MCP session for the request (tools/list cached 5 min).

Intent to MCP tool (`mcp.tool`; `mcp.tools` is the full chain, in call order):

| Intent | Typical chain (`mcp.tools`) | `mcp.tool` |
|---|---|---|
| exposure (maintainer, advisory, pkg@ver, campaign) | resolve_entity, recall, incident_scope, exposure, who_to_wake | exposure |
| exposure via "the maintainer of chalk" | resolve_entity, package_maintainers, recall, incident_scope, exposure, who_to_wake | exposure |
| who_to_wake (service) | resolve_entity, who_to_wake | who_to_wake |
| who maintains a package | resolve_entity, package_maintainers | who_to_wake |
| path_between | resolve_entity, path_between | path_between |
| single_points_of_failure | resolve_entity, single_points_of_failure | single_points_of_failure |
| recall ("qix" in a new session, "show past incidents") | resolve_entity, recall | recall |
| resolve (nothing matched) | resolve_entity | resolve_entity |

Agent-only bulk arguments (hidden from the LLM's function schemas): `exposure(scope=[incident_scope rows], depth)`
returns the per-service findings `{exposed, safe, clean, fleet_size}` with the fleet read server side,
`who_to_wake(services=[...])`, `recall(key=...)` and `recall(limit=N)` (latest incidents). `incident_scope` also takes
`campaign`, `package`+`bad_version` and `packages`. New tool `package_maintainers(package)`. Left in process on purpose:
`remediation_plan` (writes, behind the UI's explicit Generate fix) and the fuzzy maintainer ranking inside the router.
`investigate` (the whole question in one composite call) is only the fallback: when a chained tool errors, or when the
request carries route options the chain cannot carry.

Response fields:
```jsonc
"tool_via": "mcp",                        // "mcp" | "direct" (some tool fell back) ; absent only with MCP_AGENT=off
"mcp": {"via": "mcp", "transport": "memory", "intent": "exposure", "tool": "exposure",
        "tools": ["resolve_entity", "recall", "incident_scope", "exposure", "who_to_wake"], "calls": 5,
        "ms": 20.7, "wall_ms": 23.5}
        // fallback: {"via": "mcp", "tool": "investigate", "tools": ["investigate"], "fallback": "<reason>", ...}
        // MCP down: {"via": "direct", ...} and each affected step says via "direct"
        // LLM mode: {"via", "transport", "intent", "tool", "tools": every MCP call in the trace,
        //            "llm_tools": the ones the model chose, "calls", "chosen_by": "llm", "ms"}
```
Each MCP call is its own trace row, followed by the Cypher steps that tool ran server side. The first trace step of a
deterministic answer is always the `resolve_entity` MCP call:
```jsonc
{"tool": "mcp", "via": "mcp", "mcp_tool": "exposure", "intent": "exposure", "transport": "memory",
 "params": {"scope": ["debug@4.4.2"], "depth": 4}, "ms": 6.1, "tools_listed": 13, "list_ms": 0,
 "note": "router intent 'exposure': MCP tools/call exposure over the memory transport (2 Cypher steps server side)",
 "query": null, "cypher": null, "rows": 0}
```
(`params.scope` is shown as `pkg@version` strings; at most 20 list items are shown.) In LLM mode each tool the model
picks appears as `{"tool": "mcp", "via": "mcp", "mcp_tool": "who_to_wake", ..., "note": "MCP tools/call who_to_wake
chosen by the LLM from N MCP tools"}` followed by that tool's own Cypher steps; the LLM trace opens with the
agent/LLM decision step (no tool runs there) and its first tool step is an MCP call. A session follow-up opens with the
session lookup (`sessions` steps), then the MCP chain. A fallback step has `"via": "direct"` and
`"mcp_fallback": "<reason>"`. `GET /health` has
`"mcp_agent": {"enabled", "active", "transport", "mcp_calls", "direct_fallbacks", "last_error", "last_ms"}`.
Write tools (`remediation_plan`, `crew_run`) are never offered to the LLM. Parity with `MCP_AGENT=off` (same answer,
field for field, except trace, timings and the mcp block) is tested in `backend/tests/test_mcp_parity.py`.

## 2. Live OSV watch
Real data only: OSV npm index head (https://osv-vulnerabilities.storage.googleapis.com/npm/modified_id.csv, Range +
ETag) and records from api.osv.dev. New MAL-* advisories are ingested into every tenant graph (acme, globex) as
`(:Advisory {campaign:'osv-watch'})-[:AFFECTS]->(:Version {malicious:true, unpublished:true})` (all-versions malware:
`-[:AFFECTS_PACKAGE]->(:Package)`), checked with the exposure traversal at depth 6 plus npm semver, and recorded as
`(:WatchEvent)-[:ABOUT]->(:Advisory)`, `-[:HITS]->(:Service)`. Background poll every 10 min, max 200 advisories per poll.

### GET /api/watch/feed?tenant=acme&limit=50
```jsonc
{"tenant": "acme", "graph": "fleet:acme",
 "events": [ {                                   // newest first
   "id": "P-20261016T150357-3f9c:acme:MAL-2026-17896:xapi-lib", "ts": "2026-10-16T15:03:57.900106Z",
   "poll_id": "P-...", "tenant": "acme", "advisory": "MAL-2026-17896", "aliases": ["GHSA-..."],
   "package": "xapi-lib", "bad_versions": ["88.2.3", "90.3.1"], "all_versions": false,
   "verdict": "not_hit",            // "hit" | "reachable_not_admitted" | "not_hit"
   "exposed_services": [], "exposed_count": 0, "reachable_services": [], "fleet_size": 22, "depth": 6,
   "replay": false,                 // true: a REPLAY of a past advisory, label it as such
   "published": "2026-10-13T07:02:31Z",          // OSV published time of the advisory
   "modified": "2026-10-16T01:45:13Z",           // OSV modified time (why it showed up in the index now)
   "first_seen_by_watch": "2026-10-16T15:03:58Z", // when this watch first ingested it (null for replays of curated ones)
   "published_age_s": 290000, "published_ago": "3 days ago",
   "age_label": "published 3 days ago; first seen by watch at 2026-10-16 15:03 UTC",
   "summary": "Malicious code in xapi-lib (npm)",
   "url": "https://osv.dev/vulnerability/MAL-2026-17896",
   "reason": "no service depends on xapi-lib within 6 hops", "check_ms": 4.1 } ],
 "polls": [ {"id": "P-...", "ts": "...", "source": "scheduled|manual", "checked": 25, "new_advisories": 25,
             "already_known": 0, "index_entries": 1402, "hits": 0, "ms": 807.4, "errors": 0,
             "published_oldest": "2026-09-03T00:00:00Z", "published_newest": "2026-10-16T06:55:02Z",
             "note": "checked 25 advisories first seen by the watch (OSV published 2026-09-03 to 2026-10-16) against 2 tenant fleet(s)"} ],
 "last_poll": { ...same as polls[0] or null },
 "counts": {"events": 26, "hits": 1, "replays": 5, "replayed_advisories": 1},
 "running": false, "loop": {"enabled": true, "interval_s": 600, "next_at": 1760627000.0}, "trace": [...]}
```
Replays are collapsed: one row per replayed advisory and package (its latest replay) with `replay_count`,
`first_replayed` and `last_replayed`, so pressing Replay again updates that row instead of adding an identical one;
replay rows carry `age_label` "replay of a past advisory published 403 days ago (2025-09-08); replayed 5 times, last
at ...". "New" means new to the watch, not newly published: OSV lists an advisory when it is added or modified, and
many are days or weeks old. Suggested copy: "Checked {last_poll.checked} npm malware advisories first seen by the
watch, published {published_oldest} to {published_newest}: not hit", and per row `age_label` (never a "new" badge on
an advisory published more than a day before `first_seen_by_watch`). For SSE `watch` events of a replay, replace the
row with the same advisory and package rather than prepending a second one.

### GET /api/watch/stream?tenant=acme (SSE)
`event: hello` (data: `{tenant, last_poll, loop}`), then `event: watch` per new WatchEvent for that tenant (same
fields as feed events plus `paths: [{service, via[], requirement, reason}]` for hits), `event: poll` per finished poll
or replay (data: `{poll: {...}}`), and `: ping` every 15 s. Streams close after 15 min; EventSource reconnects.

### POST /api/watch/replay  body `{"advisory": "debug@4.4.2"}` (or GHSA-4x49-vf9v-38px, chalk@5.6.1, a MAL id)
Public, 6 per minute per client (429 + Retry-After). Re-plays a past real advisory through the same pipeline
(record fetched live from OSV, fallback: committed copy) for every tenant. Response:
```jsonc
{"id": "R-...", "replay": true, "source": "replay", "record_source": "osv-live", "advisory": "MAL-2025-46974",
 "query": "debug@4.4.2", "checked": 1, "hits": 2, "ms": 233,
 "note": "REPLAY of a past real advisory (MAL-2025-46974, published 2025-09-08T14:26:51Z); not a new alert",
 "events": [ {"tenant": "acme", "verdict": "hit", "exposed_count": 10, "fleet_size": 22, "replay": true,
              "paths": [{"service": "...", "via": ["svc", "pkg@x", "debug@4.4.1"], "requirement": "^4.3.4", "reason": "..."}], ...},
             {"tenant": "globex", "verdict": "hit", "exposed_count": 5, "fleet_size": 12, ...} ]}
```
400 when the advisory is not in the graph.

Per-tenant backfill: every poll (also when OSV answers 304) checks advisories the watch already ingested in any
tenant graph that a tenant has no real verdict for (for example a tenant loaded after the poll that found them). They
are ingested into that tenant, checked against its fleet and recorded with a WatchPoll `source: "backfill"`
(`id` = `<poll id>-bf-<tenant>`); the poll summary has `backfilled: {tenant: n}`.

Clearing replays before a recording: `POST /api/admin/reset?tenant=acme&include_watch_replays=true` (admin) deletes
that tenant's replay WatchEvents and returns `watch_replays_deleted`; real verdicts are kept.
`CLEAR_REPLAYS=1 TENANT=acme sh scripts/reset-live.sh` does the same.

### POST /api/watch/poll (admin, X-Admin-Token) body `{"max_batch": 200}` optional
Runs one poll now; returns the poll summary (plus `watermark`, `remaining`, `fetch_errors`). 409 when busy.

### GET /api/watch/status
`{running, loop, last_poll, last_error, watermark, max_batch, interval_s, depth, index_url, streams_open}`.

## 3. Concurrency benchmark
Two different numbers, never to be confused: the in-process database benchmark (`last`, for example 740.6 ops/s on
live) and end-to-end HTTP (`http`, about 50 req/s at 30 clients on live). Show `last.scope.summary` next to the
in-process number.
### GET /api/bench/last (public)
```jsonc
{"numbers": {"in_process_ops_s": 740.6, "in_process_clients": 30, "http_req_s": 49.4, "http_clients": 30},
 "http": {"throughput_req_s": 49.4, "clients": 30, "duration_s": 20.44, "requests": 1009, "errors": 0,
          "p50_ms": 615.3, "p95_ms": 1010.5, "p99_ms": 1149.6, "at": "2026-10-16T15:32:16+00:00",
          "target": "live Railway deploy (...)", "what": "read-only HTTP: POST /api/ask ... and GET /api/fleet ...",
          "source": "docs/verification/concurrency.md, ..."},
 "last": {"scope": {"kind": "in_process_db",
                    "summary": "740.6 ops/s is measured in process, straight to FalkorDB (no HTTP): 30 FalkorDB connections (the deploy's own app process, 2 process(es)), 80% product reads over fleet:acme and 20% synthetic writes into the scratch graph bench:B-... (deleted afterwards, not product writes), with retry on FalkorDB queue-full (busy) rejections like the app. End to end over HTTP the live deploy serves about 49.4 req/s at 30 clients.",
                    "transport": "FalkorDB Python client to the database; no HTTP, no FastAPI, no MCP in the timed path",
                    "writes": "synthetic WatchEvent-shaped nodes in a scratch graph bench:<run>, deleted after the run",
                    "retry_busy": true},
          "id": "B-...", "label": "local|server", "at": "2026-10-16T15:24:11+00:00", "clients": 32, "procs": 4,
          "retry_busy": false, "duration_s": 20.0, "read_graph": "fleet:acme", "scratch_deleted": true,
          "overall": {"ops": 35481, "errors": 0, "throughput_ops_s": 1774.0, "p50_ms": 15.65, "p95_ms": 40.31,
                      "p99_ms": 54.87, "max_ms": 120.1, "mean_ms": 18.0},
          "reads": {...same keys}, "writes": {...}, "by_op": {"read_exposure_incident": {...}, "write_event": {...}},
          "events_written": 7033, "mix": {...}, "falkordb": {"redis_version": "8.0.2", "modules": ["graph@60001"]}},
 "history": [{"id", "label", "at", "clients", "duration_s", "overall"}], "running": null, "last_error": null}
```
`last` is null until a run is saved on that deploy.
### POST /api/bench/run (admin) body `{"clients": 30, "duration_s": 20, "write_pct": 20, "retry_busy": true}`
202 `{"status": "started", ...}`; capped at 50 clients and 30 s; 409 while one runs. Poll /api/bench/last.

## Timeline note (event kinds `watch` and `watch_replay`)
Watched advisories are no longer one `campaign` event: the timeline query excludes `campaign = 'osv-watch'` from the
campaign union, and `/api/timeline` (per tenant) emits two new kinds. The full set of kinds is now
`compromise | advisory | campaign | incident | playbook | watch | watch_replay`; update `TimelineEvent["kind"]`.

- `watch`: one event per watch poll that checked at least one new advisory. `count` = advisories checked in that poll,
  `title` carries this tenant's verdict, `items` = up to 8 `"package (MAL id): hit|not hit|no data"`, `ref` null.
  ```jsonc
  {"kind": "watch", "at": "2026-10-16T14:10:00Z", "count": 25,
   "title": "not hit: 25 advisories first seen by the watch, none reach this fleet (published 2026-09-03 to 2026-10-16)",
   // or "hit: 2 of 25 advisories first seen by the watch reach this fleet (published ...)"
   "items": ["bungoma (MAL-2026-17389): not hit", "..."], "ref": null}
  ```
  Suggested label: "Live OSV watch". Treat a title starting with `hit:` as an alert.
- `watch_replay`: one row per replayed advisory (its latest replay; `count` = how many times it was replayed), the
  latest 3 advisories, always labelled as a replay with the advisory's real publish date. `items` = `["debug@4.4.2"]`,
  `ref` = the OSV URL.
  ```jsonc
  {"kind": "watch_replay", "at": "2026-10-16T13:02:11Z", "count": 5,
   "title": "replay of MAL-2025-46974 (debug, published 2025-09-08): 10 of 22 services hit (replayed 5 times)",
   "items": ["debug@4.4.2"],
   "ref": "https://osv.dev/vulnerability/MAL-2025-46974"}
  ```
  Do not render it as "N new advisories"; it is a replay of a historical advisory, not new malware.

## Asking about all-versions malware
`/api/ask` about a watched package or MAL id where every published version is malicious (no single bad version) now
answers directly instead of "no compromised version recorded". The response gains `all_versions`
(`{packages: [..], advisories: [MAL ids], lead, message, services: [..], ...}`) and the message starts with
"Every published version of <pkg> is malicious (<MAL id>, OSV), so any dependency path is exposed." followed by the
usual exposure result (exposed services with paths, or "No fleet service depends on <pkg> within N hops, so none of
the M services is hit." with outcome `not_in_graph`).

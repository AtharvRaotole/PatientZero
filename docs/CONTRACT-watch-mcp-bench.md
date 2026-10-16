# Contract: MCP tool selection, live OSV watch, concurrency benchmark

For the frontend. Owner: backend/app/mcp_agent, backend/app/watch, backend/app/bench (queries/watch.cypher,
queries/bench.cypher). All endpoints accept `?tenant=` (or `X-Tenant`) where noted; unknown tenants are 400.

## 1. MCP tool selection (visible in /api/ask and /api/fix)
The agent is an MCP client of our own MCP server (`app/mcp_server.py`, same object that `python -m app.mcp_server`
serves over stdio). Per question it runs tools/list (cached 5 min) and tools/call over the in-memory MCP transport.

New fields on the ask/fix response:
```jsonc
"tool_via": "mcp",                        // "mcp" | "direct" (fallback) ; absent only with MCP_AGENT=off
"mcp": {"via": "mcp", "transport": "memory", "tool": "investigate", "intent": "who_to_wake",
        "args": {"question": "who to wake for auth-service", "depth": 4}, "ms": 3.4, "tools_listed": 12}
        // fallback: {"via": "direct", "tool": "investigate", "fallback": "<reason>"}
        // LLM path: "chosen_by": "llm" when the model picked investigate
```
The first trace step is the MCP call (render it as its own row, for example a small "MCP" badge):
```jsonc
{"tool": "mcp", "via": "mcp", "mcp_tool": "investigate", "intent": "exposure", "transport": "memory",
 "params": {"question": "...", "depth": 4}, "ms": 25.8, "tools_listed": 12, "list_ms": 0.5,
 "note": "router intent 'exposure' selected MCP tool investigate; tools/call over the memory transport (12 tools listed)",
 "query": null, "cypher": null, "rows": 0}
```
In LLM mode each tool the model picks appears as `{"tool": "mcp", "via": "mcp", "mcp_tool": "who_to_wake", ...,
"note": "MCP tools/call who_to_wake chosen by the LLM from N MCP tools"}` followed by that tool's own Cypher steps.
A fallback step has `"via": "direct"` and `"mcp_fallback": "<reason>"`. `GET /health` has
`"mcp_agent": {"enabled", "active", "transport", "mcp_calls", "direct_fallbacks", "last_error", "last_ms"}`.
Write tools (`remediation_plan`, `crew_run`) are never offered to the LLM.

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
   "published": "2026-10-16T07:02:31Z", "summary": "Malicious code in xapi-lib (npm)",
   "url": "https://osv.dev/vulnerability/MAL-2026-17896",
   "reason": "no service depends on xapi-lib within 6 hops", "check_ms": 4.1 } ],
 "polls": [ {"id": "P-...", "ts": "...", "source": "scheduled|manual", "checked": 25, "new_advisories": 25,
             "already_known": 0, "index_entries": 1402, "hits": 0, "ms": 807.4, "errors": 0,
             "note": "checked 25 new advisories against 2 tenant fleet(s)"} ],
 "last_poll": { ...same as polls[0] or null },
 "counts": {"events": 26, "hits": 1, "replays": 1},
 "running": false, "loop": {"enabled": true, "interval_s": 600, "next_at": 1760627000.0}, "trace": [...]}
```
Suggested copy: "Checked {last_poll.checked} new npm malware advisories from OSV in {last_poll.ms} ms: not hit".

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

### POST /api/watch/poll (admin, X-Admin-Token) body `{"max_batch": 200}` optional
Runs one poll now; returns the poll summary (plus `watermark`, `remaining`, `fetch_errors`). 409 when busy.

### GET /api/watch/status
`{running, loop, last_poll, last_error, watermark, max_batch, interval_s, depth, index_url, streams_open}`.

## 3. Concurrency benchmark
### GET /api/bench/last (public)
```jsonc
{"last": {"id": "B-...", "label": "local|server", "at": "2026-10-16T15:24:11+00:00", "clients": 32, "procs": 4,
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
   "title": "not hit: 25 new advisories checked, none reach this fleet",   // or "hit: 2 of 25 new advisories reach this fleet"
   "items": ["bungoma (MAL-2026-17389): not hit", "..."], "ref": null}
  ```
  Suggested label: "Live OSV watch". Treat a title starting with `hit:` as an alert.
- `watch_replay`: the latest 3 replays of a past advisory (`POST /api/watch/replay`), always labelled as a replay.
  `count` is 1, `items` = `["debug@4.4.2"]`, `ref` = the OSV URL.
  ```jsonc
  {"kind": "watch_replay", "at": "2026-10-16T13:02:11Z", "count": 1,
   "title": "replay of MAL-2025-46974 (debug): 10 of 22 services hit", "items": ["debug@4.4.2"],
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

# Contract: crew, sessions, tenants

Backend contract for the frontend. Everything here is deterministic: zero LLM calls. All Cypher lives in
`queries/crew.cypher`, `queries/sessions.cypher` and the existing query files.

## Tenants

Each tenant is its own FalkorDB graph: `acme` is `fleet:acme` (22 services), `globex` is `fleet:globex`
(12 services). Fleet, incidents, playbooks, sessions and crew runs never cross tenants.

**How to pick a tenant, on any endpoint.** The default is `acme`. There are three ways to choose one:

1. A query string: `?tenant=globex`.
2. A header: `X-Tenant: globex`.
3. A JSON body field, `"tenant": "globex"`, on `/api/ask`, `/api/fix`, `/api/summary` and `/api/crew/run`. The body field wins over the query string and the header.

**Validation.** An unknown tenant returns `400 {"error":"bad_request","message":"unknown tenant 'x'; allowed: acme, globex"}`. The API never falls back to a different tenant.

### GET /api/tenants

```json
{"default": "acme", "current": "acme",
 "tenants": [{"id": "acme", "company": "Acme", "graph": "fleet:acme", "default": true, "loaded": true},
             {"id": "globex", "company": "Globex", "graph": "fleet:globex", "default": false, "loaded": true}]}
```

**Fields added to existing responses:**
- `/health` adds `tenant` and `tenants: {acme: true, globex: true}`.
- `/api/fleet` adds `tenant` and `company`.
- `/api/ask` and `/api/fix` add `tenant` and `company`.

**The demo contrast (verified by tests against the ingested advisory data and npm semver):**

| Ask | Acme | Globex |
| --- | --- | --- |
| `Shai-Hulud` | Not hit (0 of 22) | **4 of 12 exposed**: globex-storefront (ngx-bootstrap ^20.0.2, ngx-toastr ^19.0.0), partner-api (koa2-swagger-ui ^5.11.0), ops-console (ngx-color ^10.0.0, which also pulls @ctrl/tinycolor ^4.1.0), brand-studio (@ctrl/tinycolor ^4.1.0). design-tokens is safe: it pins exactly 4.1.0. |
| `maintainer qix was phished` | 13 of 22 | 5 of 12 |

## Sessions (conversation history in the graph)

**Graph shape:** `(:Session {id, tenant, created, updated, turns})-[:HAS_TURN]->(:Turn {i, query, resolved_query, intent, outcome, ts, depth, entity_kind, entity_key, message, kind, crew_run, followup_from, exposed_count})-[:ABOUT]->(Maintainer|Package|Service|Version|Advisory|Incident)`, plus `(:Turn)-[:NEXT]->(:Turn)`.

**session_id format:** 8 to 64 characters from `A-Z a-z 0-9 _ -`. The frontend generates it (for example `crypto.randomUUID()`) and keeps it in localStorage, one per tenant. An invalid id returns 400. Sessions are per tenant, so the same id under another tenant is a different, usually missing, session.

### /api/ask and /api/fix

Both accept `session_id`. With it, the turn is stored and follow-ups are resolved:

```json
POST /api/ask {"query": "who should I wake for that?", "tenant": "globex", "session_id": "3f2a...", "depth": 4}
```

Response: the usual AskResponse plus the fields below.

```json
{"query": "who should I wake for that?",
 "resolved_query": "who should I wake for Shai-Hulud",
 "tenant": "globex", "company": "Globex",
 "followup": {"resolved": true, "from_turn": 1, "anchor": {"kind": "campaign", "key": "2025-09-15-shai-hulud"},
              "rewritten": "who should I wake for Shai-Hulud", "depth": 4,
              "original": "who should I wake for that?",
              "reason": "'who should I wake for that?' refers to turn 1 (campaign 2025-09-15-shai-hulud)"},
 "session": {"id": "3f2a...", "turn": 2, "about_linked": 4}}
```

**Field presence:**
- `followup` appears only when the question looked like a follow-up: it contains a word like that, it, this, same or again, starts with "and" or "what about", or says "depth N".
- If there is no earlier turn, you get `{"resolved": false, "reason": "no earlier turn in this session"}` and the question runs as typed.
- `resolved_query` appears only when `followup.resolved` is true.
- `session` appears only when `session_id` was sent.

**Follow-up rules.** All of them read the last Turn and its ABOUT nodes from FalkorDB:
- **Who to wake** ("who should I wake for that?"):
  - If the last turn was about a service, it becomes "who should I wake for <service>".
  - If the last turn was an incident, it becomes a wake-for-incident question, which returns `wake` for every exposed service. This works for a maintainer, package@version, advisory, campaign or packages.
- **Depth** ("and at depth 6?"): reruns the last turn's resolved question at depth 6. The response `depth` is 6.
- **Self-contained questions:** a question that names its own subject (a service, package, advisory or campaign) is never rewritten. An explicit "depth N" in it still applies.

### GET /api/sessions/{session_id}?tenant=acme&limit=50

200 response:

```json
{"id": "3f2a...", "tenant": "globex", "created": "2026-10-16T15:20:26+00:00", "updated": "...", "turns": 2,
 "history": [
   {"i": 1, "query": "Shai-Hulud", "resolved_query": "Shai-Hulud", "intent": "crew", "outcome": "done",
    "ts": "...", "depth": 4, "entity_kind": "campaign", "entity_key": "2025-09-15-shai-hulud",
    "message": "4 of 12 Globex services can install a malicious version at depth 4; ...",
    "kind": "crew", "crew_run": "CREW-20261016-d4ec856e", "followup_from": null, "exposed_count": 4,
    "about": [{"kind": "Service", "key": "globex-storefront"}, {"kind": "Incident", "key": "INC-..."}]},
   {"i": 2, "query": "who should I wake for that?", "resolved_query": "who should I wake for Shai-Hulud",
    "intent": "who_to_wake", "outcome": "answered_with_path", "kind": "ask", "followup_from": 1, "...": "..."}],
 "trace": [...]}
```

**Turn field values:**
- `history` is oldest first.
- `kind` is one of `ask`, `fix` or `crew`.
- `outcome` is the ask outcome; for crew turns it is the crew status.

**Errors:** 400 for an invalid id. 404 `{"detail": "no session <id> in tenant <t>"}` when the session is missing.

## Crew (four agents, handoffs through shared graph state)

**Agents and DAG:**

```
triage ──> investigator ──┐
   └─────> responder ─────┴──> fixer
```

**Graph shape:**
- `(:CrewRun {id, query, tenant, depth, session_id, started, finished, ms, status, incident, message})-[:RAN]->(:AgentRun {id, run_id, agent, started, finished, ms, status, summary, error})`
- `(:AgentRun)-[:PRODUCED]->(:Finding {id, kind, agent, title, text, data(JSON), at, seq, run_id, incident, source:'crew'})`
- `(:Incident)-[:HAS_FINDING]->(:Finding)`
- `(:AgentRun)-[:HANDED_OFF_TO {reason, at, seq}]->(:AgentRun)`
- `(:CrewRun)-[:TRIAGED]->(:Incident)`

**How state is shared:** each agent reads its inputs with `crew_read_upstream`, which walks HANDED_OFF_TO edges back from the agent's own AgentRun. Nothing passes between agents in Python. A test changes Triage's Finding in FalkorDB mid-run, and every downstream agent acts on the changed data.

**Concurrency:** Investigator and Responder run concurrently on worker threads. Fixer starts after both hand off.

**Findings by agent (`kind` values):**

| agent | kinds | data highlights |
| --- | --- | --- |
| triage | `entity`, `blast_radius`, `memory_delta` (or `unresolved` when it stops) | `blast_radius.data`: `exposed[]` ({service, tier, team, exposed_packages, min_hops, findings[] with path.via/requirement/display}), `safe[]` ({service, reason, path, requirement}), `clean_count`, `fleet_size`, `incident_id`. `memory_delta.data`: `first_triage`, `previous_seen`, `triage_count`, `newly_exposed[]`, `no_longer_exposed[]`, `unchanged[]` |
| investigator | `paths`, `honest_negatives`, `spof` | `paths.data`: `chains[]` ({service, tier, package, bad_version, hops, display, direct}), `carriers` ([[pkg, n]]), `deepest`, `shortest_to_critical` (algo.SPpaths: via, requirements, hops). `spof.data`: `maintainers[]`, `pagerank[]` |
| responder | `wake`, `page_drafts` | `wake.data.teams[]`: {team, team_display, slack, pager, oncall[], services[], top_tier}. `page_drafts.data.drafts[]`: {team, channel, pager, severity SEV1/2/3, text}. Nothing is sent |
| fixer | `patch_plan`, `playbook` | `patch_plan.data`: `order[]` (tier first), `specs`, `combined`, `pins`, `exact_pins[]` (packages with no fixed release, pinned exactly to the published version resolved today), `per_service[]` ({service, tier, package_json}). `playbook.data`: `playbook_id` (or null), `uses_count`, `agrees`, `approved_by`, `source_incident`, `candidates[]` |

**Memory that updates over time:**
- The crew upserts one Incident per (tenant, trigger, depth) using `crew_key = "<entity_key>|d<depth>"`. If an Incident from Generate fix already exists for the same trigger and depth, the crew adopts it instead of creating a duplicate.
- Each re-triage sets `last_seen`, increments `triage_count`, stores `exposed_services` and `last_delta`, and replaces the EXPOSED edges in one atomic query, so the edge count always equals `exposed_count`.
- Playbooks gain `uses_count` (successful reuses) and `last_used`. The Fixer picks the matching playbook with the highest `uses_count`. Generate fix bumps `uses_count` too, when its pins agree with the playbook.

### POST /api/crew/run

Request body:

```json
{"query": "Shai-Hulud", "depth": 4, "session_id": "3f2a...", "tenant": "globex"}
```

**Fields:**
- `query`: required, 1 to 500 characters.
- `depth`: 1 to 6, default 4.
- `session_id`: optional. Follow-ups work here too: "run the crew on that".
- `tenant`: optional.

**Response modes:**
- **Default: `text/event-stream`.** The `X-Crew-Run` response header carries the run id immediately.
- **JSON:** send `Accept: application/json` or `?stream=false` to get only the final result, the same shape as the `done` event.

**Rate limit:** 6 runs per client IP per minute (`CREW_PER_IP_PER_MIN`). Over the limit you get `429 {"detail": "crew rate limit: try again in Ns"}` with a `Retry-After` header.

**Errors:**
- 400: bad tenant or session_id.
- 422: body validation failed.
- 503: graph down. On the stream, the failure arrives as an `error` event.

**SSE events.** Every `data` is JSON and carries `run_id`. Typical order:

| event | data |
| --- | --- |
| `followup` | only when a session follow-up was resolved: the `followup` object above |
| `run` | `{run_id, tenant, company, query, depth, session_id, agents: [{agent, title, role, agent_run_id}], dag: [["triage","investigator"],["triage","responder"],["investigator","fixer"],["responder","fixer"]]}` |
| `agent_start` | `{agent, agent_run_id, title, role}` |
| `incident` | `{incident_id, created, triage_count}` (after Triage upserts the Incident) |
| `finding` | `{agent, finding: {id, agent, kind, title, text, data}}` |
| `agent_done` | `{agent, status: "done" \| "no_incident" \| "error", ms, summary, error}` |
| `handoff` | `{from, to, reason}`. Exactly 4 per successful run, matching the DAG |
| `read` | `{agent, query: "crew_read_upstream", findings: [{id, agent, kind}]}`: which upstream findings the agent read from the graph |
| `error` | `{message}` |
| `done` | the full replay object (below), plus `session: {id, turn, about_linked, followup}` when a session_id was sent |

**Event order details:**
- Investigator and Responder events interleave, because they run concurrently.
- `done` is always last on success.
- **Unknown triggers** (for example "left-pad was compromised"): Triage emits an `unresolved` finding and `agent_done` with status `no_incident`. There are no handoffs, and `done` has `status: "no_incident"` with `incident: null`.

### GET /api/crew/{run_id}?tenant=globex

This replays the run entirely from the graph. The id format is `CREW-YYYYMMDD-xxxxxxxx`; anything else returns 400. A missing run, or a run in another tenant, returns 404.

```json
{"run_id": "CREW-20261016-d4ec856e", "tenant": "globex", "company": "Globex", "query": "Shai-Hulud", "depth": 4,
 "session_id": "3f2a...", "status": "done", "started": "...", "finished": "...", "ms": 93.5,
 "message": "4 of 12 Globex services can install a malicious version at depth 4; ...",
 "incident": {"id": "INC-...", "triage_count": 1, "first_seen": "...", "last_seen": "...",
              "delta": {"first_triage": true, "newly_exposed": [], "no_longer_exposed": [], "unchanged": [], "...": "..."}},
 "agents": [{"id": "CREW-...-triage", "agent": "triage", "title": "Triage", "role": "...", "status": "done",
             "started": "...", "finished": "...", "ms": 38.8, "error": null, "summary": "...",
             "findings": [{"id": "CREW-...-triage-entity", "kind": "entity", "title": "...", "text": "...",
                           "data": {}, "at": "...", "seq": 1}]}],
 "handoffs": [{"from_agent": "triage", "to_agent": "investigator", "from_run": "...", "to_run": "...",
               "reason": "blast radius ready: explain the paths", "at": "...", "seq": 4}],
 "dag": [["triage","investigator"],["triage","responder"],["investigator","fixer"],["responder","fixer"]],
 "replay_trace": [...]}
```

**Ordering:** `agents` is always in the order triage, investigator, responder, fixer. Findings and handoffs are ordered by `seq`, the global order within the run.

### GET /api/crew?tenant=globex&limit=20

Recent runs: `{"tenant", "runs": [{id, query, depth, status, started, ms, incident, message, session_id}]}`.

## MCP tools

These are added to `python -m app.mcp_server`:

- **`crew_run(query, depth=4, tenant=None, session_id=None)`** returns the crew result, with the same shape as `done` but without the traces.
- **`session_history(session_id, tenant=None, limit=50)`** returns `{found, history, ...}`.
- **`list_tenants()`** returns `{default, tenants[]}`.

**Tenant guard:** inside a request that is already pinned to a tenant (the web agent calling tools over in-memory MCP), a tool may not switch to another tenant. The call fails with "cross-tenant access refused".

## Admin and scripts

- **`POST /api/admin/reset?tenant=globex`** (X-Admin-Token) resets one tenant's graph. It deletes the Incidents, Findings, crew runs, sessions, Campaign nodes and Playbooks there. BYO scan graphs are kept unless `include_byo=true` is passed explicitly. Other tenants are untouched.
- **Reset and seed scripts take a tenant:** `TENANT=globex sh scripts/reset-live.sh` and `TENANT=globex sh scripts/seed-demo-memory.sh`. The default is acme.
- **Startup seeding:** `ingest/seed_if_missing.py` seeds every tenant in `TENANTS` (default `acme,globex`) on container start and never wipes an existing graph. Locally: `TENANT=globex python ingest/load.py`.

## Campaign nodes (session follow-ups)

A turn about a campaign (for example "Were we hit by the Shai-Hulud worm?") is linked to a real node:
`(:Turn)-[:ABOUT]->(:Campaign {id: '2025-09-15-shai-hulud', label: 'Shai-Hulud', advisories})-[:GROUPS]->(:Advisory)`.
The node is MERGEd on first mention (one per campaign per tenant graph). When the campaign is not loaded in a
tenant, `advisories` is 0 and the follow-up still resolves honestly. "who should I wake for that?" reads the
Campaign from the ABOUT edge first (`followup.anchor.via = "graph"`), so it works even without the turn's stored
trigger.

## Review 3 changes (Fri Oct 16 2026)

- **One Incident per trigger per tenant.** `crew_key` is now the entity key (`qix`, an advisory id, a campaign id),
  not `key|d<depth>`. The crew and Generate fix share `tools.upsert_incident`: depth, exposure, `patch` (fix only),
  `last_session`, `last_run` and a capped `history` (last 20 triages, JSON strings) are updated in place; EXPOSED
  edges are replaced, `last_delta` carries `previous_depth`, `newly_exposed`, `no_longer_exposed`. Legacy per-depth
  incidents are adopted (most recently seen first) on the next triage.
- **Seen before** never cites an incident created or last updated by the same session or crew run.
- **Who to wake.** A who-to-wake question about an incident keeps `intent: who_to_wake`; the message starts
  `Wake N teams: Team (#channel, on-call Name @handle) for services; ... Why: <exposure sentence>`, or
  `Nobody to wake: ...` when nothing is exposed.
- **LLM mode.** Outcome and panels come from the tool the model ran (spof, path, wake, memory; exposure and
  incident_scope build the investigate panels for the same trigger). A rejected LLM sentence keeps the tool panels.
- **Errors.** Any DB failure returns 503 `{"error": "database_unavailable", "message": <fixed sentence>}` (was
  `graph_unavailable`); crew SSE `error` events carry `{run_id, error, message}` with the same codes. Unexpected
  errors are 500 `internal_error` with a fixed sentence.
- **Warm-up.** `crew.engine.warm_up()` runs at startup in a background thread (read-only); `/health.crew_warmup`.
- **Sessions.** The same normalised question at the same depth in the same session within 8 s is one turn
  (`session.deduped: true`, `kinds: ["ask", "crew"]`, `crew_run` attached).
- **BYO.** Scan graphs are `byo:<tenant>:<hash>`; `/api/byo/scans` lists the current tenant's only; TTL cleanup
  covers every BYO graph including legacy `byo:<hash>` ones.

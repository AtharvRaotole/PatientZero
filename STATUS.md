# STATUS (PatientZero)

_Last updated: Fri Oct 16 2026, 1:30 PM ET_

## Completed
- [x] FalkorDB 6.0.1 running locally without Docker: `redis-server` 8.0.2 (apt) + `falkordb-x64.so` module (sha256 verified), port 6379
- [x] `scripts/start-falkordb.sh` (idempotent), `scripts/stop-falkordb.sh`, `scripts/verify_falkordb.py` (Cypher smoke test passes)
- [x] Backend scaffold `backend/` (FastAPI, uv): `/health` (pings FalkorDB), `/api/graph/demo`; CORS for :3001
- [x] Frontend scaffold `frontend/` (Next.js 15 + TS + Tailwind 4): homepage shows live backend/FalkorDB status + demo query; `npm run build` passes
- [x] Verified in headless Chrome: homepage renders "Backend: up" from the API (CORS OK)
- [x] `.env.example`, `.gitignore`, README skeleton, `scripts/dev.sh`

- [x] Agent: deterministic router first, OpenAI tool calling only when not confident, name-checked answers, click-only AI summary, answer cache, token caps
- [x] LLM hard spend guard: daily cap (OPENAI_DAILY_CALL_CAP=200), per-client limit (OPENAI_PER_IP_PER_MIN=10, rightmost X-Forwarded-For), deterministic fallback with a trace note, usage log .cache/llm_usage.jsonl, budget in /health
- [x] Insights: blast radius rings, exposure matrix, maintainer reach vs PageRank scatter, incident timeline (/api/timeline over queries/timeline.cypher)
- [x] BYO scan UI (GitHub URL or package.json, include devDependencies toggle, axios and express 4.x presets) and Approve, Playbooks UI, playbook reuse shown in the fix panel
- [x] Shai-Hulud campaign routing (incident_scope_by_campaign); honest message when the campaign is not loaded
- [x] Static export (NEXT_OUTPUT=export, NEXT_PUBLIC_API_URL=.) verified served by app.serve on one origin, browser smoke test clean
- [x] Deployed on Railway: **https://patientzero-production-2cb3.up.railway.app** (services `patientzero` from the Dockerfile, `falkordb` v4.22.1 with a `/data` volume, password auth, AOF). Live checks passed: /health up, UI, qix depth 4 gives 13 exposed, BYO axios, approve then recall shows the playbook, data survives app and DB redeploys. Details in DEPLOY.md
- [x] Verification: backend pytest 116 passed (was 89); browser e2e 12/12; BYO presets checked in browser (axios exposed, express 4.x runtime clean)

## Review 1 fixes (Fri Oct 16 2026, about 12:25 AM ET)
- [x] P0-1 memory never replaces a fresh traversal: pkg@ver, advisory ids, explicit depth and compromise reports always traverse; memory is attached as `memory.seen_before` (UI: "Seen before: INC-...", with a diff). Bare `qix` at the stored depth still answers from memory (AC10). Regression tests: backend/tests/test_memory_regression.py
- [x] P1-1 LLM answers naming any service, package, person or @handle not in the tool result are rejected and replaced by the deterministic answer (trace: "LLM answer rejected"); AI summary withheld the same way
- [x] P1-2 install-safe patches (direct deps bumped with a same-spec scoped override, transitive overrides scoped to the compromised major). Verified with npm 10.9.9 / Node 20: docs/verification/npm-patches.txt (`cd backend && uv run python ../scripts/verify_npm_patches.py`)
- [x] P2: hypothetical wording for pkg@ver without an advisory; unknown package named in mixed questions; ADMIN_TOKEN guard (X-Admin-Token) on reset/cleanup/DELETE plus POST /api/admin/reset; LLM prompt about 1.2k to 1.7k tokens per question (router pre-runs investigate, compact digest); router synonyms for plain-English incident phrasings; BYO 60-dependency cap in headline and warnings; tests delete their BYO graphs; BYO cache gitignored except the committed demo set
- [x] Live deploy redeployed with ADMIN_TOKEN set (value in ~/.config/patientzero/admin-token, chmod 600); live demo memory reset
- Reset before recording: `curl -XPOST https://patientzero-production-2cb3.up.railway.app/api/admin/reset -H "X-Admin-Token: $(cat ~/.config/patientzero/admin-token)"`

## Track 2 backend: crew, sessions, tenants (Fri Oct 16 2026, about 11:45 AM ET)
- [x] Agent crew (backend/app/crew): Triage, Investigator, Responder, Fixer hand off through FalkorDB (AgentRun, Finding, HANDED_OFF_TO); each agent reads upstream findings from the graph; Investigator and Responder concurrent. POST /api/crew/run (SSE or JSON), GET /api/crew/{id} replay, 6 runs per IP per minute
- [x] Sessions in the graph (Session, Turn, ABOUT); follow-ups "who should I wake for that?" and "and at depth 6?"; GET /api/sessions/{id}
- [x] Tenants: fleet:acme and fleet:globex (synthetic Globex, real npm ranges). Shai-Hulud: Acme not hit, Globex 4 of 12. `?tenant=` / X-Tenant / body field on every endpoint, allowlist; reset and seed scripts take TENANT; seed_if_missing seeds both
- [x] Memory over time: re-triage updates the Incident (last_seen, triage_count, delta); playbooks uses_count and last_used, Fixer prefers most reused; Shai-Hulud fixes pin exactly to the published version resolved today (no fixed release listed)
- [x] MCP tools crew_run, session_history, list_tenants (cross-tenant guard inside pinned requests)
- [x] Tests: 19 new (DAG shape, graph-read proof by tampering, SSE, replay, follow-ups, isolation, delta, playbook reuse, exact pins, 20 concurrent crews); full suite green on FalkorDB 6.0.1 and on 4.22.1 (the live version) for the new tests
- Contract for the frontend: docs/CONTRACT-crew-sessions-tenants.md
- Live needs one redeploy; the container seeds fleet:globex on start without touching fleet:acme

## Follow-ups: timeline, Campaign nodes, safe reset (Fri Oct 16 2026, about 11:55 AM ET)
- [x] Timeline: qix advisories collapsing into one `campaign` event (2025-09-08-qix, 19) is correct product behavior; the old test assumed graphs loaded before advisories were campaign-tagged. Startup backfill (`seed_if_missing.ensure_qix_campaign`) tags untagged incident advisories on older graphs (live) so timeline and campaign scope match a fresh load. `/api/timeline` normalises every event (known kind, string title, int count, non-null items).
- [x] Session turns about a campaign link to `(:Campaign)-[:GROUPS]->(:Advisory)`; follow-ups read it from the ABOUT edge (`anchor.via = graph`).
- [x] `POST /api/admin/reset` keeps BYO scan graphs unless `?include_byo=true` (`INCLUDE_BYO=true scripts/reset-live.sh`).
- [x] 185 tests green on FalkorDB 6.0.1 and 4.22.1 (acme with Shai-Hulud loaded, plus an old-format acme graph to exercise the backfill).

## FalkorDB 6.0.1 crash fix (Fri Oct 16 2026, about 12:20 PM ET)
- [x] Crashing construct: a RETURN with an aggregate plus 2+ grouping keys where a non-first key is null panics FalkorDB 6.0.1 (`src/reply.rs` unwrap on None, server exits). Minimal: `MATCH (n:N) RETURN n.name AS x, n.missing AS a, count(n)`. 4.22.1 is fine.
- [x] Fix: aggregate in WITH, then RETURN (who_to_wake, fleet_overview, list_incidents, recall, single_points_of_failure, watch_replay_lookup, crew_count_check). who_to_wake also coalesces team display/slack/pager; `ingest/load.py team_defaults` sets them at build time (acme, globex).
- [x] `tests/test_query_safety.py`: static check over every query plus a live run on prop-less nodes; 191 green on a throwaway 6.0.1, a throwaway 4.22.1, and the main local DB.

## Pre-redeploy gate: hang fix, security audit, live verification (Fri Oct 16 2026, about 12:50 PM ET)
- [x] Hang root cause: `Crew.run` (async) ran its own FalkorDB writes on the event loop; under SSE + crew + ask load a write waiting behind others froze every request (reproduced: /health 22.7 s). Fix: crew graph work on a bounded crew pool, crew runs capped (6, 2 per IP), watch streams capped (100, 8 per IP) with slots always released, SSE heartbeats, bounded FalkorDB pool (64), 64 API threads. Stress after the fix: /health p50 about 0.1 s, max 0.39 s, no leaked slots. `scripts/stress_streams.py`, `backend/tests/test_streams_hang.py`
- [x] Security audit: docs/verification/security.md. History clean (gitleaks + regex + verbatim secret search), bundle clean, admin routes 401 without the token. Fixed: per-IP limits on ask/fix/summary/tools (120/min) and approve (10/min), tool argument caps, BYO host allowlist (redirects included) and a deps.dev size cap, tighter CORS. FalkorDB password rotated at 1:20 PM ET (see the doc)
- [x] Tests: 198 green on FalkorDB 6.0.1 and 4.22.1; e2e 17/17 and 25/25 locally and against live
- [x] Live verified after redeploying 384fd77: /health ok (mcp_agent.active, watch loop on), tenants Acme 22 / Globex 12, Globex Shai-Hulud 4 of 12, follow-ups resolved from the graph, crew run streamed (4 agents, 4 handoffs, replay), real OSV poll (index unchanged, HTTP 304), replay debug@4.4.2 (Acme 10 of 22, Globex 5 of 12), timeline `watch` and `watch_replay` (no osv-watch campaign), live bench 740.6 ops/s p99 112 ms 0 errors
- [x] Live memory reset: acme has exactly the approved qix incident and its playbook, globex is empty, and no crew runs or sessions are left from testing

## Password rotation (Fri Oct 16 2026, about 1:20 PM ET)
- [x] Live FalkorDB password rotated (new random value set via the Railway API on both services, never printed), DB restarted, app redeployed at 76cbee7. /health ok; e2e 27/27 crew/tenants and 17/17 browser against live (the slider-drag check failed once and passed on rerun, see below). Live memory reset again: acme has exactly the approved qix incident and its playbook, globex is empty, 0 crew runs and 0 sessions
- Known flake: "P0-2 slider drag lands on the last depth" failed in 2 of 4 live runs (the answer shown was depth 6 with the slider at 4, no 429 and no console errors), and passed on rerun and in a focused probe. Looks like a stale response winning a race in the depth handling on a remote deploy; frontend owner to check

## Next actions
- Final polish pass on screenshots for the README
- Redeploy after pushing changes: `sh scripts/redeploy-railway.sh` (ships origin/main; no GitHub auto-deploy)
- Optional: load Shai-Hulud (INCIDENTS=qix,shai-hulud) in the demo graph and add a preset chip

## Assumptions
- Plain `redis-server` + module replaces Docker locally; FalkorDB Browser uses port 3000, so the frontend dev server runs on **3001**
- The Acme fleet is synthetic; npm packages, versions, ranges, advisories and maintainers are real
- The LLM is optional: everything works deterministically without OPENAI_API_KEY
- Per-client LLM limits key on the rightmost X-Forwarded-For entry (the edge proxy's), else the socket peer

## Blockers
- None. (Docker is not installed; worked around.)

## Run commands
```bash
./scripts/dev.sh                                   # FalkorDB :6379, backend :8000, frontend :3001 (logs /tmp/*.log)
./scripts/start-falkordb.sh / ./scripts/stop-falkordb.sh
cd backend && uv run uvicorn app.main:app --port 8000 --reload
cd frontend && npm run dev                         # http://localhost:3001
cd frontend && npm run build
cd backend && uv run python ../scripts/verify_falkordb.py
curl localhost:8000/health
sh scripts/redeploy-railway.sh                      # push first; deploys origin/main to Railway
```

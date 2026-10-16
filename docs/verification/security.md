# Security audit (Fri Oct 16 2026, about 12:45 PM ET)

Scope: the whole repo (all git history), the deployed Railway config (services `patientzero` and `falkordb`), and
the live app at https://patientzero-production-2cb3.up.railway.app. Secret values were compared by script and never
printed.

## Results

| Check | How | Result |
|---|---|---|
| Secrets in git history | gitleaks 8.21.2 over all 55 commits; a regex scan of `git log -p --all` (OpenAI `sk-`, GitHub `ghp_`/`github_pat_`/`gho_`, Slack, AWS, private keys) | **Clean.** gitleaks reported 3 `generic-api-key` hits, all false positives: the campaign key `2025-09-15-shai-hulud` in a test and in the contract doc. The regex scan found nothing. |
| Real secret values in history | The live `OPENAI_API_KEY`, `ADMIN_TOKEN`, FalkorDB password, Railway project token and GitHub token, each searched verbatim in `git log -p --all` | 0 occurrences of each |
| `.env` not tracked | `git ls-files`, `.gitignore`, history of every `.env*` path | Only `.env.example` was ever committed; `.env`, `.env.local`, `*.env.local` are ignored |
| OpenAI key server side only | The frontend reads only `NEXT_PUBLIC_API_URL`; the live JS bundle (every `/_next/static` chunk plus the HTML) searched for the live key value and for `sk-` | **Clean.** The live index plus all 9 `/_next/static` JS and CSS files (931 KB) contain neither the OpenAI key, the admin token nor the DB password, no `sk-` pattern, and not even the string `OPENAI` |
| Admin token on mutating admin routes | Every POST/PUT/DELETE route listed from the code; unauthenticated calls against live | `POST /api/admin/reset`, `POST /api/memory/reset`, `DELETE /api/playbooks`, `POST /api/byo/cleanup`, `POST /api/watch/poll` and `POST /api/bench/run` require `X-Admin-Token` (compared with `hmac.compare_digest`): live returns **401** to each without it. Public writes are product actions with their own limits: ask/fix (Incident memory), approve (Playbook), crew run, watch replay, BYO scan. |
| Input validation | Code review plus live probes | tenant: allowlist and regex (live `../x` gives 400); session_id: `^[A-Za-z0-9_-]{8,64}$` (400); crew run id: `CREW-YYYYMMDD-hex8` (400); incident id regex for approve, length 80 for check; query 1 to 500 characters (501 gives 422); depth 1 to 6; replay advisory 3 to 80; BYO URL 300 and package.json 200 KB; playbook match list 4,000 characters and 100 packages. **Fixed:** `/api/tools/{name}` now caps arguments (10 arguments, strings 500 characters, lists 100). |
| Rate limits | Code review, tests | crew 6 runs per IP per minute; watch replay 6; BYO scan 5; LLM 10 per IP per minute plus 200 per day. **Fixed:** `/api/ask`, `/api/fix`, `/api/summary` and `/api/tools/{name}` had no limit; now 120 per IP per minute (`ASK_PER_IP_PER_MIN`; a slider drag sends one ask per step). Approve is limited to 10 per IP per minute. A valid admin token is exempt. Client IP is `X-Real-IP` on Railway: the edge sets it to the client's remote IP and overwrites any caller copy (Railway public networking specs). `X-Forwarded-For` is ignored unless `TRUSTED_PROXY_HOPS` names how many of our own proxies sit in front (then the right-most untrusted hop is used); off Railway with neither set, only the socket peer counts. DB failures of any kind (connection, timeout, rejected credentials) return 503 `database_unavailable` with a fixed sentence on every route and in crew SSE error events; no exception text reaches a client. |
| CORS | Code review, live preflight from `https://evil.example` | Explicit dev origins only (localhost:3001/3000); production is same origin. Live preflight from a foreign origin gets no `Access-Control-Allow-Origin`. **Tightened:** `allow_credentials` false (no cookies are used), methods GET/POST/DELETE/OPTIONS, headers limited to Content-Type, Accept, X-Tenant, X-Admin-Token, Last-Event-ID. |
| SSE limits | Code review, tests, stress run | **Fixed** (see the hang below): watch streams at most 100 open and 8 per IP (429 or 503 beyond), close after 15 min, `: ping` every 15 s, disconnect checked every second, and the slot is released however the connection ends. Crew runs: at most 6 at once and 2 per IP, with a `: ping` heartbeat. |
| No Cypher built from user text | grep for f-strings, `.format` and concatenation near `query(` | Every query is a named, parameterised entry in `queries/*.cypher` (the catalogue). The only literal outside it is a constant in the bench runner. The LLM can only pick tools; it never writes Cypher. |
| BYO fetch SSRF | Code review, test | User input only fills an owner/repo/ref matched by regex into `https://raw.githubusercontent.com/...`, plus package names into `https://api.deps.dev/...`. **Fixed:** every outbound BYO request, redirects included (at most 3), must be HTTPS to one of those two hosts, or it is refused (`blocked_host`); deps.dev responses are now capped at 5 MB (package.json 200 KB and lockfile 8 MB were already capped); 15 s timeout, 5 s connect timeout. The OSV watch fetches fixed URLs with redirects off, byte caps and timeouts. |
| Outbound timeouts and caps | Code review | OSV index (Range request), records 512 KB, 10 s; BYO as above; Railway is called only from ops scripts. |
| FalkorDB exposure | Railway API: domains and TCP proxies of the `falkordb` service | No public domain and no TCP proxy: reachable only on Railway's private network, password auth, AOF on a volume |

## The 11:50 AM ET hang (root cause and fix)
Symptom: the local API stopped answering every request, including `/health`, while FalkorDB was fine.

Reproduced with `scripts/stress_streams.py` (64 watch SSE streams dropped abruptly and reopened, 10 crew SSE runs
with half the clients vanishing mid-run, 10 to 16 `/api/ask` workers, `/health` probed every 0.5 s). Before the fix:
`/health` p50 255 to 283 ms, one probe took 22.7 s and another timed out, and a watch stream timed out on read.

Root cause: `Crew.run` is a coroutine, but it issued its own FalkorDB writes (`crew_create_run`, the two triage
handoffs, `crew_finish`) **on the event loop**. A graph write waits behind other writes (FalkorDB serialises writes
per graph) and can sit in the busy retry, and every such wait froze every request and SSE stream in the process.
Disconnect-and-retry clients made it compound: an abandoned run keeps going by design, and nothing capped how many ran
at once, so the backlog grew. The new test `test_crew_run_never_blocks_the_event_loop` measures the stall directly.
On the old code it fails with a 0.33 s loop stall per crew run when each graph call takes 0.15 s.

Fix (commit 384fd77):
- Every graph call in a crew run runs on a dedicated, bounded crew thread pool (`CREW_THREADS`, 16); nothing in an
  async handler touches the database on the loop.
- Concurrent crew runs are capped at 6, and 2 per IP (`streamguard.Slots`; 503 or 429 with Retry-After). The slot is
  held by the run, not by the stream, so a client that disconnects still counts until its run finishes.
- Watch streams are capped globally and per IP. `GuardedStream` releases the slot when the ASGI call ends for any
  reason, including a body that never started.
- SSE heartbeats on crew streams; watch streams already pinged.
- A bounded, blocking FalkorDB connection pool (64 connections, a 5 s wait, then a clean 503), with socket timeouts
  now also applied when `FALKORDB_URL` is used.
- 64 worker threads for sync routes (AnyIO's default is 40).

After the fix, with the same stress plus per-client caps (3 runs of 120 to 180 s): `/health` p50 80 to 118 ms, max
0.21 to 0.39 s, no failed probes, 0 stream slots left open afterwards, and 3x the `/api/ask` throughput (13,727 asks
in 180 s). Tests: `backend/tests/test_streams_hang.py` (7 tests: the loop-stall check, slots, crew 429 and 503, ask
limit and admin exemption, tool argument caps, BYO host allowlist, and a real uvicorn run with three rounds of abrupt
TCP resets that checks the per-IP 429, a fast `/health` and that no slots leak).

## Findings to act on
- **FalkorDB password rotated (done, Fri Oct 16 2026, 1:20 PM ET).** The old value had been printed once into the
  operator's terminal during this audit. A new 43-character random password (Python `secrets.token_urlsafe(32)`,
  held only in process memory, never printed) was set through the Railway GraphQL API in the `falkordb` service's
  `REDIS_ARGS --requirepass` and `FALKORDB_PASSWORD` and in the app's `FALKORDB_PASSWORD` (no other variable held
  the old value). Then the DB restarted and the app was redeployed (76cbee7). Checked by script: all three values
  match, the old value is gone from every variable, and no file on the ops box contains it (scan of /tmp, the home
  directory, the workspace and the agent store). After the rotation: /health ok with both tenant graphs loaded, data
  and memory intact (the volume persisted), e2e 27/27 and 17/17 against live.
- `POST /api/incidents/{id}/approve` is public by design (the demo's human-in-the-loop button). It is rate limited
  and the id is validated; put it behind the admin token if the demo ends.

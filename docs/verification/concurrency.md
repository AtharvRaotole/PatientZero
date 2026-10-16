# Concurrency benchmark (FalkorDB)

Measured Fri Oct 16 2026, about 11:15 AM to 11:25 AM ET. Raw JSON for every run is in
[docs/verification/bench](bench/). Reproduce with `scripts/bench_concurrency.py` (commands below).

## What runs
`scripts/bench_concurrency.py --target db` starts N clients, each with its **own FalkorDB connection**, for a fixed
duration. Every operation is a single FalkorDB round trip using the product's own Cypher from `queries/`:

| Operation | Share | What it is |
|---|---|---|
| `read_exposure_incident` | 40% | `exposure` (variable-length `*1..6` traversal) over the 19 qix packages at depth 4: the main product query |
| `read_exposure_deep` | 20% | `exposure` for `debug` at depth 6 |
| `read_who_to_wake` | 15% | `who_to_wake` for 3 random services |
| `write_event` | 20% | `CREATE` a WatchEvent-shaped node linked to its run, in a scratch graph `bench:<run>` |
| `read_scratch` | 5% | read-after-write count on the same scratch graph |

Reads hit the tenant graph `fleet:acme`; writes only touch the scratch graph, which is deleted at the end of every run
(`scratch_deleted: true` in each JSON, and every timed write is counted back: `events_written` equals the write ops).
`--procs` spreads the clients over processes, because the Python client is GIL bound before FalkorDB is.

## Results

### FalkorDB direct (local box: 8 vCPU, FalkorDB 6.0.1 module in redis-server 8.0.2, client and server on one box)
| Run | Clients | Ops in 20 s | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|---|---|
| Single client (baseline, 8 s) | 1 | 2,239 | 280 ops/s | 3.9 ms | 7.0 ms | 10.1 ms | 0 |
| Default mix (80% reads, 20% writes) | 32 | 35,481 | **1,774 ops/s** | 15.7 ms | 40.3 ms | 54.9 ms | **0** |
| Default mix | 64 | 45,849 | **2,292 ops/s** | 25.6 ms | 51.8 ms | 69.7 ms | **0** |
| Write heavy (50% writes) | 32 | 51,454 | **2,573 ops/s** (1,287 writes/s) | 10.7 ms | 28.6 ms | 39.1 ms | **0** |

Reads and writes in the 32-client default run: 1,422 reads/s (p99 56.8 ms) alongside 352 writes/s (p99 43.4 ms).

### Live-like FalkorDB settings (local, same graph, Railway's module arguments)
The live database ran with `THREAD_COUNT 2 MAX_QUEUED_QUERIES 25` (DEPLOY.md) when these numbers were taken; it now runs `MAX_QUEUED_QUERIES 200`. A second local FalkorDB was started
with exactly those arguments:

| Run | Clients | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|---|
| Raw (every rejection counted) | 30 | 1,520 ops/s | 18.2 ms | 37.2 ms | 50.3 ms | **305** "Max pending queries exceeded" (1.0%) |
| With the app's retry (`--retry-busy`) | 30 | 1,589 ops/s | 17.6 ms | 34.5 ms | 45.4 ms | **0** |

### End-to-end HTTP (`--target http`, read-only `/api/ask` in deterministic mode plus `/api/fleet`, no LLM)
| Target | Clients | Requests in 20 s | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|---|---|
| Live Railway deploy (code before this change) | 30 | 1,104 | 53.8 req/s | 559 ms | 886 ms | 997 ms | **4 HTTP 500** |
| Live Railway deploy, FalkorDB `MAX_QUEUED_QUERIES 200` (2026-10-16) | 30 | 1,009 | 49.4 req/s | 615 ms | 1,011 ms | 1,150 ms | **0** |
| Local, one uvicorn process, agent via MCP (default) | 30 | 1,911 | 94.5 req/s | 313 ms | 509 ms | 586 ms | 0 |
| Local, one uvicorn process, `MCP_AGENT=off` | 30 | 2,804 | 139.3 req/s | 210 ms | 342 ms | 439 ms | 0 |

Unloaded single request locally: about 15 ms with the agent going through MCP vs about 11 ms with direct calls.

## Findings
- **FalkorDB is not the bottleneck.** With 64 concurrent clients mixing traversals and writes it sustains about 2,300
  ops/s with p99 under 70 ms and zero errors; the HTTP layer tops out far earlier (a single Python process).
- **The live 500s were FalkorDB queue-full rejections.** All 4 errors on live were
  `ResponseError: Max pending queries exceeded` (`MAX_QUEUED_QUERIES 25` with 2 query threads). The same rejections were
  reproduced locally with live's settings (305 in 20 s at 30 clients). Fix (commit 877820a): `db.with_busy_retry`
  retries a rejected query with jittered backoff (20 ms doubling, up to 5 retries, `FALKORDB_BUSY_RETRIES`); a
  rejected query never ran, so the retry is safe for writes. With it: 0 errors and slightly higher throughput. The fix
  reaches live with the next redeploy. On 2026-10-16 the live FalkorDB service was changed to `MAX_QUEUED_QUERIES 200`
  (`THREAD_COUNT` stays 2: the service is limited to 2 vCPU); the same 30-client live run then had 0 errors
  (docs/verification/bench/live-http-c30-queue200.json). The volume persisted across the DB restart (same incident,
  playbook and node counts).
- **MCP tool selection costs about 4 ms per request.** Under saturation of one Python process that is about 32% fewer
  requests per second (139 vs 94.5 req/s at 30 clients). It stays on by default, because routing the agent through
  MCP is the product; `MCP_AGENT=off` restores direct calls.
- The HTTP numbers are read-only; mixed reads and writes are measured at the database level above, and on any deploy
  with `POST /api/bench/run` (admin).

## Reproduce
```bash
cd backend
uv run python ../scripts/bench_concurrency.py --clients 32 --procs 4 --duration 20 --save          # default mix
uv run python ../scripts/bench_concurrency.py --clients 64 --procs 8 --duration 20
uv run python ../scripts/bench_concurrency.py --clients 32 --procs 4 --duration 20 --write-pct 50
# live-like FalkorDB on another port
redis-server --port 6390 --save "" --appendonly no --daemonize yes --loadmodule ../.falkordb/falkordb.so THREAD_COUNT 2 MAX_QUEUED_QUERIES 25
FALKORDB_PORT=6390 TENANTS=acme ../ingest/.venv/bin/python ../ingest/load.py
FALKORDB_PORT=6390 TENANTS=acme uv run python ../scripts/bench_concurrency.py --clients 30 --procs 4 --duration 20 [--retry-busy]
# HTTP (keep live runs modest)
uv run python ../scripts/bench_concurrency.py --target http --url https://patientzero-production-2cb3.up.railway.app --clients 30 --duration 20
# on a deploy with this code (admin): server-side mixed run, then read the result
curl -XPOST $URL/api/bench/run -H "X-Admin-Token: $ADMIN_TOKEN" -H 'content-type: application/json' -d '{"clients":30,"duration_s":20}'
curl $URL/api/bench/last
```

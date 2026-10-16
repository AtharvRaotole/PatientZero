# Deploying PatientZero

**Live:** https://patientzero-production-2cb3.up.railway.app (Railway project `patientzero`, environment `production`)

**Redeploy the app (one line, from the repo root, ships the pushed `origin/main`):**
```bash
sh scripts/redeploy-railway.sh
```
It fetches, `git archive`s `origin/main` into a temp dir (so uncommitted local edits never ship) and runs
`railway up --service patientzero --detach` with a Railway project token (`$RAILWAY_PROJECT_TOKEN`, or
`~/.config/patientzero/railway-project-token` on the dev box; the account token in `$RAILWAY_TOKEN` does not
work with the CLI). Push first, then run it. Pass another ref as
the first argument to ship something else, e.g. `sh scripts/redeploy-railway.sh HEAD`. The FalkorDB service
is never touched, so the graph, incidents and playbooks survive. A build plus deploy takes about 1 to 2 minutes;
check `curl -s https://patientzero-production-2cb3.up.railway.app/health`.

The app service is not connected to GitHub (Railway's GitHub app has no access to `AtharvRaotole/FalkorDB`),
so pushes do not auto-deploy. Install the Railway GitHub app on the repo and connect it in the service
settings if you want that.

Two services:

| Service | What | Image |
|---|---|---|
| `patientzero` | FastAPI API plus the static Next.js UI on one origin | this repo's `Dockerfile` |
| `falkordb` | the graph database | `falkordb/falkordb-server:v4.22.1` with a volume on `/data` |

The UI calls `./api/...` on its own origin, so there's no CORS setup and no API URL to configure.
`OPENAI_API_KEY` is only read by the API process. It never reaches the browser bundle: only `NEXT_PUBLIC_*`
variables are compiled into the UI, and the Dockerfile sets just `NEXT_PUBLIC_API_URL=.`.
BYO scans, playbooks and ingest never call an LLM.

## Railway

1. **Create a project** from the GitHub repo `AtharvRaotole/FalkorDB` (branch `main`). Railway picks up
   `railway.json`, which builds the `Dockerfile` and health-checks `/health`.
2. **Add the database service:** New, then Docker Image, then `falkordb/falkordb-server:v4.22.1`. Name it `falkordb`.
   - Add a **Volume** mounted at `/data`.
   - Variables:
     - `FALKORDB_PASSWORD` = a long random string
     - `FALKORDB_DATA_PATH` = `/data` (required: the image's `run.sh` appends `--dir $FALKORDB_DATA_PATH`
       after `REDIS_ARGS`, so a `--dir` inside `REDIS_ARGS` is ignored and data would land outside the volume)
     - `REDIS_ARGS` = `--requirepass ${{FALKORDB_PASSWORD}} --appendonly yes --appendfsync everysec --save 60 1 --maxmemory 256mb`
     - `FALKORDB_ARGS` = `TIMEOUT_DEFAULT 10000 TIMEOUT_MAX 30000 THREAD_COUNT 2 MAX_QUEUED_QUERIES 25`
   - Don't add a public domain. The app reaches it over private networking.
3. **Set variables on the `patientzero` service:**
   ```
   FALKORDB_HOST=${{falkordb.RAILWAY_PRIVATE_DOMAIN}}
   FALKORDB_PORT=6379
   FALKORDB_PASSWORD=${{falkordb.FALKORDB_PASSWORD}}
   FALKORDB_GRAPH=fleet:acme
   SEED_ON_START=true
   OPENAI_API_KEY=<optional, server-side only>
   OPENAI_MODEL=gpt-4o-mini
   OPENAI_DAILY_CALL_CAP=200
   AGENT_MODE=auto
   BYO_RATE_LIMIT=5
   PORT=8080
   ```
   The public domain targets port 8080. Then **Settings, Networking, Generate Domain** for the public URL.
4. **Deploy.** On first boot `scripts/start-prod.sh` runs `ingest/seed_if_missing.py`. It waits up to 90 s
   for FalkorDB, then loads `fleet:acme` from the committed `data/` (no network calls). Later boots see the
   graph and leave it alone, so incident and playbook memory survive redeploys. To force a clean reload,
   run `python ingest/load.py` from a Railway shell (it wipes and rebuilds `fleet:acme` only).
5. **Smoke test:**
   ```bash
   curl https://<domain>/health        # {"status":"ok","falkordb":"up","graph_loaded":true,...}
   curl -X POST https://<domain>/api/ask -H 'content-type: application/json' -d '{"query":"maintainer qix was phished"}'
   curl -X POST https://<domain>/api/byo/scan -H 'content-type: application/json' -d '{"github_url":"https://github.com/axios/axios"}'
   ```

If FalkorDB is down, every data endpoint returns **503** `{"error":"graph_unavailable",...}`, and `/health`
reports `"falkordb":"down"` with the connection error. The API never answers from anywhere else.
`/health` itself stays HTTP 200 so Railway doesn't restart-loop the app while the database restarts.

### FalkorDB Cloud instead of a Railway database
Set `FALKORDB_URL=rediss://<user>:<password>@<host>:<port>` (or HOST/PORT/USERNAME/PASSWORD plus
`FALKORDB_SSL=true`) and skip step 2. The free tier (100 MB) fits: the demo graph is about 2.8k nodes, and each
BYO scan graph has a few hundred to a few thousand nodes and expires after `BYO_TTL_SECONDS`.

## Run the production build locally (mirrors the container)

```bash
scripts/start-falkordb.sh                                    # local FalkorDB on 6379
cd frontend && NEXT_OUTPUT=export NEXT_PUBLIC_API_URL=. npm run build && cd ..   # writes frontend/out
cd backend && uv sync && cd ..
FRONTEND_DIR=$PWD/frontend/out PORT=8000 PYTHON=backend/.venv/bin/python sh scripts/start-prod.sh
open http://localhost:8000
```

With Docker:
```bash
docker build -t patientzero .
docker run --rm -p 8000:8000 -e FALKORDB_HOST=host.docker.internal patientzero
```

## Verification done (Oct 16 2026)
- `docker build -t patientzero .` succeeds (Docker 26.1.5 on the dev box).
- `falkordb/falkordb-server:v4.22.1` (same digest as `latest` on Oct 16 2026) ran with
  `REDIS_ARGS="... --appendonly yes"`, and the image ran against it:
  - first boot seeded `fleet:acme`
  - `/` served the UI, and `/health`, `/api/ask`, `/api/fix`, the approve flow and `/api/byo/scan` all worked
  - after `docker restart` of the database, playbooks and incidents were still there (AOF in the container
    filesystem; see the `FALKORDB_DATA_PATH` note above for volumes)
  - stopping the database gave 503 `graph_unavailable` and `/health` showed `"falkordb":"down"`; restarting it
    recovered without restarting the app
- `ingest/verify.py` and the full backend pytest suite (88 tests) pass against that 4.22.1 server as well as the
  local 6.0.1 module.
- On the dev box, Docker bridge networking between containers timed out, so the two containers were tested with
  `--network host`. Railway private networking doesn't have this issue.
- `FALKORDB_ARGS` replaces the module arguments; `TIMEOUT_DEFAULT 10000 TIMEOUT_MAX 30000 THREAD_COUNT 2
  MAX_QUEUED_QUERIES 25` was checked with `GRAPH.CONFIG GET` on 4.22.1 with auth still on.

## Live deployment check (Oct 16 2026, about 00:10 ET)
- `/health`: `falkordb: up`, `graph_loaded: true`, `agent_mode: llm`, `llm_model: gpt-4o-mini`, daily cap 200.
- `/` serves the UI (title "PatientZero: npm supply-chain incident agent on FalkorDB").
- First boot seeded `fleet:acme` (2805 nodes, 5835 edges) in 0.1 s.
- `POST /api/ask` "The qix account was compromised on 2025-09-08..." at depth 4: 13 of 22 services exposed,
  in both `deterministic` and `llm` mode.
- `POST /api/byo/scan` `github.com/axios/axios`: 5 malicious versions reachable (ansi-styles 6.2.2, debug 4.4.2,
  error-ex 1.3.3, strip-ansi 7.1.1, wrap-ansi 9.0.1), all via devDependencies at today's axios HEAD, about 1 s.
- `/api/fix` then `POST /api/incidents/<id>/approve` created a playbook (8 pins); a recall question answered
  from memory and included it.
- Redeployed both the app (`railway up`) and the `falkordb` service: the app log said "graph 'fleet:acme'
  already loaded ... leaving it alone", FalkorDB loaded the AOF from `/data`, and the incident, the playbook
  and the BYO scan graph were all still there.

## Limits and operations
- BYO: 5 scans per minute per IP (in-memory, per instance), at most 60 direct dependencies, package.json up
  to 200 KB, package-lock.json up to 8 MB, 60 s per scan, 15 s per upstream call. Scan graphs `byo:<hash>`
  expire after 6 h and are capped at 50 (oldest deleted first). Cleanup runs on every scan, or on demand with
  `POST /api/byo/cleanup`.
- Upstream calls (raw.githubusercontent.com, api.deps.dev) are cached under `data/raw/byo/`. When the
  network fails, a stale cache entry is used before the scan gives up.
- Admin endpoints (`POST /api/admin/reset`, `POST /api/memory/reset`, `DELETE /api/playbooks`,
  `POST /api/byo/cleanup`) need the `X-Admin-Token` header. Set `ADMIN_TOKEN` on the Railway `patientzero`
  service (a strong random value; stored locally at `~/.config/patientzero/admin-token`, chmod 600).
- **Before recording or judging, reset the demo** so everyone starts clean (incidents, findings, playbooks and
  BYO scan graphs; the fleet data is untouched):
  `curl -XPOST https://patientzero-production-2cb3.up.railway.app/api/admin/reset -H "X-Admin-Token: $(cat ~/.config/patientzero/admin-token)"`
- `INCIDENTS=qix,shai-hulud` is set on the `patientzero` service. On start, `ingest/seed_if_missing.py` adds the
  Shai-Hulud advisories to an existing graph with MERGE only (idempotent, memory untouched).

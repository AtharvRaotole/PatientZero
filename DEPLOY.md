# Deploying PatientZero

Two services:

| Service | What | Image |
|---|---|---|
| `patientzero` | FastAPI API plus the static Next.js UI on one origin | this repo's `Dockerfile` |
| `falkordb` | the graph database | `falkordb/falkordb-server:latest` with a volume on `/data` |

The UI calls `./api/...` on its own origin, so there's no CORS setup and no API URL to configure.
`OPENAI_API_KEY` is only read by the API process. It never reaches the browser bundle: only `NEXT_PUBLIC_*`
variables are compiled into the UI, and the Dockerfile sets just `NEXT_PUBLIC_API_URL=.`.
BYO scans, playbooks and ingest never call an LLM.

## Railway

1. **Create a project** from the GitHub repo `AtharvRaotole/FalkorDB` (branch `main`). Railway picks up
   `railway.json`, which builds the `Dockerfile` and health-checks `/health`.
2. **Add the database service:** New, then Docker Image, then `falkordb/falkordb-server:latest`. Name it `falkordb`.
   - Add a **Volume** mounted at `/data`.
   - Variables:
     - `FALKORDB_PASSWORD` = a long random string
     - `REDIS_ARGS` = `--requirepass ${{FALKORDB_PASSWORD}} --dir /data --appendonly yes --appendfsync everysec --save 60 1`
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
   AGENT_MODE=auto
   ```
   Railway injects `PORT` itself. Then **Settings, Networking, Generate Domain** for the public URL.
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
- `falkordb/falkordb-server:latest` (graph module 4.22.1) ran with `REDIS_ARGS="... --dir /data --appendonly yes"`
  on a volume, and the image ran against it:
  - first boot seeded `fleet:acme`
  - `/` served the UI, and `/health`, `/api/ask`, `/api/fix`, the approve flow and `/api/byo/scan` all worked
  - after `docker restart` of the database, playbooks and incidents were still there (AOF on the volume)
  - stopping the database gave 503 `graph_unavailable` and `/health` showed `"falkordb":"down"`; restarting it
    recovered without restarting the app
- `ingest/verify.py` and the full backend pytest suite (88 tests) pass against that 4.22.1 server as well as the
  local 6.0.1 module.
- On the dev box, Docker bridge networking between containers timed out, so the two containers were tested with
  `--network host`. Railway private networking doesn't have this issue.
- The image's FalkorDB defaults include `TIMEOUT 1000` ms per query. The heaviest query
  (single_points_of_failure) takes about 100 ms, but raise it with `FALKORDB_ARGS=TIMEOUT 5000` if the instance is small.

## Limits and operations
- BYO: 5 scans per minute per IP (in-memory, per instance), at most 60 direct dependencies, package.json up
  to 200 KB, package-lock.json up to 8 MB, 60 s per scan, 15 s per upstream call. Scan graphs `byo:<hash>`
  expire after 6 h and are capped at 50 (oldest deleted first). Cleanup runs on every scan, or on demand with
  `POST /api/byo/cleanup`.
- Upstream calls (raw.githubusercontent.com, api.deps.dev) are cached under `data/raw/byo/`. When the
  network fails, a stale cache entry is used before the scan gives up.
- Memory housekeeping for demos: `POST /api/memory/reset` (incidents and findings), `DELETE /api/playbooks`.

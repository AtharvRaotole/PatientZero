# STATUS (PatientZero)

_Last updated: Thu Oct 15 2026, 11:50 PM ET_

## Completed
- [x] FalkorDB 6.0.1 running locally without Docker: `redis-server` 8.0.2 (apt) + `falkordb-x64.so` module (sha256 verified), port 6379
- [x] `scripts/start-falkordb.sh` (idempotent), `scripts/stop-falkordb.sh`, `scripts/verify_falkordb.py` (Cypher smoke test passes)
- [x] Backend scaffold `backend/` (FastAPI, uv): `/health` (pings FalkorDB), `/api/graph/demo`; CORS for :3001
- [x] Frontend scaffold `frontend/` (Next.js 15 + TS + Tailwind 4): homepage shows live backend/FalkorDB status + demo query; `npm run build` passes
- [x] Verified in headless Chrome: homepage renders "Backend: up" from the API (CORS OK)
- [x] `.env.example`, `.gitignore`, README skeleton, `scripts/dev.sh`

## Next actions
- Implement the PatientZero core journey per FIRST_BRIEF.md / SPEC.md (ingest → graph → incident query → UI paths)
- Add `queries/*.cypher` + data-model diagram to README
- Demo mode with fixtures; demo script; deployment (FalkorDB Cloud free tier + host)

## Assumptions
- Plain `redis-server` + module replaces Docker; FalkorDB Browser (port 3000) is unavailable, so the frontend runs on **3001**
- Generic `falkordb-x64.so` (v6.0.1) works on this Debian 13 box; the rhel9 variant is an alternative
- No LLM key: core journey must be deterministic

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
```

# STATUS (PatientZero)

_Last updated: Fri Oct 16 2026, 12:40 AM ET_

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
- [x] Verification: backend pytest 89 passed; browser e2e 12/12; BYO presets checked in browser (axios exposed, express 4.x runtime clean)

## Next actions
- Final polish pass on screenshots for the README
- Redeploy after pushing UI changes: `sh scripts/redeploy-railway.sh` (ships origin/main; no GitHub auto-deploy)
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

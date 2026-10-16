# PatientZero

> When an npm maintainer gets phished, PatientZero tells you in seconds which of your services are hit, the exact dependency chain, who to wake, and the fix. Built on FalkorDB.

**Track:** FalkorDB Hackathon, Track 1 (Agents That Act on Connected Data)

_Skeleton README. The graph data model, Cypher queries (`queries/*.cypher`), demo script, and AI-assistant disclosure are added as the build progresses._

## Stack
- **Graph DB:** FalkorDB 6.0.1, run as a Redis module (`falkordb.so` loaded into `redis-server`, no Docker)
- **Backend:** Python 3.13, FastAPI, `falkordb` client, managed with `uv` (`backend/`)
- **Frontend:** Next.js 15 (App Router), TypeScript, Tailwind v4 (`frontend/`)

## Prerequisites
- Linux x86_64 with `redis-server` ≥ 7 (`sudo apt-get install -y redis-server libgomp1`)
- Python 3.13 + [uv](https://docs.astral.sh/uv/)
- Node 20+

## Setup & run
```bash
cp .env.example .env                 # defaults work locally
./scripts/start-falkordb.sh          # downloads falkordb.so (v6.0.1) on first run, starts redis-server on :6379
(cd backend && uv sync)
(cd frontend && npm install)

# run everything in the background (idempotent):
./scripts/dev.sh
# or in separate terminals:
cd backend && uv run uvicorn app.main:app --port 8000 --reload
cd frontend && npm run dev           # http://localhost:3001
```

Smoke test FalkorDB: `cd backend && uv run python ../scripts/verify_falkordb.py`

## API
| Method | Path | Description |
|---|---|---|
| GET | `/health` | Backend status + FalkorDB ping + graph list |
| GET | `/api/graph/demo` | Seeds a tiny sample graph and returns nodes/edges |

## Graph data model
_TBD (see SPEC.md)._

## AI assistance disclosure
_TBD._

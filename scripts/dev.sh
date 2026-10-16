#!/usr/bin/env bash
# Start FalkorDB, backend (:8000) and frontend (:3001) in the background. Logs in /tmp.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/start-falkordb.sh"
if ! curl -sf localhost:8000/health >/dev/null; then
  (cd "$ROOT/backend" && nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload > /tmp/backend.log 2>&1 &)
  echo "backend starting on :8000 (log /tmp/backend.log)"
else echo "backend already running on :8000"; fi
if ! curl -sf -o /dev/null localhost:3001; then
  (cd "$ROOT/frontend" && nohup npm run dev > /tmp/frontend.log 2>&1 &)
  echo "frontend starting on :3001 (log /tmp/frontend.log)"
else echo "frontend already running on :3001"; fi

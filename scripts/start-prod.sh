#!/bin/sh
# Container entrypoint (also runnable locally to mirror the container; see DEPLOY.md).
# 1. Seed the demo graph if it is missing (never wipes, so incident/playbook memory survives redeploys).
# 2. Serve the API and the static UI on $PORT.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python}"
if [ "${SEED_ON_START:-true}" = "true" ]; then
  "$PY" "$ROOT/ingest/seed_if_missing.py" || echo "seed: skipped (FalkorDB unreachable); the API will report 503 until it is up"
fi
cd "$ROOT/backend"
exec "$PY" -m uvicorn app.serve:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'

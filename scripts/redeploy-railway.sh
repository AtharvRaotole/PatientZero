#!/bin/sh
# Redeploy the PatientZero app service on Railway from the pushed origin/main.
# Deploys a clean `git archive` of origin/main, so uncommitted local edits never ship.
# Needs a Railway project token: $RAILWAY_PROJECT_TOKEN, else ~/.config/patientzero/railway-project-token,
# else $RAILWAY_TOKEN (only if that is itself a project token; account tokens fail with "Not signed in").
# The FalkorDB service and its /data volume are not touched; the graph and memory survive.
set -eu
REF="${1:-origin/main}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN_FILE="${HOME}/.config/patientzero/railway-project-token"
if [ -n "${RAILWAY_PROJECT_TOKEN:-}" ]; then
  RAILWAY_TOKEN="$RAILWAY_PROJECT_TOKEN"
elif [ -f "$TOKEN_FILE" ]; then
  RAILWAY_TOKEN="$(cat "$TOKEN_FILE")"
fi
unset RAILWAY_API_TOKEN
[ -n "${RAILWAY_TOKEN:-}" ] || { echo "set RAILWAY_TOKEN (project token) first" >&2; exit 1; }
export RAILWAY_TOKEN
command -v railway >/dev/null 2>&1 || PATH="$(npm prefix -g)/bin:$PATH"
cd "$ROOT"
git fetch -q origin
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
git archive "$REF" | tar -x -C "$OUT"
echo "deploying $(git rev-parse --short "$REF") to Railway service patientzero"
cd "$OUT" && railway up --service patientzero --detach 2>&1 | grep -v -i -E "agent tooling|setup agent" || true
echo "watch: curl -s https://patientzero-production-2cb3.up.railway.app/health"

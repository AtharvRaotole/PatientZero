#!/usr/bin/env sh
# Wipe demo memory on the deployed app: Incident/Finding nodes, Playbooks, and every BYO scan graph.
# The fleet graph (services, packages, advisories) is untouched.
#   sh scripts/reset-live.sh                          # live Railway app
#   BASE_URL=http://localhost:8000 sh scripts/reset-live.sh
# Token: $ADMIN_TOKEN, else ~/.config/patientzero/admin-token. It is sent as a header and never printed.
set -eu
BASE_URL="${BASE_URL:-https://patientzero-production-2cb3.up.railway.app}"
BASE_URL="${BASE_URL%/}"
export BASE_URL
TOKEN_FILE="${ADMIN_TOKEN_FILE:-$HOME/.config/patientzero/admin-token}"
TOKEN="${ADMIN_TOKEN:-$(cat "$TOKEN_FILE" 2>/dev/null || true)}"
[ -n "$TOKEN" ] || { echo "no admin token: set ADMIN_TOKEN or create $TOKEN_FILE" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

code=$(curl -sS -o /tmp/pz-reset.json -w '%{http_code}' -X POST "$BASE_URL/api/admin/reset" -H "X-Admin-Token: $TOKEN")
[ "$code" = 200 ] || { echo "reset failed: HTTP $code $(cat /tmp/pz-reset.json)" >&2; exit 1; }
jq -r '"reset \(env.BASE_URL // ""): deleted \(.incidents_and_findings) incident/finding node(s), \(.playbooks) playbook(s), \(.byo_graphs|length) BYO graph(s)"' /tmp/pz-reset.json

left=$(curl -sS "$BASE_URL/api/incidents" | jq '(.incidents // []) | length')
pbs=$(curl -sS "$BASE_URL/api/playbooks" | jq '(.playbooks // []) | length')
[ "$left" = 0 ] && [ "$pbs" = 0 ] || { echo "after reset: $left incident(s), $pbs playbook(s) remain" >&2; exit 1; }
echo "ok: $BASE_URL has 0 incidents and 0 playbooks"

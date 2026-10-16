#!/usr/bin/env sh
# Seed the "approve, then remember" demo beat on a clean deploy:
#   1. admin reset (scripts/reset-live.sh)
#   2. POST /api/fix  "maintainer qix was phished"   -> writes an Incident with per-service patches
#   3. approve that incident as approved_by=atharv    -> writes a Playbook linked to the Incident
#   4. verify a bare "qix" ask answers from graph memory and carries the playbook
#   sh scripts/seed-demo-memory.sh                    # live Railway app
#   BASE_URL=http://localhost:8000 sh scripts/seed-demo-memory.sh
# Every step runs deterministically (mode=deterministic), so the seed costs no LLM tokens.
set -eu
BASE_URL="${BASE_URL:-https://patientzero-production-2cb3.up.railway.app}"
BASE_URL="${BASE_URL%/}"
export BASE_URL
HERE="$(cd "$(dirname "$0")" && pwd)"
TRIGGER="${TRIGGER:-maintainer qix was phished}"
APPROVER="${APPROVER:-atharv}"

sh "$HERE/reset-live.sh"

post() { # path, json body -> body on stdout, fail on non-2xx
  code=$(curl -sS -o /tmp/pz-seed.json -w '%{http_code}' -X POST "$BASE_URL$1" -H 'content-type: application/json' -d "$2")
  case "$code" in 2*) cat /tmp/pz-seed.json ;; *) echo "POST $1 failed: HTTP $code $(cat /tmp/pz-seed.json)" >&2; exit 1 ;; esac
}

fix=$(post /api/fix "$(jq -nc --arg q "$TRIGGER" '{query:$q}')")
inc=$(printf '%s' "$fix" | jq -r '.fix.incident_id // empty')
[ -n "$inc" ] || { echo "fix returned no incident id" >&2; exit 1; }
echo "fix: $inc, $(printf '%s' "$fix" | jq -r '"\(.exposed_count) of \(.fleet_size) services exposed, \(.fix.per_service|length) service patch(es)"')"

ap=$(post "/api/incidents/$inc/approve" "$(jq -nc --arg a "$APPROVER" '{approved_by:$a}')")
pb=$(printf '%s' "$ap" | jq -r '.playbook.id // empty')
[ -n "$pb" ] || { echo "approve returned no playbook" >&2; exit 1; }
echo "approved: $pb by $(printf '%s' "$ap" | jq -r '.playbook.approved_by')"

ask=$(post /api/ask '{"query":"qix","mode":"deterministic"}')
outcome=$(printf '%s' "$ask" | jq -r '.outcome')
mem_inc=$(printf '%s' "$ask" | jq -r '.memory.incidents[0].id // .memory.seen_before.id // empty')
mem_pb=$(printf '%s' "$ask" | jq -r '[.. | objects | select(.id? and (.id|type)=="string" and (.id|startswith("PB-"))) | .id] | first // empty')
echo "bare 'qix': outcome=$outcome memory=$mem_inc playbook=$mem_pb"
[ "$outcome" = answered_from_memory ] || { echo "expected answered_from_memory" >&2; exit 1; }
[ "$mem_inc" = "$inc" ] || { echo "expected memory to cite $inc" >&2; exit 1; }
[ "$mem_pb" = "$pb" ] || { echo "expected playbook $pb in the memory answer" >&2; exit 1; }
echo "ok: $BASE_URL seeded with $inc and $pb"

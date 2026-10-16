#!/usr/bin/env bash
# Idempotently start FalkorDB (redis-server + falkordb.so module) without Docker.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/.falkordb"
PORT="${FALKORDB_PORT:-6379}"
VERSION="${FALKORDB_VERSION:-v6.0.1}"
SO="$DIR/falkordb.so"
mkdir -p "$DIR/data"

if redis-cli -p "$PORT" ping >/dev/null 2>&1; then
  if redis-cli -p "$PORT" MODULE LIST | grep -qi graph; then
    echo "FalkorDB already running on port $PORT"; exit 0
  fi
  echo "ERROR: something else (without FalkorDB module) is on port $PORT" >&2; exit 1
fi

command -v redis-server >/dev/null || { echo "Installing redis-server..."; sudo apt-get install -y redis-server libgomp1; }

if [ ! -f "$SO" ]; then
  echo "Downloading FalkorDB $VERSION module..."
  curl -fsSL -o "$SO" "https://github.com/FalkorDB/FalkorDB/releases/download/$VERSION/falkordb-x64.so"
  chmod +x "$SO"
fi

chmod +x "$SO"
redis-server --port "$PORT" --loadmodule "$SO" \
  --dir "$DIR/data" --save 60 1 --appendonly yes --appendfsync everysec --daemonize yes \
  --logfile "$DIR/falkordb.log" --pidfile "$DIR/falkordb.pid"

for i in $(seq 1 30); do
  redis-cli -p "$PORT" ping >/dev/null 2>&1 && { echo "FalkorDB started on port $PORT (log: $DIR/falkordb.log)"; exit 0; }
  sleep 0.5
done
echo "ERROR: FalkorDB failed to start; see $DIR/falkordb.log" >&2; tail -20 "$DIR/falkordb.log" >&2; exit 1

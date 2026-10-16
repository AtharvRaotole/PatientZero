#!/usr/bin/env bash
redis-cli -p "${FALKORDB_PORT:-6379}" shutdown nosave 2>/dev/null && echo "FalkorDB stopped" || echo "FalkorDB not running"

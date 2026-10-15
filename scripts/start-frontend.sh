#!/usr/bin/env bash
# Start the MoodyMate frontend (Linux/macOS). Usage: scripts/start-frontend.sh [port]
set -euo pipefail
PORT="${1:-3000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"

if command -v lsof >/dev/null 2>&1; then
  pids="$(lsof -ti tcp:"$PORT" -sTCP:LISTEN || true)"
  [ -n "$pids" ] && { echo "Port $PORT held by $pids - stopping"; kill $pids || true; sleep 1; }
fi

rm -rf .next
exec npm run dev -- --port "$PORT"

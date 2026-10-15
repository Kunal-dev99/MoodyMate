#!/usr/bin/env bash
# Start the MoodyMate backend (Linux/macOS). Usage: scripts/start-backend.sh [port]
set -euo pipefail
PORT="${1:-8000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

# Clear any orphan uvicorn --reload tree still holding the port.
if command -v lsof >/dev/null 2>&1; then
  pids="$(lsof -ti tcp:"$PORT" -sTCP:LISTEN || true)"
  [ -n "$pids" ] && { echo "Port $PORT held by $pids - stopping"; kill $pids || true; sleep 1; }
fi

exec .venv/bin/python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port "$PORT"

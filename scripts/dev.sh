#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Python venv
if [ -d "$ROOT_DIR/venv" ]; then
  source "$ROOT_DIR/venv/bin/activate"
  pip install -q -r "$ROOT_DIR/requirements.txt"
fi

export APP_PORT="${APP_PORT:-8080}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export VITE_API_PORT="${VITE_API_PORT:-$APP_PORT}"

BACK_PID=0
FRONT_PID=0

cleanup() {
  if [ "$FRONT_PID" -ne 0 ] && kill -0 "$FRONT_PID" 2>/dev/null; then
    kill "$FRONT_PID" || true
  fi
  if [ "$BACK_PID" -ne 0 ] && kill -0 "$BACK_PID" 2>/dev/null; then
    kill "$BACK_PID" || true
  fi
}
trap cleanup EXIT INT TERM

python3 "$ROOT_DIR/main.py" &
BACK_PID=$!
sleep 2

if command -v npm >/dev/null 2>&1; then
  npm --prefix "$ROOT_DIR/web/frontend" install
  VITE_API_PORT="$VITE_API_PORT" npm --prefix "$ROOT_DIR/web/frontend" run dev &
  FRONT_PID=$!
  echo "Frontend (Vite) running on http://localhost:5173/"
else
  echo "npm not found, serving static SPA on http://localhost:${APP_PORT}/app"
fi

echo "Backend running on http://localhost:${APP_PORT}/"
wait

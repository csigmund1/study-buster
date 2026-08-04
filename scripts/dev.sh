#!/usr/bin/env bash
# study-buster dev orchestrator: run / restart / stop the backend + frontend
# in local mock mode (no API key needed). Ports default to 8000 / 5173.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_DIR="$ROOT/.dev"
mkdir -p "$DEV_DIR"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BE_LOG="$DEV_DIR/backend.log"
FE_LOG="$DEV_DIR/frontend.log"

pids_on_port() { lsof -ti "tcp:$1" 2>/dev/null || true; }

kill_port() {
  local port="$1" pids n=0
  pids="$(pids_on_port "$port")"
  [ -z "$pids" ] && return 0
  kill $pids 2>/dev/null || true
  while [ -n "$(pids_on_port "$port")" ] && [ $n -lt 25 ]; do sleep 0.2; n=$((n + 1)); done
  pids="$(pids_on_port "$port")"
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
}

wait_http() { # url label
  local url="$1" label="$2" n=0
  until curl -sf "$url" >/dev/null 2>&1; do
    n=$((n + 1))
    if [ $n -ge 100 ]; then
      echo "  $label: TIMED OUT (check $DEV_DIR/*.log)"
      return 1
    fi
    sleep 0.3
  done
  echo "  $label: ready"
}

ensure_frontend_deps() {
  [ -d "$ROOT/frontend/node_modules" ] || (cd "$ROOT/frontend" && npm install)
}

# Foreground entrypoints — for `Bash(run_in_background: true)` or a manual foreground run.
fg_backend() {
  cd "$ROOT/backend"
  exec env CARD_GENERATOR="${CARD_GENERATOR:-mock}" \
    uv run uvicorn app.main:app --port "$BACKEND_PORT"
}

fg_frontend() {
  ensure_frontend_deps
  cd "$ROOT/frontend"
  exec npm run dev -- --port "$FRONTEND_PORT" --strictPort
}

# Detached start — for a real terminal (persists via nohup).
up() {
  kill_port "$BACKEND_PORT"
  kill_port "$FRONTEND_PORT"
  ensure_frontend_deps
  (cd "$ROOT/backend" && CARD_GENERATOR="${CARD_GENERATOR:-mock}" \
    nohup uv run uvicorn app.main:app --port "$BACKEND_PORT" >"$BE_LOG" 2>&1 &)
  (cd "$ROOT/frontend" && \
    nohup npm run dev -- --port "$FRONTEND_PORT" --strictPort >"$FE_LOG" 2>&1 &)
  echo "starting (CARD_GENERATOR=${CARD_GENERATOR:-mock})..."
  wait_http "http://localhost:$BACKEND_PORT/health" "backend  http://localhost:$BACKEND_PORT"
  wait_http "http://localhost:$FRONTEND_PORT/" "frontend http://localhost:$FRONTEND_PORT"
  echo "open http://localhost:$FRONTEND_PORT"
}

down() {
  kill_port "$BACKEND_PORT"
  kill_port "$FRONTEND_PORT"
  echo "stopped (ports $BACKEND_PORT, $FRONTEND_PORT)"
}

status() {
  local b f
  b="$(pids_on_port "$BACKEND_PORT")"
  f="$(pids_on_port "$FRONTEND_PORT")"
  echo "backend  (:$BACKEND_PORT): ${b:-stopped}"
  echo "frontend (:$FRONTEND_PORT): ${f:-stopped}"
}

case "${1:-}" in
  up | start)   up ;;
  down | stop)  down ;;
  restart)      down; up ;;
  status)       status ;;
  logs)         tail -n 60 "$BE_LOG" "$FE_LOG" 2>/dev/null ;;
  wait)
    wait_http "http://localhost:$BACKEND_PORT/health" "backend  http://localhost:$BACKEND_PORT"
    wait_http "http://localhost:$FRONTEND_PORT/" "frontend http://localhost:$FRONTEND_PORT"
    echo "open http://localhost:$FRONTEND_PORT"
    ;;
  fg-backend)   fg_backend ;;
  fg-frontend)  fg_frontend ;;
  *)
    echo "usage: scripts/dev.sh {up|down|restart|status|logs|wait|fg-backend|fg-frontend}"
    exit 2
    ;;
esac

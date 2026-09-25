#!/bin/bash
# HHGOA local demo launcher. Double-click this file in Finder.
set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1

API_URL="http://127.0.0.1:8000"
MCP_URL="http://127.0.0.1:8001/mcp/"
UI_URL="http://127.0.0.1:5173"
LOG_DIR="$PROJECT_DIR/runtime/demo-logs"
mkdir -p "$LOG_DIR"

fail() {
  printf '\nHHGOA demo could not start: %s\n' "$1" >&2
  printf 'See logs in %s\n' "$LOG_DIR" >&2
  exit 1
}

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  fail "the project environment is missing at .venv; run the setup in docs/SETUP.md first"
fi
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  fail "the local .env file is missing; copy .env.example and add the TigerGraph credentials"
fi
if ! grep -q '^TG_HOST=.' "$PROJECT_DIR/.env" || ! grep -q '^TG_SECRET=.' "$PROJECT_DIR/.env"; then
  fail "TG_HOST or TG_SECRET is not configured in .env"
fi
if [[ ! -d "$PROJECT_DIR/frontend/node_modules" ]]; then
  fail "frontend dependencies are missing; run npm --prefix frontend install once"
fi

for port in 8000 8001 5173; do
  if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $port is already in use; stop the existing process and retry"
  fi
done

PIDS=()
cleanup() {
  printf '\nStopping HHGOA demo services...\n'
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

printf 'Starting restricted TigerGraph MCP...\n'
"$PROJECT_DIR/.venv/bin/tigergraph-mcp" --env-file "$PROJECT_DIR/.env" \
  --transport streamable-http --host 127.0.0.1 --port 8001 \
  --allowed-tools run_installed_query >"$LOG_DIR/mcp.log" 2>&1 &
PIDS+=("$!")
sleep 2
kill -0 "${PIDS[0]}" 2>/dev/null || fail "TigerGraph MCP stopped during startup"

printf 'Starting FastAPI investigation API...\n'
"$PROJECT_DIR/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 \
  >"$LOG_DIR/api.log" 2>&1 &
PIDS+=("$!")

for _ in {1..30}; do
  if curl -fsS "$API_URL/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS "$API_URL/health" >/dev/null 2>&1 || fail "FastAPI did not become ready; inspect api.log"

printf 'Starting React analyst dashboard...\n'
npm --prefix "$PROJECT_DIR/frontend" run dev -- --host 127.0.0.1 --port 5173 \
  >"$LOG_DIR/frontend.log" 2>&1 &
PIDS+=("$!")

for _ in {1..30}; do
  if curl -fsS "$UI_URL" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS "$UI_URL" >/dev/null 2>&1 || fail "the React dashboard did not become ready; inspect frontend.log"

printf '\nHHGOA demo is ready.\n'
printf 'Dashboard: %s\n' "$UI_URL"
printf 'API health: '; curl -fsS "$API_URL/health"; printf '\n'
printf 'MCP endpoint: %s\n' "$MCP_URL"
printf 'Logs: %s\n\n' "$LOG_DIR"
printf 'Record the browser window now. Press Ctrl-C in this window when finished.\n'
open "$UI_URL" >/dev/null 2>&1 || true

wait

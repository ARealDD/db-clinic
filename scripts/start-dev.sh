#!/usr/bin/env bash
# ------------------------------------------------------------------
#  start-dev.sh — build and start all services for local development
#
#  Services:
#    1. Rust gRPC server  (localhost:50051)
#    2. Python FastAPI     (localhost:8001)
#
#  Usage:
#    scripts/start-dev.sh              # build + start
#    scripts/start-dev.sh --skip-build # start only (use existing binaries)
#    scripts/start-dev.sh --mock       # force mock LLM mode
#    scripts/start-dev.sh --port 9000  # custom FastAPI port
# ------------------------------------------------------------------
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Defaults
SKIP_BUILD=false
MOCK_FLAG=""
GRPC_ADDR="0.0.0.0:50051"
FASTAPI_PORT=8001
GRPC_PID=""
FASTAPI_PID=""
VITE_PID=""

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=true; shift ;;
    --mock)       MOCK_FLAG="--mock"; shift ;;
    --port)       FASTAPI_PORT="$2"; shift 2 ;;
    --grpc-addr)  GRPC_ADDR="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: scripts/start-dev.sh [--skip-build] [--mock] [--port PORT] [--grpc-addr ADDR]"
      exit 0 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

# Cleanup on exit
cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "$VITE_PID" ]    && kill "$VITE_PID" 2>/dev/null    && wait "$VITE_PID" 2>/dev/null    || true
    [ -n "$FASTAPI_PID" ] && kill "$FASTAPI_PID" 2>/dev/null && wait "$FASTAPI_PID" 2>/dev/null || true
    [ -n "$GRPC_PID" ]    && kill "$GRPC_PID" 2>/dev/null    && wait "$GRPC_PID" 2>/dev/null    || true
    echo "Done."
}
trap cleanup EXIT INT TERM

# ---- Step 1: Build ----
if ! $SKIP_BUILD; then
  echo "=== Building project ==="
  bash "$SCRIPT_DIR/build.sh"
  echo ""
fi

# ---- Step 2: Install npm dependencies & start Vite dev server ----
echo "=== Starting Vite dev server (port 3000) ==="
cd "$REPO_ROOT/ui"
if [ ! -d node_modules ]; then
  npm install --silent
fi
npm run dev &
VITE_PID=$!
cd "$REPO_ROOT"

# ---- Step 3: Start Rust gRPC server ----
echo "=== Starting Rust gRPC server ==="
cd "$REPO_ROOT/rust"
cargo run -p agent-grpc-server -- --addr "$GRPC_ADDR" --skills-dir "$REPO_ROOT/skills" --config "$REPO_ROOT/config.toml" $MOCK_FLAG &
GRPC_PID=$!

echo "Waiting for gRPC server..."
for i in $(seq 1 15); do
  if kill -0 "$GRPC_PID" 2>/dev/null; then
    sleep 1
  else
    echo "ERROR: gRPC server exited unexpectedly."
    exit 1
  fi
  # Check if port is open (works on most systems)
  if command -v nc &>/dev/null; then
    GRPC_HOST="${GRPC_ADDR%%:*}"
    GRPC_PORT="${GRPC_ADDR##*:}"
    [ "$GRPC_HOST" = "0.0.0.0" ] && GRPC_HOST="127.0.0.1"
    if nc -z "$GRPC_HOST" "$GRPC_PORT" 2>/dev/null; then
      break
    fi
  elif [ "$i" -ge 5 ]; then
    break
  fi
done
echo ""

# ---- Step 4: Install Python dependencies ----
echo "=== Installing Python dependencies ==="
pip3 install --quiet aiosqlite python-jose bcrypt cryptography 2>/dev/null \
  || pip install --quiet aiosqlite python-jose bcrypt cryptography 2>/dev/null \
  || python3 -m pip install --quiet aiosqlite python-jose bcrypt cryptography 2>/dev/null \
  || python -m pip install --quiet aiosqlite python-jose bcrypt cryptography 2>/dev/null \
  || { echo "WARNING: Could not install Python deps via pip; they may already be present or need manual install."; }

# ---- Step 5: Start Python FastAPI gateway ----
echo "=== Starting FastAPI gateway ==="
cd "$REPO_ROOT/python"
GRPC_ADDR="localhost:${GRPC_ADDR##*:}" \
  ADMIN_USERNAME=admin ADMIN_PASSWORD=Gauss_234 \
  python -m uvicorn server:app \
    --host 0.0.0.0 \
    --port "$FASTAPI_PORT" \
    --reload \
    --reload-dir "$REPO_ROOT/python" &
FASTAPI_PID=$!

sleep 2
echo ""
echo "============================================"
echo "  gRPC server:    $GRPC_ADDR"
[ -n "$MOCK_FLAG" ] && echo "                  (mock mode)"
echo "  Web UI (dev):   http://localhost:3000"
echo "  Web UI (prod):  http://localhost:$FASTAPI_PORT"
echo "  Health:         http://localhost:$FASTAPI_PORT/api/health"
echo "============================================"
echo ""
echo "Press Ctrl+C to stop all services."
echo ""

wait

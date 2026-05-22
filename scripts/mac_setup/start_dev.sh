#!/usr/bin/env bash
# ------------------------------------------------------------------
#  start_dev.sh — macOS dev server launcher for db-clinic
#
#  Sources cargo env, builds (optional), then starts both services:
#    1. Rust gRPC server  (localhost:50051)
#    2. Python FastAPI     (localhost:8001)
#
#  Uses python3 by default (macOS Homebrew convention).
#
#  Usage:
#    scripts/mac_setup/start_dev.sh              # build + start
#    scripts/mac_setup/start_dev.sh --skip-build # start only
#    scripts/mac_setup/start_dev.sh --mock       # force mock LLM mode
#    scripts/mac_setup/start_dev.sh --port 9000  # custom FastAPI port
#    scripts/mac_setup/start_dev.sh --grpc-addr 0.0.0.0:50052
# ------------------------------------------------------------------
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Defaults
SKIP_BUILD=false
MOCK_FLAG=""
GRPC_ADDR="0.0.0.0:50051"
FASTAPI_PORT=8001
GRPC_PID=""
FASTAPI_PID=""
PYTHON="python3"

# ---------- Parse flags ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=true; shift ;;
    --mock)       MOCK_FLAG="--mock"; shift ;;
    --port)       FASTAPI_PORT="$2"; shift 2 ;;
    --grpc-addr)  GRPC_ADDR="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: scripts/mac_setup/start_dev.sh [--skip-build] [--mock] [--port PORT] [--grpc-addr ADDR]"
      exit 0 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

# ---------- macOS: ensure cargo is in PATH ----------
if ! command -v cargo &>/dev/null && [ -f "$HOME/.cargo/env" ]; then
  source "$HOME/.cargo/env"
fi

if ! command -v cargo &>/dev/null; then
  echo "ERROR: cargo not found. Source your Rust env or run scripts/mac_setup/build.sh --setup-only first."
  exit 1
fi

# ---------- Use python3 on macOS ----------
if ! command -v "$PYTHON" &>/dev/null; then
  echo "ERROR: python3 not found."
  exit 1
fi

# ---------- Cleanup on exit ----------
cleanup() {
  echo ""
  echo "Shutting down..."
  [ -n "$FASTAPI_PID" ] && kill "$FASTAPI_PID" 2>/dev/null && wait "$FASTAPI_PID" 2>/dev/null || true
  [ -n "$GRPC_PID" ]    && kill "$GRPC_PID" 2>/dev/null    && wait "$GRPC_PID" 2>/dev/null    || true
  echo "Done."
}
trap cleanup EXIT INT TERM

# ---------- Step 1: Build ----------
if ! $SKIP_BUILD; then
  echo "=== Building project ==="
  bash "$SCRIPT_DIR/build.sh" --yes
  echo ""
fi

# ---------- Step 2: Start Rust gRPC server ----------
echo "=== Starting Rust gRPC server ==="
cd "$REPO_ROOT/rust"
cargo run -p agent-grpc-server -- --addr "$GRPC_ADDR" --skills-dir "$REPO_ROOT/skills" $MOCK_FLAG &
GRPC_PID=$!

echo "Waiting for gRPC server..."
for i in $(seq 1 20); do
  if ! kill -0 "$GRPC_PID" 2>/dev/null; then
    echo "ERROR: gRPC server exited unexpectedly."
    exit 1
  fi
  GRPC_HOST="${GRPC_ADDR%%:*}"
  GRPC_PORT="${GRPC_ADDR##*:}"
  [ "$GRPC_HOST" = "0.0.0.0" ] && GRPC_HOST="127.0.0.1"
  if command -v nc &>/dev/null && nc -z "$GRPC_HOST" "$GRPC_PORT" 2>/dev/null; then
    echo "gRPC server ready on $GRPC_HOST:$GRPC_PORT"
    break
  fi
  sleep 1
done
echo ""

# ---------- Step 3: Start FastAPI gateway ----------
echo "=== Starting FastAPI gateway ==="
cd "$REPO_ROOT/python"
GRPC_ADDR="localhost:${GRPC_ADDR##*:}" \
  $PYTHON -m uvicorn server:app \
    --host 0.0.0.0 \
    --port "$FASTAPI_PORT" \
    --reload \
    --reload-dir "$REPO_ROOT/python" &
FASTAPI_PID=$!

sleep 2
echo ""
echo "============================================"
echo "  gRPC server:   $GRPC_ADDR"
[ -n "$MOCK_FLAG" ] && echo "                  (mock mode)"
echo "  Web UI:        http://localhost:$FASTAPI_PORT"
echo "  Settings:      http://localhost:$FASTAPI_PORT/static/settings.html"
echo "  Chat WS:       ws://localhost:$FASTAPI_PORT/ws/chat/{session_id}"
echo "  Health:        http://localhost:$FASTAPI_PORT/api/health"
echo "============================================"
echo ""
echo "Press Ctrl+C to stop all services."
echo ""

wait

#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
    echo ""
    echo "Shutting down..."
    if [ -n "$GRPC_PID" ]; then
        kill "$GRPC_PID" 2>/dev/null || true
        wait "$GRPC_PID" 2>/dev/null || true
    fi
    if [ -n "$FASTAPI_PID" ]; then
        kill "$FASTAPI_PID" 2>/dev/null || true
        wait "$FASTAPI_PID" 2>/dev/null || true
    fi
    echo "Done."
}
trap cleanup EXIT INT TERM

echo "=== Starting Rust gRPC server (mock mode) ==="
cd "$REPO_ROOT/rust"
cargo run -p agent-grpc-server -- --mock &
GRPC_PID=$!

echo "Waiting for gRPC server to start..."
sleep 3

echo ""
echo "=== Starting FastAPI gateway ==="
cd "$REPO_ROOT/python"
uvicorn server:app --host 0.0.0.0 --port 8000 --reload &
FASTAPI_PID=$!

echo ""
echo "============================================"
echo "  gRPC server:  localhost:50051 (mock mode)"
echo "  Web UI:       http://localhost:8000"
echo "============================================"
echo ""
echo "Press Ctrl+C to stop both servers."

wait

#!/usr/bin/env bash
# ------------------------------------------------------------------
#  run_eval.sh — build db-clinic, start gRPC server, run evaluation.
#
#  Prerequisites: tests/llm/config.local.yaml with a valid API key.
#  Copy tests/llm/config.yaml to config.local.yaml and fill in your keys.
#
#  Usage:
#    tests/run_eval.sh                              # full suite (gRPC)
#    tests/run_eval.sh --direct                     # full suite (direct Rust)
#    tests/run_eval.sh --case case_ops_023.yaml     # single case
#    tests/run_eval.sh --cases <path>               # custom cases dir
#    tests/run_eval.sh --dry-run                    # matcher-only test (no LLM calls)
# ------------------------------------------------------------------
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$SCRIPT_DIR/llm/config.local.yaml"

# Defaults
CASE_ARG=""
DRY_RUN=""
DIRECT=""
CASES_DIR="$SCRIPT_DIR/cases"
EXTRA_ARGS=()

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --direct)     DIRECT="--direct"; shift ;;
    --case)       CASE_ARG="$2"; CASES_DIR="$SCRIPT_DIR/cases/$2"; shift 2 ;;
    --cases)      CASES_DIR="$2"; shift 2 ;;
    --dry-run)    DRY_RUN="--dry-run"; shift ;;
    --help|-h)
      echo "Usage: tests/run_eval.sh [--direct] [--case FILE] [--cases PATH] [--dry-run]"
      echo ""
      echo "  --direct      Use direct Rust agent-eval binary (no gRPC server)"
      echo "  --case FILE   Run a single case (e.g. case_ops_023.yaml)"
      echo "  --cases PATH  Custom cases directory (default: tests/cases)"
      echo "  --dry-run     Matcher-only test (no agent/LLM calls)"
      echo ""
      echo "Prerequisites:"
      echo "  Copy tests/llm/config.yaml to tests/llm/config.local.yaml"
      echo "  and fill in your API key, provider, model, and base_url."
      exit 0 ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

# ------------------------------------------------------------------
# Pre-flight: verify API config
# ------------------------------------------------------------------

if [ -z "$DRY_RUN" ]; then
  check_config=$(python -c "
import sys, os
sys.path.insert(0, '$SCRIPT_DIR/..')
from eval.llm.config import _load_config
cfg = _load_config()
api_key = cfg.get('api_key', '')
provider = cfg.get('provider', '')
model = cfg.get('model', '')
if not api_key:
    print('MISSING_API_KEY')
elif not provider:
    print('MISSING_PROVIDER')
elif not model:
    print('MISSING_MODEL')
else:
    print(f'OK|{provider}|{model}')
" 2>&1)

  case "$check_config" in
    MISSING_API_KEY)
      echo "ERROR: No API key configured."
      echo ""
      echo "  Copy the template and fill in your API key:"
      echo "    cp tests/llm/config.yaml tests/llm/config.local.yaml"
      echo "    # edit tests/llm/config.local.yaml"
      echo ""
      echo "  Or set the DB_CLINIC_API_KEY environment variable."
      exit 1
      ;;
    MISSING_PROVIDER)
      echo "ERROR: No provider configured in $CONFIG_FILE"
      exit 1
      ;;
    MISSING_MODEL)
      echo "ERROR: No model configured in $CONFIG_FILE"
      exit 1
      ;;
    OK*)
      provider=$(echo "$check_config" | cut -d'|' -f2)
      model=$(echo "$check_config" | cut -d'|' -f3)
      echo "==> API config OK (provider=$provider, model=$model)"
      ;;
    *)
      echo "ERROR: Failed to read API config from $CONFIG_FILE"
      echo "  $check_config"
      exit 1
      ;;
  esac
fi

# Ensure gRPC stubs are generated (only needed for the gRPC path)
if [ -z "$DIRECT" ] && [ ! -f "$REPO_ROOT/python/generated/agent_pb2.py" ]; then
  echo "==> Generating Python gRPC stubs"
  bash "$REPO_ROOT/scripts/gen-proto.sh"
fi

# Source cargo env if available
[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"

# Build and start gRPC server (skip for dry-run and direct mode)
if [ -z "$DRY_RUN" ]; then
  if [ -n "$DIRECT" ]; then
    echo "==> Building agent-eval (direct mode)"
    cd "$REPO_ROOT/rust"
    cargo build -p agent-eval 2>&1 | tail -3
  else
    echo "==> Building gRPC server"
    cd "$REPO_ROOT/rust"
    cargo build -p agent-grpc-server 2>&1 | tail -3

    echo "==> Starting gRPC server"
    GRPC_PID=""
    cleanup() {
      if [ -n "$GRPC_PID" ]; then
        echo ""
        echo "==> Stopping gRPC server (pid=$GRPC_PID)"
        kill "$GRPC_PID" 2>/dev/null && wait "$GRPC_PID" 2>/dev/null || true
      fi
    }
    trap cleanup EXIT INT TERM

    cargo run -p agent-grpc-server -- --addr 0.0.0.0:50051 --skills-dir "$REPO_ROOT/skills" &
    GRPC_PID=$!

    # Wait for gRPC server to be ready
    echo "==> Waiting for gRPC server..."
    for i in $(seq 1 15); do
      if ! kill -0 "$GRPC_PID" 2>/dev/null; then
        echo "ERROR: gRPC server exited unexpectedly."
        exit 1
      fi
      if nc -z 127.0.0.1 50051 2>/dev/null; then
        echo "==> gRPC server ready"
        break
      fi
      sleep 1
    done
  fi
fi

# Run evaluation
echo ""
echo "============================================"
echo "  db-clinic evaluation"
echo "  Cases: $CASES_DIR"
echo "============================================"
echo ""

cd "$REPO_ROOT"
RUNNER="tests/engine/run.py"
if [ -n "$DRY_RUN" ]; then
  python "$RUNNER" --dry-run --cases "$CASES_DIR" $DIRECT "${EXTRA_ARGS[@]}"
else
  python "$RUNNER" \
    --cases "$CASES_DIR" \
    --output "$SCRIPT_DIR/results" \
    $DIRECT \
    "${EXTRA_ARGS[@]}"
fi

#!/usr/bin/env bash
# ------------------------------------------------------------------
#  build.sh — one-shot build for all project components
#
#  Usage:
#    scripts/build.sh            # full build (proto + rust + python check)
#    scripts/build.sh --rust     # rust only
#    scripts/build.sh --proto    # regenerate proto stubs only
#    scripts/build.sh --check    # lint/clippy/fmt check (CI mode)
# ------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Parse flags
BUILD_RUST=false
BUILD_PROTO=false
CHECK_ONLY=false
BUILD_ALL=true

for arg in "$@"; do
  case "$arg" in
    --rust)  BUILD_RUST=true; BUILD_ALL=false ;;
    --proto) BUILD_PROTO=true; BUILD_ALL=false ;;
    --check) CHECK_ONLY=true ;;
    --help|-h)
      echo "Usage: scripts/build.sh [--rust] [--proto] [--check]"
      echo "  (no flags)   full build: proto + rust + python dep check"
      echo "  --rust       rust workspace only"
      echo "  --proto      regenerate Python gRPC stubs only"
      echo "  --check      lint mode (fmt --check + clippy + tests)"
      exit 0 ;;
    *) echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# ---- Proto stubs ----
if $BUILD_ALL || $BUILD_PROTO; then
  echo "==> [1/3] Generating Python gRPC stubs"
  bash "$SCRIPT_DIR/gen-proto.sh"
  echo ""
fi

# ---- Rust ----
if $BUILD_ALL || $BUILD_RUST; then
  cd "$REPO_ROOT/rust"
  if $CHECK_ONLY; then
    echo "==> [2/3] Rust format check"
    bash "$SCRIPT_DIR/fmt.sh" --check
    echo ""
    echo "==> [2/3] Rust clippy"
    cargo clippy --workspace --all-targets -- -D warnings
    echo ""
    echo "==> [2/3] Rust tests"
    cargo test --workspace
  else
    echo "==> [2/3] Rust build (workspace)"
    cargo build --workspace
  fi
  echo ""
fi

# ---- Python dependency check ----
if $BUILD_ALL; then
  echo "==> [3/3] Python dependency check"
  cd "$REPO_ROOT"
  python -c "
import importlib, sys
required = ['grpc', 'grpc_tools', 'fastapi', 'uvicorn', 'pydantic', 'yaml', 'bcrypt', 'jose', 'aiosqlite']
pkg_map = {
    'grpc': 'grpcio',
    'grpc_tools': 'grpcio-tools',
    'yaml': 'PyYAML',
    'jose': 'python-jose',
}
missing = []
for mod in required:
    try:
        importlib.import_module(mod)
    except ImportError:
        missing.append(pkg_map.get(mod, mod))
if missing:
    print(f'Missing Python packages: {\" \".join(missing)}', file=sys.stderr)
    print(f'  pip install {\" \".join(missing)}', file=sys.stderr)
    sys.exit(1)
print('All Python dependencies OK')
"
  echo ""
fi

echo "================================================"
if $CHECK_ONLY; then
  echo "  All checks passed."
else
  echo "  Build complete."
  echo ""
  echo "  Next: scripts/start-dev.sh"
fi
echo "================================================"

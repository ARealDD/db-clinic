#!/usr/bin/env bash
# ------------------------------------------------------------------
#  build.sh — macOS build script for db-clinic
#
#  Handles macOS-specific setup: sources cargo env, checks for
#  Homebrew protobuf, ensures Python deps including websockets.
#
#  Usage:
#    scripts/mac_setup/build.sh              # full build
#    scripts/mac_setup/build.sh --rust       # rust workspace only
#    scripts/mac_setup/build.sh --proto      # regenerate gRPC stubs only
#    scripts/mac_setup/build.sh --check      # CI mode (fmt check + clippy + tests)
#    scripts/mac_setup/build.sh --setup-only # only verify/install dependencies
#    scripts/mac_setup/build.sh --yes        # auto-install missing pip packages
# ------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ----- Parse flags -----
BUILD_RUST=false
BUILD_PROTO=false
CHECK_ONLY=false
SETUP_ONLY=false
AUTO_YES=false
BUILD_ALL=true

for arg in "$@"; do
  case "$arg" in
    --rust)        BUILD_RUST=true; BUILD_ALL=false ;;
    --proto)       BUILD_PROTO=true; BUILD_ALL=false ;;
    --check)       CHECK_ONLY=true ;;
    --setup-only)  SETUP_ONLY=true; BUILD_ALL=false ;;
    --yes|-y)      AUTO_YES=true ;;
    --help|-h)
      echo "Usage: scripts/mac_setup/build.sh [--rust] [--proto] [--check] [--setup-only] [--yes]"
      echo "  (no flags)   full build: setup + proto + rust + dep check"
      echo "  --rust       rust workspace only"
      echo "  --proto      regenerate Python gRPC stubs only"
      echo "  --check      lint mode (fmt check + clippy + tests)"
      echo "  --setup-only verify/install dependencies, then exit"
      exit 0 ;;
    *) echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# ----- Helper: print section header -----
section() { echo ""; echo "==> $1"; }

# ================================================================
#  Step 0: macOS environment setup
# ================================================================
section "Setting up macOS environment"

# --- Source cargo if not already on PATH ---
if ! command -v cargo &>/dev/null && [ -f "$HOME/.cargo/env" ]; then
  echo "Sourcing $HOME/.cargo/env for Rust toolchain..."
  source "$HOME/.cargo/env"
fi

# --- Verify Rust ---
if ! command -v cargo &>/dev/null; then
  echo "ERROR: cargo not found. Install Rust first:"
  echo "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
  echo "  source \$HOME/.cargo/env"
  exit 1
fi
echo "Rust: $(rustc --version)"

# --- Verify protoc ---
if ! command -v protoc &>/dev/null; then
  echo "ERROR: protoc not found. Install protobuf:"
  echo "  brew install protobuf"
  exit 1
fi
echo "protoc: $(protoc --version)"

# --- Python detection (prefer python / conda env, then python3) ---
PYTHON=""
if command -v python &>/dev/null; then
  PYTHON="python"
elif command -v python3 &>/dev/null; then
  PYTHON="python3"
fi

if [ -z "$PYTHON" ]; then
  echo "ERROR: Python not found. Install Python 3:"
  echo "  brew install python"
  exit 1
fi
echo "Python: $($PYTHON --version)"

# --- Check and offer to install Python packages ---
REQUIRED_PYTHON_PKGS=(
  "grpcio:grpc"
  "grpcio-tools:grpc_tools"
  "fastapi:fastapi"
  "uvicorn:uvicorn"
  "websockets:websockets"
  "pydantic:pydantic"
  "PyYAML:yaml"
)

echo "Checking Python dependencies..."
MISSING_PKGS=()
MISSING_MODS=()

for entry in "${REQUIRED_PYTHON_PKGS[@]}"; do
  pkg="${entry%%:*}"
  mod="${entry##*:}"
  if ! $PYTHON -c "import $mod" 2>/dev/null; then
    MISSING_PKGS+=("$pkg")
    MISSING_MODS+=("$mod")
  fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
  echo "Missing Python packages: ${MISSING_PKGS[*]}"
  if $AUTO_YES; then
    echo "Installing automatically (--yes)..."
  elif [ -t 0 ]; then
    echo ""
    read -rp "Install them now with pip? [Y/n] " answer
    if [ "$answer" = "n" ] || [ "$answer" = "N" ]; then
      echo "Aborting. Please install manually:  pip install ${MISSING_PKGS[*]}"
      exit 1
    fi
  else
    echo "Run with --yes to auto-install, or install manually:"
    echo "  pip install ${MISSING_PKGS[*]}"
    exit 1
  fi
  $PYTHON -m pip install "${MISSING_PKGS[@]}"
  echo "Done."
else
  echo "All Python dependencies OK."
fi

# --- Stop here if --setup-only ---
if $SETUP_ONLY; then
  echo ""
  echo "================================================"
  echo "  Setup complete."
  echo ""
  echo "  Next: scripts/mac_setup/build.sh       # full build"
  echo "        scripts/mac_setup/start_dev.sh   # start services"
  echo "================================================"
  exit 0
fi

# ================================================================
#  Step 1: Proto stubs
# ================================================================
if $BUILD_ALL || $BUILD_PROTO; then
  section "[1/3] Generating Python gRPC stubs"
  mkdir -p "$REPO_ROOT/python/generated"
  $PYTHON -m grpc_tools.protoc \
    --proto_path="$REPO_ROOT/proto" \
    --python_out="$REPO_ROOT/python/generated" \
    --grpc_python_out="$REPO_ROOT/python/generated" \
    --pyi_out="$REPO_ROOT/python/generated" \
    "$REPO_ROOT/proto/agent.proto"
  echo "Python gRPC stubs generated in $REPO_ROOT/python/generated"
fi

# ================================================================
#  Step 2: Rust build
# ================================================================
if $BUILD_ALL || $BUILD_RUST; then
  cd "$REPO_ROOT/rust"
  if $CHECK_ONLY; then
    section "[2/3] Rust format check"
    cargo fmt --check
    echo ""
    echo "==> [2/3] Rust clippy"
    cargo clippy --workspace --all-targets -- -D warnings
    echo ""
    echo "==> [2/3] Rust tests"
    cargo test --workspace
  else
    section "[2/3] Rust build (workspace)"
    cargo build --workspace
  fi
fi

# ================================================================
#  Done
# ================================================================
if ! $CHECK_ONLY; then
  echo ""
  echo "================================================"
  echo "  Build complete."
  echo ""
  echo "  Start services: scripts/mac_setup/start_dev.sh"
  echo "================================================"
else
  echo ""
  echo "================================================"
  echo "  All checks passed."
  echo "================================================"
fi

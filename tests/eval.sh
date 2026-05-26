#!/usr/bin/env bash
# ------------------------------------------------------------------
#  eval.sh — run db-clinic evaluation from any working directory.
#
#  Usage:
#    tests/eval.sh                                    # full suite (gRPC)
#    tests/eval.sh --direct                           # full suite (direct Rust)
#    tests/eval.sh --direct --case case_ops_023.yaml  # single case
#    tests/eval.sh --dry-run                          # matcher-only test
# ------------------------------------------------------------------
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

python tests/engine/run.py "$@"

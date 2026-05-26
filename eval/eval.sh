#!/usr/bin/env bash
# ------------------------------------------------------------------
#  eval.sh — run db-clinic evaluation from any working directory.
#
#  Usage:
#    eval/eval.sh                                    # full suite (gRPC)
#    eval/eval.sh --direct                           # full suite (direct Rust)
#    eval/eval.sh --direct --case case_ops_023.yaml  # single case
#    eval/eval.sh --dry-run                          # matcher-only test
# ------------------------------------------------------------------
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

python eval/engine/run.py "$@"

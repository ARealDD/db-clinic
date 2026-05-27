#!/usr/bin/env bash
# Drift-check wrapper: see scripts/check_prompt_mirror.py for the comparison.
# Kept as a thin shell shim so `scripts/fmt.sh --check` can call one stable
# entrypoint regardless of how the implementation language evolves.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "check-prompt-mirror: no python interpreter on PATH" >&2
  exit 2
fi

exec "$PY" "$SCRIPT_DIR/check_prompt_mirror.py" "$@"

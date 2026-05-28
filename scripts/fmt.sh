#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# When invoked in --check mode, also verify the Python copy of
# TOOL_USAGE_GUIDANCE has not drifted from the Rust source. The two strings
# must stay byte-for-byte identical because the gateway uses its copy to
# render the system-prompt preview operators see in the settings page.
for arg in "$@"; do
  if [[ "$arg" == "--check" ]]; then
    "$SCRIPT_DIR/check-prompt-mirror.sh"
    break
  fi
done

cd "$REPO_ROOT/rust"
exec cargo fmt "$@"

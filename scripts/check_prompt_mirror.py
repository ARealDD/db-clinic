#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drift check: the Python copy of TOOL_USAGE_GUIDANCE must match the Rust copy
byte-for-byte. Run from `scripts/check-prompt-mirror.sh`, which `fmt.sh --check`
invokes alongside `cargo fmt --check`.

Two files are compared:
  - rust/crates/agent-grpc-server/src/session_store.rs  (const TOOL_USAGE_GUIDANCE)
  - python/tool_usage_guidance.py                       (TOOL_USAGE_GUIDANCE)

Exit code 0 = identical; 1 = drift; 2 = could not extract.
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUST_FILE = REPO_ROOT / "rust" / "crates" / "agent-grpc-server" / "src" / "session_store.rs"
PY_FILE = REPO_ROOT / "python" / "tool_usage_guidance.py"


def _extract_rust(text: str) -> str:
    # const TOOL_USAGE_GUIDANCE: &str = r"...";
    m = re.search(
        r'const\s+TOOL_USAGE_GUIDANCE\s*:\s*&str\s*=\s*r"(.*?)"\s*;',
        text,
        re.DOTALL,
    )
    if not m:
        sys.stderr.write("could not locate Rust TOOL_USAGE_GUIDANCE constant\n")
        sys.exit(2)
    return m.group(1)


def _extract_python(text: str) -> str:
    # TOOL_USAGE_GUIDANCE = """..."""
    m = re.search(
        r'TOOL_USAGE_GUIDANCE\s*=\s*"""(.*?)"""',
        text,
        re.DOTALL,
    )
    if not m:
        sys.stderr.write("could not locate Python TOOL_USAGE_GUIDANCE constant\n")
        sys.exit(2)
    return m.group(1)


def main() -> int:
    try:
        rust_text = RUST_FILE.read_text(encoding="utf-8")
        py_text = PY_FILE.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        sys.stderr.write(f"missing file: {exc.filename}\n")
        return 2

    rust_const = _extract_rust(rust_text)
    py_const = _extract_python(py_text)

    if rust_const == py_const:
        print("OK: TOOL_USAGE_GUIDANCE mirror is in sync")
        return 0

    sys.stderr.write("DRIFT: TOOL_USAGE_GUIDANCE Rust vs Python copies diverge.\n")
    diff = difflib.unified_diff(
        rust_const.splitlines(keepends=True),
        py_const.splitlines(keepends=True),
        fromfile=str(RUST_FILE.relative_to(REPO_ROOT)),
        tofile=str(PY_FILE.relative_to(REPO_ROOT)),
    )
    sys.stderr.writelines(diff)
    return 1


if __name__ == "__main__":
    sys.exit(main())

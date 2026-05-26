#!/usr/bin/env python3
"""
Standalone entry point for db-clinic evaluation.
Use this from any working directory — it handles module path resolution.

Usage:
    python eval/engine/run.py --direct --cases eval/cases/case_ops_023.yaml
    python eval/engine/run.py --cases eval/cases --output eval/results
"""
import sys
from pathlib import Path

# Ensure the repo root is on sys.path so the `eval` package is importable.
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from eval.engine.cli import main

sys.exit(main())

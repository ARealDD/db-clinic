# -*- coding: utf-8 -*-
"""Persistent-data paths resolved from config.toml [storage] section.

Single source of truth so db.py and server.py agree on where the SQLite DB
and session JSONL transcripts live. Imported eagerly; the directories are
created at import time.

Env overrides (for tests):
  META_DB_PATH       — full path to the SQLite file (wins over config.toml)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_DEFAULTS: Dict[str, str] = {
    "data_dir": "data",
    "db_file": "metadatabase.db",
    "sessions_subdir": "sessions",
}


def _load_storage_cfg() -> Dict[str, Any]:
    if tomllib is None:
        return dict(_DEFAULTS)
    cfg_path = _PROJECT_ROOT / "config.toml"
    try:
        with cfg_path.open("rb") as f:
            data = tomllib.load(f)
        return {**_DEFAULTS, **data.get("storage", {})}
    except FileNotFoundError:
        return dict(_DEFAULTS)
    except Exception:
        # Defensive: malformed TOML should not stop startup; fall back to defaults.
        return dict(_DEFAULTS)


def _resolve(p: str) -> Path:
    path = Path(p)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


_cfg = _load_storage_cfg()

DATA_DIR: Path = _resolve(_cfg["data_dir"])
SESSIONS_DIR: Path = DATA_DIR / _cfg["sessions_subdir"]

_env_db = os.environ.get("META_DB_PATH")
DB_PATH: Path = Path(_env_db) if _env_db else DATA_DIR / _cfg["db_file"]

# Ensure parent directories exist before any caller tries to write.
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

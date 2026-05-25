"""
llm/config.py — LLM API configuration loader.

Priority: env var > config.local.yaml > config.yaml > default.

Used by the eval adapter (gRPC CreateSession) and the judge client (dba-bench scoring).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def _load_config() -> dict[str, str]:
    """Load LLM config from YAML files, with env vars as highest priority."""
    config: dict[str, str] = {}
    llm_dir = Path(__file__).resolve().parent

    for cfg_file in (llm_dir / "config.yaml", llm_dir / "config.local.yaml"):
        if cfg_file.is_file():
            with open(cfg_file) as f:
                data = yaml.safe_load(f) or {}
            config.update({k: str(v) for k, v in data.items() if v})

    for key in ("provider", "api_key", "base_url", "model"):
        env_val = os.environ.get(f"DB_CLINIC_{key.upper()}", "")
        if env_val:
            config[key] = env_val

    return config

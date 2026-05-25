"""
Benchmark case schema — data classes for loading and representing benchmark cases.

A BenchmarkCase maps directly to the YAML format defined in experiments/template.yaml.

Structure:
    BenchmarkCase
    ├── case_id: str
    ├── user_report: str               — initial symptom description sent to agent
    ├── information_inventory: dict    — artifact_key -> Artifact (the evidence pool)
    │       Artifact
    │       ├── key: str
    │       ├── type: str              — text/sql | text/plain | text/json
    │       ├── description: str
    │       ├── retrieval_hints: list[str]
    │       └── content: str
    └── oracle
            ├── canonical_root_causes: list[OracleRootCause]
            └── acceptable_actions:    list[OracleAction]
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Artifact:
    """A single piece of evidence that can be retrieved from the information pool."""
    key: str
    type: str                       # text/sql | text/plain | text/json | text/yaml
    description: str
    retrieval_hints: list[str]
    content: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "type": self.type,
            "description": self.description,
            "retrieval_hints": self.retrieval_hints,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, key: str, data: dict) -> "Artifact":
        return cls(
            key=key,
            type=data.get("type", "text/plain"),
            description=data.get("description", ""),
            retrieval_hints=data.get("retrieval_hints", []),
            content=data.get("content", ""),
        )


@dataclass
class OracleRootCause:
    """A canonical root cause entry in the oracle."""
    root_cause_id: str
    summary: str
    description: str
    matching_hints: list[str]
    evidence_hint: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root_cause_id": self.root_cause_id,
            "summary": self.summary,
            "description": self.description,
            "matching_hints": self.matching_hints,
            "evidence_hint": self.evidence_hint,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OracleRootCause":
        return cls(
            root_cause_id=data.get("root_cause_id", ""),
            summary=data.get("summary", ""),
            description=data.get("description", ""),
            matching_hints=data.get("matching_hints", []),
            evidence_hint=data.get("evidence_hint", []),
        )


@dataclass
class OracleAction:
    """An acceptable tuning action entry in the oracle.

    `primary` marks "this is the action that actually fixes the problem"
    (vs stopgap/diagnostic actions). When at least one action in a case is
    marked primary AND EvalConfig.require_primary_action is True, action_hit
    requires matching at least one primary action — preventing a fluff hit
    when the oracle has 5+ acceptable actions.
    """
    action_id: str
    summary: str
    description: str
    command_templates: list[str]
    matching_hints: list[str]
    primary: bool = False

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "summary": self.summary,
            "description": self.description,
            "command_templates": self.command_templates,
            "matching_hints": self.matching_hints,
            "primary": self.primary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OracleAction":
        return cls(
            action_id=data.get("action_id", ""),
            summary=data.get("summary", ""),
            description=data.get("description", ""),
            command_templates=data.get("command_templates", []),
            matching_hints=data.get("matching_hints", []),
            primary=bool(data.get("primary", False)),
        )


@dataclass
class BenchmarkCase:
    """A complete benchmark evaluation case."""
    case_id: str
    user_report: str
    information_inventory: dict[str, Artifact]      # artifact_key -> Artifact
    canonical_root_causes: list[OracleRootCause]
    acceptable_actions: list[OracleAction]
    source_file: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "user_report": self.user_report,
            "information_inventory": {k: v.to_dict() for k, v in self.information_inventory.items()},
            "canonical_root_causes": [rc.to_dict() for rc in self.canonical_root_causes],
            "acceptable_actions": [a.to_dict() for a in self.acceptable_actions],
            "source_file": self.source_file,
        }

    @classmethod
    def from_dict(cls, data: dict, source_file: str = "") -> "BenchmarkCase":
        # Parse information_inventory
        raw_inventory: dict[str, Any] = data.get("information_inventory", {})
        inventory: dict[str, Artifact] = {
            key: Artifact.from_dict(key, val)
            for key, val in raw_inventory.items()
        }

        # Parse oracle
        oracle = data.get("oracle", {})
        root_causes = [
            OracleRootCause.from_dict(rc)
            for rc in oracle.get("canonical_root_causes", [])
        ]
        actions = [
            OracleAction.from_dict(a)
            for a in oracle.get("acceptable_actions", [])
        ]

        # Parse user_report — may be nested under initial_observation
        initial_obs = data.get("initial_observation", {})
        user_report: str = (
            initial_obs.get("user_report", "")
            if isinstance(initial_obs, dict)
            else str(initial_obs)
        )
        # Fallback: top-level user_report
        if not user_report:
            user_report = data.get("user_report", "")

        return cls(
            case_id=data.get("case_id", ""),
            user_report=user_report.strip(),
            information_inventory=inventory,
            canonical_root_causes=root_causes,
            acceptable_actions=actions,
            source_file=source_file,
        )

    @classmethod
    def load_yaml(cls, path: str) -> "BenchmarkCase":
        """Load a single benchmark case from a YAML file."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data, source_file=path)

    @classmethod
    def load_dir(cls, dir_path: str) -> list["BenchmarkCase"]:
        """Load all YAML benchmark cases from a directory (sorted by filename)."""
        p = Path(dir_path)
        if not p.exists():
            raise FileNotFoundError(f"Cases directory not found: {dir_path}")

        cases: list["BenchmarkCase"] = []
        yaml_files = sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml"))
        for yaml_file in yaml_files:
            try:
                case = cls.load_yaml(str(yaml_file))
                cases.append(case)
            except Exception as e:
                print(f"[WARN] Failed to load {yaml_file}: {e}")
        return cases

    def summary(self) -> str:
        return (
            f"BenchmarkCase({self.case_id!r}, "
            f"{len(self.information_inventory)} artifacts, "
            f"{len(self.canonical_root_causes)} root causes, "
            f"{len(self.acceptable_actions)} actions)"
        )

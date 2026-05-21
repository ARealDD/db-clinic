"""
Skill Registry — loads and indexes skills from two directories:

  skills/case/      — Case Skills: structured diagnostic playbooks
  skills/knowledge/ — Knowledge Skills: free-form GaussDB factual references

Case Skills support two file formats:
  - YAML (.yaml / .yml): original structured format
  - Markdown (.md): DBA case study format with YAML frontmatter + Markdown body
    The Markdown format mirrors how operators document real troubleshooting cases:
      ## 根因        — one-sentence root cause
      ## 诊断步骤   — ordered steps, each with 操作/现象/现象分析
      ## 恢复手段   — per-root-cause recovery with 描述 + 具体命令

Knowledge Skills use a simpler format:
  - YAML frontmatter: id, name, category, keywords, tags
  - Markdown body: free-form factual content (no rigid sections)
"""
from __future__ import annotations

import os
import re
import yaml
from dataclasses import dataclass
from typing import Optional

from skills.base import Skill, DiagnosticStep, RootCause, KnowledgeSkill


def _load_skill_from_dict(d: dict) -> Skill:
    """Parse a YAML dict into a Skill object."""
    steps = []
    for s in d.get("diagnosis_steps", []):
        steps.append(DiagnosticStep(
            step=s.get("step", 0),
            action=s.get("action", ""),
            tool=s.get("tool"),
            expected_evidence=s.get("expected_evidence"),
            condition=s.get("condition"),
            phenomenon=s.get("phenomenon"),
            phenomenon_analysis=s.get("phenomenon_analysis"),
        ))

    root_causes = []
    for rc in d.get("root_causes", []):
        root_causes.append(RootCause(
            id=rc.get("id", ""),
            description=rc.get("description", ""),
            probability=rc.get("probability", 0.5),
            indicators=rc.get("indicators", []),
            recommendations=rc.get("recommendations", []),
            sql_fixes=rc.get("sql_fixes", []),
            config_fixes=rc.get("config_fixes", []),
            recovery_description=rc.get("recovery_description", ""),
            recovery_commands=rc.get("recovery_commands", []),
        ))

    return Skill(
        id=d["id"],
        name=d["name"],
        category=d.get("category", "general"),
        description=d.get("description", ""),
        version=d.get("version", "1.0"),
        author=d.get("author", ""),
        symptoms=d.get("symptoms", []),
        keywords=d.get("keywords", []),
        triggers=d.get("triggers", []),
        evidence_required=d.get("evidence_required", []),
        evidence_optional=d.get("evidence_optional", []),
        diagnosis_steps=steps,
        root_cause_summary=d.get("root_cause_summary", ""),
        root_causes=root_causes,
        case_examples=d.get("case_examples", []),
        references=d.get("references", []),
    )


# ---------------------------------------------------------------------------
# Markdown skill parser
# ---------------------------------------------------------------------------

def _split_frontmatter(text: str) -> tuple[str, str]:
    """
    Split a Markdown file into (frontmatter_yaml, body_markdown).
    Expects the file to start with '---' delimiter.
    Returns ('', text) if no frontmatter found.
    """
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    frontmatter = text[3:end].strip()
    body = text[end + 4:].strip()
    return frontmatter, body


def _extract_section(body: str, header: str) -> str:
    """
    Extract the text content of a top-level '## <header>' section.
    Returns empty string if not found.
    """
    pattern = re.compile(
        r"^##\s+" + re.escape(header) + r"\s*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(body)
    return m.group(1).strip() if m else ""


def _extract_field(block: str, label: str) -> str:
    """
    Extract the text after a bold label like '**操作**：' or '**描述**：'.
    Returns all text up to the next bold label or end of block.
    """
    pattern = re.compile(
        r"\*\*" + re.escape(label) + r"\*\*[：:]\s*(.*?)(?=\*\*\w|\Z)",
        re.DOTALL,
    )
    m = pattern.search(block)
    if not m:
        return ""
    return m.group(1).strip()


def _extract_code_blocks(text: str) -> list[str]:
    """Extract content of all fenced code blocks (``` ... ```) in text."""
    pattern = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
    return [m.group(1).strip() for m in pattern.finditer(text)]


def _parse_step_block(block: str, step_num: int) -> DiagnosticStep:
    """Parse a single '### 步骤 N' block into a DiagnosticStep."""
    action_text = _extract_field(block, "操作")

    # Extract tool name from bullet "- 工具：`tool_name`" or "- Tool: tool_name"
    tool_match = re.search(r"-\s*工具[：:]\s*`?(\w+)`?", block)
    tool = tool_match.group(1) if tool_match else None

    # Extract condition from bullet "- 条件：..."
    cond_match = re.search(r"-\s*条件[：:]\s*(.+)", block)
    condition = cond_match.group(1).strip() if cond_match else None

    # The action description is the first line / sentences before the bullet list
    action_lines = []
    for line in action_text.splitlines():
        if line.startswith("-"):
            break
        if line.strip():
            action_lines.append(line.strip())
    action = " ".join(action_lines) if action_lines else action_text.splitlines()[0].strip() if action_text else ""

    phenomenon = _extract_field(block, "现象")
    phenomenon_analysis = _extract_field(block, "现象分析")

    return DiagnosticStep(
        step=step_num,
        action=action,
        tool=tool,
        condition=condition,
        phenomenon=phenomenon or None,
        phenomenon_analysis=phenomenon_analysis or None,
    )


def _parse_recovery_block(block: str, idx: int) -> RootCause:
    """Parse a single '### 根因...' recovery block into a RootCause."""
    description = _extract_field(block, "描述")
    if not description:
        # Fall back: first non-empty line of the block
        for line in block.splitlines():
            if line.strip() and not line.startswith("#"):
                description = line.strip()
                break

    # Recovery commands come from fenced code blocks
    recovery_commands = _extract_code_blocks(block)

    return RootCause(
        id=f"root_cause_{idx}",
        description=description,
        recovery_description=description,
        recovery_commands=recovery_commands,
    )


def _load_skill_from_markdown(text: str) -> Optional[Skill]:
    """
    Parse a Markdown skill file with YAML frontmatter into a Skill object.

    Expected structure:
      ---
      <YAML frontmatter: id, name, keywords, triggers, evidence_required, ...>
      ---

      ## 根因
      <one-sentence root cause>

      ## 诊断步骤
      ### 步骤 1
      **操作**：...
      - 工具：`tool_name`
      - 条件：...
      **现象**：...
      **现象分析**：...

      ## 恢复手段
      ### 根因：<title>
      **描述**：...
      **具体命令**：
      ```sql
      ...
      ```
    """
    frontmatter_yaml, body = _split_frontmatter(text)
    if not frontmatter_yaml:
        return None

    try:
        meta = yaml.safe_load(frontmatter_yaml)
    except yaml.YAMLError:
        return None

    if not meta or not isinstance(meta, dict) or "id" not in meta:
        return None

    # --- 根因 section ---
    root_cause_summary = _extract_section(body, "根因")

    # --- 诊断步骤: prefer frontmatter structured data, fall back to Markdown body ---
    diagnosis_steps: list[DiagnosticStep] = []
    if "diagnosis_steps" in meta:
        for s in meta.get("diagnosis_steps") or []:
            diagnosis_steps.append(DiagnosticStep(
                step=s.get("step", 0),
                action=s.get("action", ""),
                tool=s.get("tool"),
                expected_evidence=s.get("expected_evidence"),
                condition=s.get("condition"),
                phenomenon=s.get("phenomenon"),
                phenomenon_analysis=s.get("phenomenon_analysis"),
            ))
    else:
        steps_section = _extract_section(body, "诊断步骤")
        if steps_section:
            step_blocks = re.split(r"(?=^###\s+步骤\s+\d+)", steps_section, flags=re.MULTILINE)
            for block in step_blocks:
                num_match = re.match(r"###\s+步骤\s+(\d+)", block.strip())
                if not num_match:
                    continue
                step_num = int(num_match.group(1))
                diagnosis_steps.append(_parse_step_block(block, step_num))

    # --- 恢复手段: prefer frontmatter structured data, fall back to Markdown body ---
    root_causes: list[RootCause] = []
    if "root_causes" in meta:
        for rc in meta.get("root_causes") or []:
            root_causes.append(RootCause(
                id=rc.get("id", ""),
                description=rc.get("description", ""),
                probability=rc.get("probability", 0.5),
                indicators=rc.get("indicators", []),
                recommendations=rc.get("recommendations", []),
                sql_fixes=rc.get("sql_fixes", []),
                config_fixes=rc.get("config_fixes", []),
                recovery_description=rc.get("recovery_description", ""),
                recovery_commands=rc.get("recovery_commands", []),
            ))
    else:
        recovery_section = _extract_section(body, "恢复手段")
        if recovery_section:
            recovery_blocks = re.split(r"(?=^###\s+)", recovery_section, flags=re.MULTILINE)
            rc_idx = 1
            for block in recovery_blocks:
                if not block.strip() or not block.strip().startswith("###"):
                    continue
                root_causes.append(_parse_recovery_block(block, rc_idx))
                rc_idx += 1

    return Skill(
        id=meta["id"],
        name=meta.get("name", meta["id"]),
        category=meta.get("category", "general"),
        description=meta.get("description", ""),
        version=meta.get("version", "1.0"),
        author=meta.get("author", ""),
        symptoms=meta.get("symptoms", []),
        keywords=meta.get("keywords", []),
        triggers=meta.get("triggers", []),
        evidence_required=meta.get("evidence_required", []),
        evidence_optional=meta.get("evidence_optional", []),
        diagnosis_steps=diagnosis_steps,
        root_cause_summary=root_cause_summary,
        root_causes=root_causes,
        case_examples=meta.get("case_examples", []),
        references=meta.get("references", []),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class SkillRegistry:
    """
    Loads all case skill files from the case skills directory (default: skills/case).
    Supports YAML (.yaml / .yml) and Markdown (.md) formats.
    Provides lookup by ID and search by keyword.
    """

    def __init__(self, catalog_dir: str = "skills/case"):
        self._skills: dict[str, Skill] = {}
        self._load_catalog(catalog_dir)

    def _load_catalog(self, catalog_dir: str) -> None:
        if not os.path.isdir(catalog_dir):
            return
        for entry in sorted(os.listdir(catalog_dir)):
            path = os.path.join(catalog_dir, entry)
            try:
                if os.path.isdir(path):
                    skill_md = os.path.join(path, "SKILL.md")
                    if os.path.isfile(skill_md):
                        self._load_markdown_skill(skill_md)
                elif entry.endswith((".yaml", ".yml")):
                    self._load_yaml_skill(path)
                elif entry.endswith(".md"):
                    self._load_markdown_skill(path)
            except Exception as e:
                print(f"[SkillRegistry] Failed to load {path}: {e}")

    def _load_yaml_skill(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict) and "id" in data:
            skill = _load_skill_from_dict(data)
            self._skills[skill.id] = skill

    def _load_markdown_skill(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        skill = _load_skill_from_markdown(text)
        if skill:
            self._skills[skill.id] = skill
        else:
            print(f"[SkillRegistry] Skipped {path}: missing id or frontmatter")

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def by_category(self, category: str) -> list[Skill]:
        return [s for s in self._skills.values() if s.category == category]

    def add_skill(self, skill: Skill) -> None:
        """Add or update a skill (for dynamic skill registration)."""
        self._skills[skill.id] = skill

    def __len__(self) -> int:
        return len(self._skills)

    def __repr__(self) -> str:
        return f"<SkillRegistry skills={list(self._skills.keys())}>"


# ---------------------------------------------------------------------------
# Knowledge Skill Registry
# ---------------------------------------------------------------------------

class KnowledgeSkillRegistry:
    """
    Loads and indexes knowledge skills from the knowledge directory.

    Knowledge skills use a simpler format than case skills:
    - YAML frontmatter: id, name, category, description, keywords, tags
    - Markdown body: free-form factual content (no rigid sections required)

    They live in a separate directory (default: skills/knowledge) to make
    it easy to browse and maintain GaussDB-specific reference material.
    """

    def __init__(self, knowledge_dir: str = "skills/knowledge"):
        self._skills: dict[str, KnowledgeSkill] = {}
        if os.path.isdir(knowledge_dir):
            self._load_catalog(knowledge_dir)
        else:
            print(f"[KnowledgeSkillRegistry] Directory not found: {knowledge_dir}")

    def _load_catalog(self, knowledge_dir: str) -> None:
        for entry in sorted(os.listdir(knowledge_dir)):
            path = os.path.join(knowledge_dir, entry)
            try:
                if os.path.isdir(path):
                    skill_md = os.path.join(path, "SKILL.md")
                    if os.path.isfile(skill_md):
                        self._load_knowledge_skill(skill_md)
                elif entry.endswith(".md"):
                    self._load_knowledge_skill(path)
            except Exception as e:
                print(f"[KnowledgeSkillRegistry] Failed to load {path}: {e}")

    def _load_knowledge_skill(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            raw = f.read()

        frontmatter_yaml, body = _split_frontmatter(raw)
        if not frontmatter_yaml:
            print(f"[KnowledgeSkillRegistry] Skipped {path}: no YAML frontmatter")
            return

        try:
            meta = yaml.safe_load(frontmatter_yaml)
        except yaml.YAMLError as e:
            print(f"[KnowledgeSkillRegistry] YAML parse error in {path}: {e}")
            return

        if not meta or not isinstance(meta, dict) or "id" not in meta:
            print(f"[KnowledgeSkillRegistry] Skipped {path}: missing 'id' field")
            return

        skill = KnowledgeSkill(
            id=meta["id"],
            name=meta.get("name", meta["id"]),
            category=meta.get("category", "general"),
            description=meta.get("description", ""),
            keywords=meta.get("keywords", []),
            tags=meta.get("tags", []),
            content=body.strip(),
            version=str(meta.get("version", "1.0")),
            author=meta.get("author", ""),
        )
        self._skills[skill.id] = skill

    def get(self, skill_id: str) -> KnowledgeSkill | None:
        return self._skills.get(skill_id)

    def all(self) -> list[KnowledgeSkill]:
        return list(self._skills.values())

    def by_category(self, category: str) -> list[KnowledgeSkill]:
        return [s for s in self._skills.values() if s.category == category]

    def __len__(self) -> int:
        return len(self._skills)

    def __repr__(self) -> str:
        return f"<KnowledgeSkillRegistry skills={list(self._skills.keys())}>"

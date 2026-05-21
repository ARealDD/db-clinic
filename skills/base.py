"""
Skill base class.

A Skill represents a structured piece of DBA diagnostic knowledge. It captures:
  - Symptom patterns (keywords/phrases that trigger matching)
  - Required evidence (what data must be collected)
  - Diagnosis steps (ordered investigation procedure)
  - Diagnostic commands (SQL/shell commands to collect evidence)
  - Known root causes (structured causes with probability weights)
  - Recommendations (concrete actions indexed by root cause)
  - References (documentation, known bugs, case numbers)

Skills are authored as YAML files and loaded into the SkillRegistry.
The SkillMatcher selects relevant skills for each diagnostic session
and injects them as structured context into the agent's prompt.

Design principle: Skills encode experience that would otherwise be
buried in system prompts or tribal knowledge. They make the agent's
reasoning explicit, auditable, and continuously improvable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DiagnosticStep:
    step: int
    action: str                           # 操作：What to do
    tool: Optional[str] = None            # Tool name to invoke (if any)
    expected_evidence: Optional[str] = None  # Evidence type to collect
    condition: Optional[str] = None       # Only execute if condition is met
    phenomenon: Optional[str] = None      # 现象：Objective observation after running the operation
    phenomenon_analysis: Optional[str] = None  # 现象分析：Preliminary conclusions drawn from phenomenon


@dataclass
class RootCause:
    id: str
    description: str
    probability: float = 0.5             # 0.0 to 1.0
    indicators: list[str] = field(default_factory=list)  # Evidence patterns
    recommendations: list[str] = field(default_factory=list)
    sql_fixes: list[str] = field(default_factory=list)
    config_fixes: list[str] = field(default_factory=list)
    recovery_description: str = ""        # 恢复手段描述：What to do for recovery
    recovery_commands: list[str] = field(default_factory=list)  # 具体命令：Shell/SQL commands


@dataclass
class Skill:
    id: str
    name: str
    category: str                         # slow_sql | lock | index | vacuum | etc.
    description: str
    version: str = "1.0"
    author: str = ""

    # Matching signals
    symptoms: list[str] = field(default_factory=list)       # Natural language symptom descriptions
    keywords: list[str] = field(default_factory=list)       # Trigger keywords for matching
    triggers: list[str] = field(default_factory=list)       # Regex patterns on input text

    # Investigation procedure
    evidence_required: list[str] = field(default_factory=list)   # EvidenceType values needed
    evidence_optional: list[str] = field(default_factory=list)   # Nice-to-have evidence
    diagnosis_steps: list[DiagnosticStep] = field(default_factory=list)

    # Structured knowledge
    root_cause_summary: str = ""          # 根因：One-sentence root cause description
    root_causes: list[RootCause] = field(default_factory=list)

    # Examples from historical cases
    case_examples: list[dict] = field(default_factory=list)

    # References
    references: list[str] = field(default_factory=list)

    # Matching score cache (set by matcher)
    _match_score: float = field(default=0.0, repr=False)

    def to_prompt_context(self, detail_level: str = "full") -> str:
        """
        Render this skill as structured text to inject into the agent prompt.

        detail_level:
          "summary"  — just name + symptoms + top-3 root causes
          "full"     — complete diagnosis workflow
        """
        lines = [
            f"## Skill: {self.name}",
            f"Category: {self.category}",
            f"Description: {self.description}",
            "",
        ]

        if self.symptoms:
            lines.append("**Known Symptoms:**")
            for s in self.symptoms:
                lines.append(f"  - {s}")
            lines.append("")

        if detail_level == "summary":
            if self.root_causes:
                lines.append("**Likely Root Causes (by probability):**")
                for rc in sorted(self.root_causes, key=lambda r: r.probability, reverse=True)[:3]:
                    lines.append(f"  - [{rc.probability:.0%}] {rc.description}")
            return "\n".join(lines)

        # Full detail
        if self.evidence_required:
            lines.append("**Required Evidence to Collect:**")
            for ev in self.evidence_required:
                lines.append(f"  - {ev}")
            lines.append("")

        if self.root_cause_summary:
            lines.append("**根因 (Root Cause):**")
            lines.append(f"  {self.root_cause_summary}")
            lines.append("")

        if self.diagnosis_steps:
            lines.append("**诊断步骤 (Diagnosis Steps):**")
            for step in sorted(self.diagnosis_steps, key=lambda s: s.step):
                tool_hint = f" [工具: {step.tool}]" if step.tool else ""
                cond_hint = f" (条件: {step.condition})" if step.condition else ""
                ev_hint = f" [收集: {step.expected_evidence}]" if step.expected_evidence else ""
                lines.append(f"  {step.step}. 操作: {step.action}{tool_hint}{cond_hint}{ev_hint}")
                if step.phenomenon:
                    lines.append(f"     现象: {step.phenomenon}")
                if step.phenomenon_analysis:
                    lines.append(f"     现象分析: {step.phenomenon_analysis}")
            lines.append("")

        if self.root_causes:
            lines.append("**根因与恢复手段 (Root Causes & Recovery):**")
            for rc in sorted(self.root_causes, key=lambda r: r.probability, reverse=True):
                lines.append(f"  ### [{rc.probability:.0%}] {rc.description}")
                if rc.indicators:
                    lines.append(f"    判断依据: {', '.join(rc.indicators)}")
                if rc.recovery_description:
                    lines.append(f"    恢复手段: {rc.recovery_description}")
                for cmd in rc.recovery_commands[:3]:
                    lines.append(f"    命令: `{cmd}`")
                for rec in rc.recommendations[:2]:
                    lines.append(f"    → {rec}")
                for fix in rc.sql_fixes[:2]:
                    lines.append(f"    SQL: `{fix}`")
                for fix in rc.config_fixes[:2]:
                    lines.append(f"    Config: {fix}")
            lines.append("")

        if self.case_examples:
            lines.append("**Historical Case Pattern:**")
            for ex in self.case_examples[:1]:  # just first example
                if "summary" in ex:
                    lines.append(f"  {ex['summary']}")
            lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "type": "case",
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "symptoms": self.symptoms,
            "keywords": self.keywords,
            "evidence_required": self.evidence_required,
            "root_cause_summary": self.root_cause_summary,
            "diagnosis_steps": [
                {
                    "step": s.step,
                    "action": s.action,
                    "tool": s.tool,
                    "condition": s.condition,
                    "phenomenon": s.phenomenon,
                    "phenomenon_analysis": s.phenomenon_analysis,
                }
                for s in sorted(self.diagnosis_steps, key=lambda s: s.step)
            ],
            "root_causes": [
                {
                    "id": rc.id,
                    "description": rc.description,
                    "probability": rc.probability,
                    "indicators": rc.indicators,
                    "recommendations": rc.recommendations,
                    "sql_fixes": rc.sql_fixes,
                    "config_fixes": rc.config_fixes,
                    "recovery_description": rc.recovery_description,
                    "recovery_commands": rc.recovery_commands,
                }
                for rc in sorted(self.root_causes, key=lambda r: r.probability, reverse=True)
            ],
            "case_examples": self.case_examples,
            "references": self.references,
        }


@dataclass
class KnowledgeSkill:
    """
    A lightweight knowledge reference for GaussDB-specific facts.

    Unlike Case Skills (the ``Skill`` class), knowledge skills have no fixed
    diagnostic structure — they contain free-form Markdown content.

    Purpose: ground the agent in GaussDB-specific reality to prevent
    hallucinations caused by training on generic PostgreSQL knowledge.

    Examples of content:
    - GaussDB field name differences vs PostgreSQL (wait_status vs wait_event_type)
    - Correct system view usage (dbe_perf.statement_history for slow SQL)
    - Configuration parameter behaviors (explain_perf_mode values)
    - GaussDB-specific DDL / function / operator quirks
    """

    id: str
    name: str
    category: str          # e.g. "gaussdb_specifics" | "system_views" | "config" | "sql_syntax"
    description: str       # One-line summary shown in skill listings
    keywords: list[str] = field(default_factory=list)   # Matching signals
    tags: list[str] = field(default_factory=list)       # Topic tags for broader matching
    content: str = ""      # Full free-form Markdown body
    version: str = "1.0"
    author: str = ""

    # Matching score cache (set by KnowledgeSkillMatcher)
    _match_score: float = field(default=0.0, repr=False)

    def to_prompt_context(self) -> str:
        """Render this knowledge skill as a concise reference block for prompt injection."""
        lines = [
            f"### 知识参考: {self.name}",
            f"*{self.description}*",
            "",
        ]
        lines.append(self.content.strip())
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "type": "knowledge",
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "keywords": self.keywords,
            "tags": self.tags,
            "content": self.content,
        }

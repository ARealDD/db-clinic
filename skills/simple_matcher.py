"""
Lightweight skill matcher that works without the evidence module.

Scores skills against a user message using keywords, symptoms, triggers,
and category hints. Returns a combined ranked list of case + knowledge skills.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from skills.base import Skill, KnowledgeSkill
from skills.registry import SkillRegistry, KnowledgeSkillRegistry


class MatchedSkill(NamedTuple):
    skill_id: str
    skill_name: str
    category: str
    score: float
    skill_type: str  # "case" or "knowledge"


_CATEGORY_HINTS: dict[str, list[str]] = {
    "slow_sql": [
        "slow", "慢查询", "slow query", "timeout", "duration", "执行计划",
        "explain", "seq scan", "full table scan", "performance", "速度",
    ],
    "lock": [
        "lock", "deadlock", "死锁", "锁", "blocking", "blocked", "waiting",
        "idle in transaction", "lock wait",
    ],
    "index": [
        "index", "索引", "create index", "missing index", "seq scan", "no index",
    ],
    "wait_events": [
        "wait event", "lwlock", "io wait", "等待", "clientread", "wait_event",
    ],
    "vacuum": [
        "vacuum", "autovacuum", "bloat", "dead tuple", "dead rows", "膨胀",
    ],
}


def _normalize(text: str) -> str:
    return text.lower().replace("_", " ").replace("-", " ")


class SimpleSkillMatcher:
    def __init__(
        self,
        case_registry: SkillRegistry,
        knowledge_registry: KnowledgeSkillRegistry,
    ):
        self._case_registry = case_registry
        self._knowledge_registry = knowledge_registry

    def match(self, user_message: str, top_k: int = 3) -> list[MatchedSkill]:
        context = _normalize(user_message)
        results: list[MatchedSkill] = []

        for skill in self._case_registry.all():
            score = self._score_case_skill(skill, context)
            if score > 0:
                results.append(MatchedSkill(
                    skill_id=skill.id,
                    skill_name=skill.name,
                    category=skill.category,
                    score=score,
                    skill_type="case",
                ))

        for skill in self._knowledge_registry.all():
            score = self._score_knowledge_skill(skill, context)
            if score > 0:
                results.append(MatchedSkill(
                    skill_id=skill.id,
                    skill_name=skill.name,
                    category=skill.category,
                    score=score,
                    skill_type="knowledge",
                ))

        results.sort(key=lambda s: s.score, reverse=True)
        return results[:top_k]

    def _score_case_skill(self, skill: Skill, context: str) -> float:
        score = 0.0

        for kw in skill.keywords:
            if _normalize(kw) in context:
                score += 2.0

        for symptom in skill.symptoms:
            words = re.findall(r"\w{3,}", _normalize(symptom))
            if sum(1 for w in words if w in context) >= 2:
                score += 1.5

        for trigger in skill.triggers:
            try:
                if re.search(trigger, context, re.IGNORECASE):
                    score += 3.0
            except re.error:
                pass

        cat_hints = _CATEGORY_HINTS.get(skill.category, [])
        for hint in cat_hints:
            if hint in context:
                score += 1.0
                break

        return score

    def _score_knowledge_skill(self, skill: KnowledgeSkill, context: str) -> float:
        score = 0.0
        for kw in skill.keywords:
            if _normalize(kw) in context:
                score += 2.0
        for tag in skill.tags:
            if _normalize(tag) in context:
                score += 0.5
        return score

    def build_context(self, matched: list[MatchedSkill]) -> str:
        if not matched:
            return ""

        lines = ["[Matched Diagnostic Skills]", ""]
        for m in matched:
            if m.skill_type == "case":
                skill = self._case_registry.get(m.skill_id)
                if skill:
                    lines.append(skill.to_prompt_context(detail_level="full"))
                    lines.append("")
            else:
                skill = self._knowledge_registry.get(m.skill_id)
                if skill:
                    lines.append(skill.to_prompt_context())
                    lines.append("")

        lines.append("[End of Matched Skills]")
        return "\n".join(lines)

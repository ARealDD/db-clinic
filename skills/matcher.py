"""
Skill Matcher — selects the most relevant skills for a given query / session context.

Scoring strategy (keyword + heuristic, no embedding required for Phase 1):
  1. Keyword overlap: count how many skill keywords appear in user message + evidence summary
  2. Regex trigger match: apply skill.triggers patterns
  3. Category boost: if user explicitly mentions "slow query", boost slow_sql skills
  4. Evidence alignment: if collected evidence types match skill.evidence_required → boost

The matcher returns a ranked list of (Skill, score) pairs.
The agent selects the top-K to inject as context.

Phase 2 enhancement: replace keyword scoring with embedding-based similarity
using the Anthropic embeddings API or a local sentence-transformer model.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from skills.base import Skill, KnowledgeSkill
from skills.registry import SkillRegistry, KnowledgeSkillRegistry
from evidence.store import EvidenceStore, EvidenceType


class ScoredSkill(NamedTuple):
    skill: Skill
    score: float
    matched_keywords: list[str]
    matched_triggers: list[str]


# Category detection shortcuts
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


def _text_fingerprint(text: str) -> str:
    """Normalize text for matching."""
    return text.lower().replace("_", " ").replace("-", " ")


class SkillMatcher:
    """
    Stateless matcher: given a user message + evidence store, ranks skills.
    """

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def match(
        self,
        user_message: str,
        evidence_store: EvidenceStore,
        top_k: int = 3,
        min_score: float = 0.0,
    ) -> list[ScoredSkill]:
        """
        Score all skills against the current context and return top_k.
        """
        context_text = _text_fingerprint(
            user_message + " " + evidence_store.build_context_string(max_chars=2000)
        )
        evidence_types = {e.type for e in evidence_store.get_all()}

        scored: list[ScoredSkill] = []
        for skill in self.registry.all():
            ss = self._score_skill(skill, context_text, evidence_types)
            if ss.score >= min_score:
                scored.append(ss)

        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]

    def _score_skill(
        self,
        skill: Skill,
        context_text: str,
        evidence_types: set,
    ) -> ScoredSkill:
        score = 0.0
        matched_keywords: list[str] = []
        matched_triggers: list[str] = []

        # 1. Keyword overlap
        for kw in skill.keywords:
            kw_norm = _text_fingerprint(kw)
            if kw_norm in context_text:
                score += 2.0
                matched_keywords.append(kw)

        # 2. Symptom overlap (lower weight)
        for symptom in skill.symptoms:
            symptom_norm = _text_fingerprint(symptom)
            # Check for word overlap
            words = re.findall(r"\w{3,}", symptom_norm)
            matches = sum(1 for w in words if w in context_text)
            if matches >= 2:
                score += 1.5
                matched_keywords.append(f"symptom:{symptom[:30]}")

        # 3. Trigger regex match
        for trigger in skill.triggers:
            try:
                if re.search(trigger, context_text, re.IGNORECASE):
                    score += 3.0
                    matched_triggers.append(trigger)
            except re.error:
                pass

        # 4. Evidence alignment boost
        required_types = set()
        for ev_str in skill.evidence_required:
            try:
                required_types.add(EvidenceType(ev_str))
            except ValueError:
                pass

        if required_types:
            overlap = len(required_types & evidence_types)
            score += overlap * 1.5

        # 5. Category boost from direct mentions
        cat_hints = _CATEGORY_HINTS.get(skill.category, [])
        for hint in cat_hints:
            if hint in context_text:
                score += 1.0
                break  # Only one boost per category

        return ScoredSkill(
            skill=skill,
            score=score,
            matched_keywords=matched_keywords,
            matched_triggers=matched_triggers,
        )

    def explain(
        self,
        user_message: str,
        evidence_store: EvidenceStore,
        top_k: int = 5,
    ) -> str:
        """Human-readable explanation of skill matching results."""
        results = self.match(user_message, evidence_store, top_k=top_k, min_score=0.0)
        lines = ["**Skill Matching Results:**"]
        for ss in results:
            lines.append(
                f"  [{ss.score:.1f}] {ss.skill.name} "
                f"(keywords: {ss.matched_keywords[:3]}, "
                f"triggers: {ss.matched_triggers[:2]})"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Knowledge Skill Matcher
# ---------------------------------------------------------------------------

class ScoredKnowledgeSkill(NamedTuple):
    skill: KnowledgeSkill
    score: float
    matched_keywords: list[str]


class KnowledgeSkillMatcher:
    """
    Stateless matcher for knowledge skills.

    Uses keyword + tag matching only (no trigger regex, no evidence alignment).
    This is intentionally lighter than SkillMatcher — knowledge skills are
    factual references that should surface based on topic keywords alone,
    without requiring accumulated evidence or regex trigger patterns.
    """

    def __init__(self, registry: KnowledgeSkillRegistry):
        self.registry = registry

    def match(
        self,
        user_message: str,
        evidence_store: EvidenceStore,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[ScoredKnowledgeSkill]:
        """
        Score all knowledge skills against the current context and return top_k.
        A skill with score 0.0 is still included when min_score=0.0 to ensure
        baseline GaussDB facts are always available.
        """
        context_text = _text_fingerprint(
            user_message + " " + evidence_store.build_context_string(max_chars=2000)
        )

        scored: list[ScoredKnowledgeSkill] = []
        for skill in self.registry.all():
            score = 0.0
            matched: list[str] = []

            # Keyword matching — primary signal
            for kw in skill.keywords:
                if _text_fingerprint(kw) in context_text:
                    score += 2.0
                    matched.append(kw)

            # Tag matching — secondary broadening signal
            for tag in skill.tags:
                if _text_fingerprint(tag) in context_text:
                    score += 0.5

            if score >= min_score:
                scored.append(ScoredKnowledgeSkill(skill, score, matched))

        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]

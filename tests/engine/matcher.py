"""
Semantic Matcher — multi-field keyword-weighted scoring for benchmark evaluation.

Design principles:
  - Phase 1: keyword overlap + character bigram matching (no external dependencies)
  - Phase 2: can be extended with embedding similarity or LLM judge
  - All three matching contexts (artifact / root_cause / action) use the same
    underlying scoring engine with different field weights
  - Returns best match with score, per-field breakdown, and human-readable explanation

Tokenization strategy:
  - Split on whitespace and CJK punctuation
  - Add character bigrams for each token (handles Chinese compound words)
  - Preserve full tokens (English words, identifiers like "user_id")
  - All tokens lowercased for comparison

Scoring formula per field:
    overlap = |query_tokens ∩ field_tokens|
    field_score = overlap / (1.0 + log(1 + len(field_tokens))) * weight
    (log normalization prevents very long fields from dominating)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from tests.engine.schema import Artifact, OracleRootCause, OracleAction


# ---------------------------------------------------------------------------
# Field weight configs for each matching context
# ---------------------------------------------------------------------------

ARTIFACT_FIELD_WEIGHTS: dict[str, float] = {
    "retrieval_hints": 3.0,   # Designed specifically for natural language retrieval
    "key":             2.0,   # Artifact key name (sql_text, explain_plan, ...)
    "description":     2.0,   # Short description of what this artifact represents
    "content":         0.3,   # Content itself — low weight to avoid false positives
}

ROOT_CAUSE_FIELD_WEIGHTS: dict[str, float] = {
    "matching_hints":  3.0,   # Explicitly designed to match agent paraphrases
    "summary":         2.5,   # One-sentence standard summary
    "description":     1.5,   # Detailed explanation
    "evidence_hint":   1.0,   # Evidence clues (indirect signal)
}

ACTION_FIELD_WEIGHTS: dict[str, float] = {
    "matching_hints":     3.0,
    "summary":            2.5,
    "command_templates":  1.5,  # SQL/shell commands — keyword overlap still useful
    "description":        1.0,
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """Result of a semantic matching attempt."""
    item_id: str                         # root_cause_id / action_id / artifact key
    item: dict                           # original item as dict
    score: float                         # overall weighted score (higher = better)
    field_scores: dict[str, float]       # per-field score breakdown
    matched_keywords: list[str]          # keywords that contributed to score
    explanation: str                     # human-readable matching rationale

    def is_hit(self, threshold: float) -> bool:
        return self.score >= threshold

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "score": round(self.score, 4),
            "field_scores": {k: round(v, 4) for k, v in self.field_scores.items()},
            "matched_keywords": self.matched_keywords,
            "explanation": self.explanation,
        }


# ---------------------------------------------------------------------------
# Core matcher
# ---------------------------------------------------------------------------

class SemanticMatcher:
    """
    Multi-field keyword-weighted semantic matcher.

    Usage:
        matcher = SemanticMatcher(threshold=0.05)

        # Match agent's information request to artifact
        result = matcher.match_artifact(query_text, artifacts_dict)

        # Match agent's final answer text to oracle root causes
        result = matcher.match_root_cause(answer_text, root_causes_list)

        # Match agent's final answer text to all acceptable actions
        results = matcher.match_all_actions(answer_text, actions_list)
    """

    def __init__(self, threshold: float = 0.05):
        self.threshold = threshold

    # ------------------------------------------------------------------
    # Public: context-specific matching methods
    # ------------------------------------------------------------------

    def match_artifact(
        self,
        query: str,
        artifacts: dict[str, Artifact],
        threshold: float | None = None,
    ) -> MatchResult | None:
        """
        Find the best-matching artifact for an agent information request.

        Args:
            query:     The agent's request text (tool instructions + command text).
            artifacts: The information_inventory dict from the benchmark case.
            threshold: Override instance threshold for this call.

        Returns:
            MatchResult for the best candidate, or None if no candidate meets threshold.
        """
        th = threshold if threshold is not None else self.threshold
        candidates = []
        for key, artifact in artifacts.items():
            # Build field -> text mapping for scoring
            fields = {
                "key":             key,
                "description":     artifact.description,
                "retrieval_hints": " ".join(artifact.retrieval_hints),
                "content":         artifact.content[:500],  # only first 500 chars
            }
            result = self._score(
                query=query,
                item_id=key,
                item=artifact.to_dict(),
                fields=fields,
                weights=ARTIFACT_FIELD_WEIGHTS,
            )
            candidates.append(result)

        return self._best(candidates, th)

    def match_root_cause(
        self,
        answer_text: str,
        candidates: list[OracleRootCause],
        threshold: float | None = None,
    ) -> MatchResult | None:
        """
        Find the best-matching canonical root cause for the agent's final answer.
        """
        th = threshold if threshold is not None else self.threshold
        results = []
        for rc in candidates:
            fields = {
                "matching_hints": " ".join(rc.matching_hints),
                "summary":        rc.summary,
                "description":    rc.description,
                "evidence_hint":  " ".join(rc.evidence_hint),
            }
            result = self._score(
                query=answer_text,
                item_id=rc.root_cause_id,
                item=rc.to_dict(),
                fields=fields,
                weights=ROOT_CAUSE_FIELD_WEIGHTS,
            )
            results.append(result)

        return self._best(results, th)

    def match_all_actions(
        self,
        answer_text: str,
        candidates: list[OracleAction],
        threshold: float | None = None,
    ) -> list[MatchResult]:
        """
        Score all acceptable actions against the answer text.
        Returns all results above threshold, sorted by score descending.
        """
        th = threshold if threshold is not None else self.threshold
        results = []
        for action in candidates:
            fields = {
                "matching_hints":    " ".join(action.matching_hints),
                "summary":           action.summary,
                "command_templates": " ".join(action.command_templates),
                "description":       action.description,
            }
            result = self._score(
                query=answer_text,
                item_id=action.action_id,
                item=action.to_dict(),
                fields=fields,
                weights=ACTION_FIELD_WEIGHTS,
            )
            results.append(result)

        hits = [r for r in results if r.score >= th]
        hits.sort(key=lambda r: r.score, reverse=True)
        return hits

    def match_action(
        self,
        answer_text: str,
        candidates: list[OracleAction],
        threshold: float | None = None,
    ) -> MatchResult | None:
        """Return the single best-matching action, or None."""
        hits = self.match_all_actions(answer_text, candidates, threshold)
        return hits[0] if hits else None

    # ------------------------------------------------------------------
    # Internal: scoring engine
    # ------------------------------------------------------------------

    def _score(
        self,
        query: str,
        item_id: str,
        item: dict,
        fields: dict[str, str],
        weights: dict[str, float],
    ) -> MatchResult:
        """
        Compute weighted multi-field keyword overlap score.

        For each field:
          1. Tokenize field text
          2. Compute overlap with query tokens
          3. Apply log-normalized weight
        """
        query_tokens = self._tokenize(query)
        total_score = 0.0
        field_scores: dict[str, float] = {}
        all_matched: set[str] = set()

        for fname, ftext in fields.items():
            weight = weights.get(fname, 1.0)
            if not ftext:
                field_scores[fname] = 0.0
                continue

            field_tokens = self._tokenize(ftext)
            if not field_tokens:
                field_scores[fname] = 0.0
                continue

            overlap_tokens = query_tokens & field_tokens
            overlap = len(overlap_tokens)
            if overlap == 0:
                field_scores[fname] = 0.0
                continue

            # Log normalization: prevents very long fields from dominating
            norm = 1.0 + math.log(1 + len(field_tokens))
            fscore = (overlap / norm) * weight
            field_scores[fname] = fscore
            total_score += fscore
            all_matched.update(overlap_tokens)

        matched_kw = sorted(all_matched)
        explanation = self._build_explanation(item_id, field_scores, matched_kw, weights)

        return MatchResult(
            item_id=item_id,
            item=item,
            score=total_score,
            field_scores=field_scores,
            matched_keywords=matched_kw,
            explanation=explanation,
        )

    def _best(self, results: list[MatchResult], threshold: float) -> MatchResult | None:
        """Return the highest-scoring result above threshold, or None."""
        if not results:
            return None
        best = max(results, key=lambda r: r.score)
        return best if best.score >= threshold else None

    # ------------------------------------------------------------------
    # Internal: tokenization
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> set[str]:
        """
        Tokenize text into a set of lowercase tokens.

        Strategy:
          1. Split on whitespace and CJK/ASCII punctuation.
          2. Sub-split each raw token at CJK ↔ non-CJK boundaries, so that
             oracle hints like "开启enable_transaction_read_only参数" decompose
             into ["开启", "enable_transaction_read_only", "参数"] instead of
             one giant 38-char token that never matches anything in the agent
             text. Without this, mixed-script hints score 0 even when the
             ASCII identifier appears verbatim in the answer.
          3. Drop tokens shorter than 2 chars (single-char Chinese kept).
          4. For mostly-CJK tokens of length >= 2, also add character bigrams
             (captures Chinese compound-word partial matches).
          5. Lowercase everything.
        """
        if not text:
            return set()

        # Normalize: lowercase
        text = text.lower()

        # Split on whitespace + punctuation (CJK and ASCII)
        # Keeps Chinese characters together as tokens, splits on separators
        raw_tokens = re.split(
            r'[\s\u3000\uff01-\uff60\u3001-\u3009\u300a-\u301e'
            r'.,;:!?()[\]{}<>"\'`~@#$%^&*+=|/\\，。；：！？（）【】《》、]+',
            text,
        )

        tokens: set[str] = set()
        for raw in raw_tokens:
            for tok in self._split_cjk_ascii_boundary(raw):
                tok = tok.strip()
                if not tok or len(tok) < 2:
                    # Skip single-char tokens — too noisy
                    # Exception: single-char Chinese keywords handled via bigrams
                    if len(tok) == 1 and self._is_cjk(tok):
                        tokens.add(tok)
                    continue

                tokens.add(tok)

                # Add character bigrams for CJK tokens (len >= 2)
                if self._is_mostly_cjk(tok) and len(tok) >= 2:
                    for i in range(len(tok) - 1):
                        tokens.add(tok[i:i+2])

        return tokens

    @staticmethod
    def _split_cjk_ascii_boundary(text: str) -> list[str]:
        """Split a string at every transition between CJK and non-CJK chars.

        Examples:
          "foo"                     -> ["foo"]
          "中文"                    -> ["中文"]
          "user_id"                 -> ["user_id"]
          "2024-01-01"              -> ["2024-01-01"]
          "开启enable_xx参数"       -> ["开启", "enable_xx", "参数"]
          "a中b"                    -> ["a", "中", "b"]

        Pure-CJK and pure-ASCII inputs are returned unchanged (single
        element), so the existing CJK-bigram logic and ASCII-identifier
        scoring continue to work exactly as before.
        """
        if not text:
            return []
        pieces: list[str] = []
        current_chars: list[str] = []
        current_is_cjk: bool | None = None
        for ch in text:
            is_cjk = '一' <= ch <= '鿿'
            if current_is_cjk is None or is_cjk == current_is_cjk:
                current_chars.append(ch)
                current_is_cjk = is_cjk
            else:
                pieces.append(''.join(current_chars))
                current_chars = [ch]
                current_is_cjk = is_cjk
        if current_chars:
            pieces.append(''.join(current_chars))
        return pieces

    @staticmethod
    def _is_cjk(char: str) -> bool:
        return '\u4e00' <= char <= '\u9fff'

    @staticmethod
    def _is_mostly_cjk(tok: str) -> bool:
        cjk_count = sum(1 for c in tok if '\u4e00' <= c <= '\u9fff')
        return cjk_count >= len(tok) * 0.5

    # ------------------------------------------------------------------
    # Internal: explanation builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_explanation(
        item_id: str,
        field_scores: dict[str, float],
        matched_keywords: list[str],
        weights: dict[str, float],
    ) -> str:
        top_fields = sorted(
            [(f, s) for f, s in field_scores.items() if s > 0],
            key=lambda x: x[1],
            reverse=True,
        )[:3]

        if not top_fields:
            return f"[{item_id}] no keyword overlap found"

        field_desc = ", ".join(f"{f}({s:.3f})" for f, s in top_fields)
        kw_sample = matched_keywords[:8]
        kw_desc = ", ".join(f'"{k}"' for k in kw_sample)
        if len(matched_keywords) > 8:
            kw_desc += f" ... (+{len(matched_keywords) - 8} more)"

        return f"[{item_id}] top fields: {field_desc} | matched: {kw_desc}"

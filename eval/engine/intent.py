"""
Intent recognition and final answer extraction for the benchmark runner.

IntentRecognizer classifies each agent response turn into:
  - REQUEST_INFORMATION : agent is asking for more data (via proxy tool or text)
  - FINAL_ANSWER        : agent has given a root cause + recommendation conclusion
  - OTHER               : intermediate reasoning, no clear action yet

FinalAnswerExtractor splits the final answer text into:
  - root_cause_text : the segment describing the diagnosed root cause
  - action_text     : the segment describing the recommended action/fix

Detection strategy (in priority order):
  1. Proxy tool command present → REQUEST_INFORMATION (tool-based evidence request)
  2. Text contains explicit information request phrases → usually REQUEST_INFORMATION
     BUT if no proxy command and the turn already contains strong final-answer
     structure (root cause + action, long end_turn response), treat as FINAL_ANSWER.
  3. LLM judge (if available): does text contain root cause + action? → FINAL_ANSWER
     Falls back to keyword patterns (_has_final_answer_markers) when LLM result is None.
  4. stop_reason == "end_turn" AND text is long enough → FINAL_ANSWER (conservative fallback)
  5. All else → OTHER
"""
from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    REQUEST_INFORMATION = "request_information"
    FINAL_ANSWER = "final_answer"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Keyword patterns for final answer detection
# ---------------------------------------------------------------------------

# A response is "final answer" if it contains at least one keyword from EACH group.
# Group A: root cause declaration vocabulary
_FINAL_ROOT_CAUSE_PATTERNS = [
    r"根因",
    r"根本原因",
    r"问题根源",
    r"诊断(结论|结果|完成|如下)",
    r"原因(是|为|：|:)",
    r"问题(在于|出在|是由)",
    r"定位(到|为)",
    r"分析(结论|结果|如下)",
    r"root\s*cause",
    r"identified.*cause",
    r"cause.*identified",
]

# Group B: action/recommendation vocabulary.
#
# Note: bare "建议" is too weak — it appears in tails like "我将给出修复建议"
# or "请提供XXX，以便建议". Action markers must be (a) compound keywords like
# "修复建议", or (b) "建议" at start of line / after a heading-like prefix.
_FINAL_ACTION_PATTERNS = [
    r"调优(建议|方案|操作|步骤)",
    r"解决(方案|办法|措施|建议)",
    r"修复(方案|建议|步骤)",
    r"处理(方案|建议|步骤)",
    r"优化(建议|方案|操作)",
    r"恢复(建议|方案|步骤)",
    r"推荐(操作|方案|步骤)",
    # "建议" alone, only when it leads a section (after newline/heading marker)
    r"(?:^|\n)[\s#*\->•·●◆◎◯○●■□▶►📌🔧🛠💊⚙️✀-➿]*建议[：: \n]",
    r"create\s+index",
    r"alter\s+(table|index)",
    r"analyze\s+\w+",
    r"vacuum",
    r"rewrite",
    r"recommend",
    r"suggestion",
]

# Section header patterns used to split final answer into root_cause / action segments.
#
# After "#" and before the keyword we allow:
#   - whitespace
#   - emoji / pictographs / symbols (e.g. "##  🎯 根因定位", "### 🛠 恢复建议")
#   - bold/italic markers (* _)
#   - common decorations ("---", "▶", "📋")
# The character class \S excludes whitespace, so we use a permissive non-keyword
# char class explicitly.
_HEADER_DECORATION = (
    r"[\s\*_\-=>·•●○◯◆◎▶►■□"
    r"\U0001F300-\U0001F6FF"   # Misc symbols & pictographs
    r"\U0001F900-\U0001F9FF"   # Supplemental symbols & pictographs
    r"\U00002600-\U000026FF"   # Misc symbols
    r"\U00002700-\U000027BF"   # Dingbats
    r"\U0001F100-\U0001F1FF"   # Enclosed alphanumerics
    r"☀-➿]*"
)

_ROOT_CAUSE_HEADERS = [
    rf"#+{_HEADER_DECORATION}(根因|根本原因|问题根源|诊断结论|原因分析|问题分析|根因(分析|定位|结论))",
    r"(根因|根本原因|问题根源|诊断结论)[：:]\s*",
    r"\*\*(根因|根本原因|问题根源)[：:]?\*\*",
]

_ACTION_HEADERS = [
    rf"#+{_HEADER_DECORATION}(调优建议|优化建议|解决方案|处理建议|修复建议|恢复建议|推荐操作|止损措施|立即(止损|行动|措施)|修复方案|处置方案|建议)",
    r"(调优建议|优化建议|解决方案|处理建议|修复建议|恢复建议|止损措施|修复方案)[：:]\s*",
    r"\*\*(调优建议|优化建议|解决方案|处理建议|修复建议|恢复建议)[：:]?\*\*",
]

# Minimum text length (chars) for conservative end_turn→FINAL_ANSWER fallback
_MIN_FINAL_ANSWER_LENGTH = 200

# Minimum length of an extracted action segment to be considered valid.
# Below this, the agent likely ended on a fragment like "我将给出修复建议".
_MIN_ACTION_LEN = 60

# Patterns that indicate the agent is still requesting information from the user,
# even when no proxy_command chunk was emitted (agent wrote a plain-text request).
_REQUEST_INFORMATION_TEXT_PATTERNS = [
    # Common "send me back the result" phrasings, with optional 您/你
    r"请(您|你)?(将|把|发|发给).{0,40}(结果|输出|信息|数据|SQL|EXPLAIN|贴|粘贴|回复|返回|发给我|给我|提供)",
    r"请执行以下",
    r"请运行以下",
    r"执行(上述|以下|这个|该).{0,10}(SQL|查询|命令|语句)",
    r"(执行|运行)后.{0,10}(将|把).{0,20}结果",
    r"请(您|你)?(提供|给我|发送).{0,20}(结果|输出|信息|数据)",
    r"需要您(执行|运行|提供)",
    # Trailing "贴回来 / 贴过来 / 贴给我" patterns regardless of leading verb
    r"(贴|粘贴)(回来|回复|给我|过来|过去)",
    r"反馈结果.{0,10}(给我|后我)",
    # Additional patterns covering plain-text asks without proxy_command
    r"请告诉我",
    r"需要确认的信息",
    r"需要(您|进一步)(确认|提供|告诉)",
    r"请(提供|发给我|给我).{0,30}(SQL|查询|语句|表名|信息|执行计划)",
    r"(报表|查询|SQL).{0,20}(是什么|来自哪里|涉及哪些)",
    r"麻烦(您|你)?(提供|发送|告诉我)",
    r"(能否|是否可以|可以|请您?)(提供|发送|给我).{0,20}(SQL|查询|语句|表名|信息)",
    r"让我(先)?(查看|检查|确认)",
    r"我需要(先)?(查看|检查|确认)",
    r"现在需要(查看|检查|确认)",
    # "请问需要我帮您..." / "请问您需要..." style follow-up offers
    r"请问.{0,20}(需要|是否|要)",
    r"是否(需要|要)我(帮|帮您|继续|进一步)",
    r"您(还)?需要我(帮|继续|进一步|进一步分析)",
]


# ---------------------------------------------------------------------------
# IntentRecognizer
# ---------------------------------------------------------------------------

class IntentRecognizer:
    """
    Classify an agent response turn into REQUEST_INFORMATION / FINAL_ANSWER / OTHER.
    """

    def recognize(
        self,
        text: str,
        has_proxy_command: bool,
        stop_reason: str = "end_turn",
        llm_final_answer: bool | None = None,
    ) -> Intent:
        """
        Args:
            text:              The full text output from this agent turn.
            has_proxy_command: True if a proxy_command chunk was seen in this turn.
            stop_reason:       The 'stop_reason' from the 'done' chunk.
            llm_final_answer:  Optional LLM judgment: True = agent gave complete
                               diagnosis (root cause + action), False = not yet,
                               None = LLM unavailable (fall back to keyword patterns).

        Returns:
            Intent enum value.
        """
        # Priority 1: proxy tool call → information request
        if has_proxy_command:
            return Intent.REQUEST_INFORMATION

        # Priority 2: text-based information request detection.
        # Some models append "请执行后告诉我结果" after a complete diagnosis.
        # In that case, avoid false REQUEST_INFORMATION by checking whether this
        # turn already has strong final-answer structure.
        if self._has_any_pattern(text, _REQUEST_INFORMATION_TEXT_PATTERNS):
            has_structured_final = (
                stop_reason == "end_turn"
                and len(text.strip()) >= _MIN_FINAL_ANSWER_LENGTH
                and self._has_final_answer_markers(text)
            )
            if not has_structured_final:
                return Intent.REQUEST_INFORMATION

        # Priority 3: FINAL_ANSWER detection.
        # Use LLM judgment when available; fall back to keyword patterns otherwise.
        if llm_final_answer is not None:
            if llm_final_answer:
                return Intent.FINAL_ANSWER
        elif self._has_final_answer_markers(text):
            return Intent.FINAL_ANSWER

        # Priority 4: conservative fallback — end_turn with substantial text
        if stop_reason == "end_turn" and len(text.strip()) >= _MIN_FINAL_ANSWER_LENGTH:
            # Only treat as final answer if text has some action-like content
            if self._has_any_pattern(text, _FINAL_ACTION_PATTERNS):
                return Intent.FINAL_ANSWER

        return Intent.OTHER

    def is_request_information(self, text: str) -> bool:
        """
        Quick check: does the text contain explicit information request phrases?

        Used by BenchmarkRunner to skip the LLM final-answer call when we already
        know this turn is a REQUEST_INFORMATION (saving an unnecessary LLM call).
        """
        has_request = self._has_any_pattern(text, _REQUEST_INFORMATION_TEXT_PATTERNS)
        if not has_request:
            return False

        # Quick guard: don't skip LLM final-judge for long structured conclusions
        # that only end with a follow-up sentence like "请执行后告诉我结果".
        has_structured_final = (
            len(text.strip()) >= _MIN_FINAL_ANSWER_LENGTH
            and self._has_final_answer_markers(text)
        )
        return not has_structured_final

    def _has_final_answer_markers(self, text: str) -> bool:
        """True if text contains at least one pattern from BOTH groups."""
        has_root = self._has_any_pattern(text, _FINAL_ROOT_CAUSE_PATTERNS)
        has_action = self._has_any_pattern(text, _FINAL_ACTION_PATTERNS)
        return has_root and has_action

    @staticmethod
    def _has_any_pattern(text: str, patterns: list[str]) -> bool:
        for p in patterns:
            if re.search(p, text, re.IGNORECASE):
                return True
        return False


# ---------------------------------------------------------------------------
# FinalAnswerExtractor
# ---------------------------------------------------------------------------

class FinalAnswerExtractor:
    """
    Extract root cause text and action text from a final answer.

    Tries to find section boundaries using common header patterns.
    Falls back to returning the full text for both if structure is unclear.
    """

    def extract(self, text: str) -> tuple[str, str]:
        """
        Parse the agent's final answer into (root_cause_text, action_text).

        Returns:
            (root_cause_text, action_text)
            Both may equal the full text if structure cannot be determined.
        """
        # Pre-step: strip trailing "still asking for info" tails so they don't
        # bleed into action_text and inflate matcher score.
        text = self._strip_trailing_request(text)

        root_cause_text, action_text = self._split_by_headers(text)

        # If extraction failed, return full text for both
        # (the matcher will search the full response)
        if not root_cause_text:
            root_cause_text = text
        if not action_text:
            action_text = text

        return root_cause_text.strip(), action_text.strip()

    @staticmethod
    def _strip_trailing_request(text: str) -> str:
        """
        Drop trailing paragraphs that are pure "请提供 XXX / 请执行 SQL" requests.

        Many agents end a final answer with a follow-up like
        '请您把 EXPLAIN 输出贴回来，我再深入分析'. That sentence is not a
        recommended action; including it in action_text causes false-positive
        keyword matches against oracle action commands.

        Strategy: look at the last paragraph (split by blank line); if it
        matches any of the request patterns AND contains no action verb
        ('CREATE INDEX' / 'ALTER' / 'VACUUM' / '建议' / '修复'), drop it.
        Repeat once.
        """
        if not text:
            return text

        for _ in range(2):  # at most strip 2 trailing request paragraphs
            paragraphs = re.split(r"\n\s*\n", text.rstrip())
            if not paragraphs:
                break
            tail = paragraphs[-1]
            # If the last paragraph is itself long (no blank-line separation
            # between content and tail request), try to peel off only the
            # final sentence by Chinese/ASCII sentence punctuation.
            tail_only_sentence = None
            if len(tail) > 200:
                sentences = re.split(r"(?<=[。！？!?])\s*", tail)
                sentences = [s for s in sentences if s.strip()]
                if len(sentences) >= 2:
                    tail_only_sentence = sentences[-1]
            tail_for_test = tail_only_sentence if tail_only_sentence else tail
            tail_short = tail_for_test.strip()
            if len(tail_short) > 600:
                # too long to be a pure request; keep
                break
            is_request = any(
                re.search(p, tail_short, re.IGNORECASE)
                for p in _REQUEST_INFORMATION_TEXT_PATTERNS
            )
            if not is_request:
                break
            # Don't drop if tail contains real action content: SQL/shell verbs
            # or a fenced code block. Bare nouns like "修复建议" are NOT enough
            # because tails often say "我将给出修复建议".
            has_real_action = bool(
                re.search(r"```", tail_short)
                or re.search(
                    r"\b(create\s+(index|table)|alter\s+(table|index|system|database)"
                    r"|analyze\b|vacuum(\s+full)?|reindex|drop\s+(table|index)"
                    r"|set\s+\w+\s*=|update\s+\w+\s+set|delete\s+from)\b",
                    tail_short,
                    re.IGNORECASE,
                )
            )
            if has_real_action:
                break
            if tail_only_sentence:
                # Drop just the last sentence from the trailing paragraph,
                # leaving everything before it intact.
                trimmed_tail = tail[: -len(tail_only_sentence)].rstrip()
                paragraphs[-1] = trimmed_tail
                text = "\n\n".join(p for p in paragraphs if p)
            else:
                text = "\n\n".join(paragraphs[:-1])
        return text

    def _split_by_headers(self, text: str) -> tuple[str, str]:
        """
        Find section header positions and extract root cause / action segments.
        Returns ("", "") if headers cannot be found.
        """
        lines = text.split("\n")

        rc_start: int | None = None
        action_start: int | None = None

        for i, line in enumerate(lines):
            if rc_start is None and self._matches_any_header(line, _ROOT_CAUSE_HEADERS):
                rc_start = i
            if action_start is None and self._matches_any_header(line, _ACTION_HEADERS):
                action_start = i

        if rc_start is None and action_start is None:
            # No headers found — try inline patterns
            return self._extract_inline(text)

        root_cause_text = ""
        action_text = ""

        if rc_start is not None:
            end = action_start if (action_start is not None and action_start > rc_start) else len(lines)
            root_cause_text = "\n".join(lines[rc_start:end])

        if action_start is not None:
            end2 = rc_start if (rc_start is not None and rc_start > action_start) else len(lines)
            action_text = "\n".join(lines[action_start:end2])

        # If only one section found, use full text for the missing one
        if not root_cause_text:
            root_cause_text = text
        if not action_text:
            action_text = text

        return root_cause_text, action_text

    def _extract_inline(self, text: str) -> tuple[str, str]:
        """
        Try inline extraction: split on the first action keyword found.
        Returns ("", "") if no split point found.

        Reject splits that leave action_text shorter than _MIN_ACTION_LEN —
        these are typically false positives where "建议" appears in a trailing
        sentence like "我将给出修复建议" with no actual content following.
        """
        # Find last position of a root cause marker
        rc_pos = -1
        for p in _FINAL_ROOT_CAUSE_PATTERNS:
            m = re.search(p, text, re.IGNORECASE)
            if m and m.start() > rc_pos:
                rc_pos = m.start()

        # Find first position of an action marker after root cause
        action_pos = -1
        for p in _FINAL_ACTION_PATTERNS:
            m = re.search(p, text, re.IGNORECASE | re.MULTILINE)
            if m and m.start() > rc_pos:
                if action_pos == -1 or m.start() < action_pos:
                    action_pos = m.start()

        if rc_pos == -1 or action_pos == -1:
            return "", ""

        # Use a window around each marker
        root_cause_text = text[max(0, rc_pos - 50):action_pos]
        action_text = text[action_pos:]

        # Reject obviously truncated action segments (e.g. "修复建议。")
        if len(action_text.strip()) < _MIN_ACTION_LEN:
            return "", ""

        return root_cause_text, action_text

    @staticmethod
    def _matches_any_header(line: str, header_patterns: list[str]) -> bool:
        for p in header_patterns:
            if re.search(p, line, re.IGNORECASE):
                return True
        return False

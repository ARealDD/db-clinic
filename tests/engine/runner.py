"""
BenchmarkRunner — multi-turn interactive evaluation loop.

Core design:
  - Drives the DBAAgent through a complete multi-turn diagnostic session
  - Intercepts proxy tool calls, matches them to artifacts via SemanticMatcher
  - Returns matched artifact content (or fallback) as the "user result"
  - Detects final answer turns and evaluates against oracle
  - Records complete interaction logs for post-hoc analysis

Multi-turn loop flow:
  1. Create fresh SessionState per case
  2. Send user_report as first message
  3. Collect agent response (all chunks until 'done')
  4. Classify intent (REQUEST_INFORMATION / FINAL_ANSWER / OTHER)
  5. If REQUEST_INFORMATION:
       - Build query from proxy_command context (tool name + command + instructions)
       - SemanticMatcher.match_artifact() against information_inventory
       - Return matched content or fallback message
       - Call agent.chat() again (routes to _complete_proxy() automatically)
  6. If FINAL_ANSWER:
       - FinalAnswerExtractor.extract() -> root_cause_text, action_text
       - SemanticMatcher.match_root_cause() + match_all_actions()
       - Build CaseRunResult and stop
  7. If OTHER and stop_reason == "end_turn":
       - Check if text is long enough to be a silent final answer
       - Otherwise wait for next turn (agent will continue on next iteration)
  8. Termination: final_answer | max_turns | consecutive_misses | error

Proxy tool interception:
  When a proxy_command chunk is received, session.has_pending_proxy() becomes True.
  Calling agent.chat(session, user_message) again automatically routes to
  _complete_proxy(), feeding the result back into the ReAct loop.
"""
from __future__ import annotations

import asyncio
import re
import time
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tests.engine.schema import BenchmarkCase, Artifact
from tests.engine.matcher import SemanticMatcher, MatchResult
from tests.engine.intent import Intent, IntentRecognizer, FinalAnswerExtractor

if TYPE_CHECKING:
    from tests.engine.protocol import AgentProtocol as DBAAgent
    from tests.engine.protocol import SimpleSession


def _strip_think_output(text: str) -> str:
    """
    Remove model reasoning blocks from raw output.

    Handles both:
      - well-formed <think>...</think>
      - malformed/truncated output that only has an opening <think>
    """
    if not text:
        return ""

    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>", 1)[0]
    cleaned = cleaned.replace("</think>", "")
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class EvalConfig:
    """Configuration parameters for the benchmark runner."""
    max_turns: int = 20
    """Maximum number of agent turns before forcing termination."""

    max_consecutive_misses: int = 5
    """Stop case if this many consecutive information requests found no matching artifact."""

    artifact_match_threshold: float = 0.05
    """Minimum score to count an artifact match as a 'hit'."""

    root_cause_match_threshold: float = 11.0
    """Minimum score to count root cause match as correct.

    Calibrated from cross-case negative scoring on the 45-case test set
    (P95 of off-target SemanticMatcher scores ≈ 10.84 → rounded up to 11.0).
    Use dba_bench.tools.calibrate_thresholds to re-derive on new data.

    Note: this threshold only applies when the LLM judge is unavailable or
    returns malformed output. When the LLM judge cleanly returns 0/positive,
    its verdict is trusted directly (post-Phase-1 fix).
    """

    action_match_threshold: float = 8.0
    """Minimum score to count an action match as correct.

    Calibrated similarly (P95 of negatives ≈ 7.66 → 8.0).
    """

    min_action_hits: int = 1
    """Minimum number of matched acceptable actions required for action_hit."""

    require_primary_action: bool = False
    """Whether action_hit requires matching the primary action."""

    require_claimed_id_match: bool = False
    """Off-target ID gate.

    When True (the strict mode): if the agent's final text contains explicit
    RC IDs (matched by `[A-Z]\\d-RC-\\d{2,3}`), at least one of them must
    appear in the oracle ID set, otherwise the RC match is rejected.

    Default False, because for an ops user the question is "did the agent
    find the right root cause concept?" — not "did it pick the right ID
    label from a private taxonomy". The doer agent's RC-ID labels and the
    oracle's RC-IDs come from different revisions of the same dictionary
    and don't always agree even when the diagnosis is correct.

    Set True (e.g. via --strict-claimed-ids) when you specifically want to
    benchmark whether the agent uses the canonical taxonomy correctly.
    """

    consistency_bypass_score_ratio: float = 1.5
    """Skip the matching-hint consistency check when the matcher score is
    at least this ratio × the threshold.

    The consistency check requires the answer text to contain ≥2 of the
    candidate's anchor tokens (whole CJK runs ≥3 chars + ASCII identifiers
    ≥3 chars). It exists to catch matcher false positives where shared
    description-level keywords inflate the score for an unrelated
    candidate. But agents commonly paraphrase oracle hints, so the check
    becomes a false-negative source for high-confidence semantic matches.

    Empirically, scores at >=1.5× threshold (i.e. RC>=16.5, action>=12)
    are dominated by matching_hints + summary token overlap (not
    description spillover), so trusting them is safe.

    Set to a very large number (e.g. 999.0) to disable the bypass and
    always require consistency.
    """

    fallback_message: str = (
        "抱歉，目前没有找到与您请求相关的更多信息。请根据已有信息继续分析。"
    )
    """Message returned to agent when no matching artifact is found."""

    llm_call_timeout: float = 2000.0
    """Timeout in seconds for a single agent chat() call."""

    llm_timeout_retry_delay: float = 20.0
    """Seconds to wait before retrying after a timeout or connection error.
    Both are retried indefinitely until a response is received."""

    llm_judge_max_attempts: int = 5
    """Max attempts for matcher / judge LLM calls before falling back. The agent
    chat loop is unaffected (it must keep retrying to make progress)."""


# ---------------------------------------------------------------------------
# Per-turn log entry
# ---------------------------------------------------------------------------

@dataclass
class TurnLog:
    """Complete record of a single agent interaction turn."""
    turn_index: int
    agent_text: str                      # Full text output from agent (all text_delta chunks)
    intent: str                          # Intent enum value
    stop_reason: str                     # From the 'done' chunk

    # Proxy tool information (if intent == REQUEST_INFORMATION)
    has_proxy_command: bool = False
    proxy_tool_name: str | None = None
    proxy_command: str | None = None
    proxy_instructions: str | None = None

    # Artifact matching (if intent == REQUEST_INFORMATION)
    request_query: str = ""             # Query text built from proxy context
    matched_artifact_key: str | None = None
    artifact_match_score: float = 0.0
    artifact_match_explanation: str = ""
    returned_content: str | None = None  # What was returned to the agent
    was_fallback: bool = False           # True if fallback message was returned

    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "turn_index": self.turn_index,
            "intent": self.intent,
            "stop_reason": self.stop_reason,
            "agent_text_length": len(self.agent_text),
            "agent_text_preview": self.agent_text[:300] + ("..." if len(self.agent_text) > 300 else ""),
            "has_proxy_command": self.has_proxy_command,
            "proxy_tool_name": self.proxy_tool_name,
            "proxy_command": self.proxy_command,
            "proxy_instructions": self.proxy_instructions,
            "request_query": self.request_query,
            "matched_artifact_key": self.matched_artifact_key,
            "artifact_match_score": round(self.artifact_match_score, 4),
            "artifact_match_explanation": self.artifact_match_explanation,
            "returned_content_preview": (
                (self.returned_content[:200] + "...") if self.returned_content and len(self.returned_content) > 200
                else self.returned_content
            ),
            "was_fallback": self.was_fallback,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Per-case result
# ---------------------------------------------------------------------------

@dataclass
class CaseRunResult:
    """Complete evaluation result for a single benchmark case."""
    case_id: str

    # Final answer analysis
    total_turns: int = 0
    final_agent_text: str = ""
    root_cause_text: str = ""           # Extracted root cause segment
    action_text: str = ""               # Extracted action segment

    # Oracle matching results
    best_root_cause_match: MatchResult | None = None
    best_action_matches: list[MatchResult] = field(default_factory=list)

    # Evaluation verdicts
    root_cause_hit: bool = False
    action_hit: bool = False
    e2e_success: bool = False

    # Information request tracking
    info_request_count: int = 0         # Number of information request turns
    info_request_hits: int = 0          # Turns where artifact was matched (score >= threshold)
    info_request_hit_rate: float = 0.0  # hits / count

    # Termination
    stop_reason: str = ""   # "final_answer" | "max_turns" | "consecutive_misses" | "error"
    error_message: str = ""

    # Full interaction trace
    turn_logs: list[TurnLog] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "total_turns": self.total_turns,
            "stop_reason": self.stop_reason,
            "root_cause_hit": self.root_cause_hit,
            "action_hit": self.action_hit,
            "e2e_success": self.e2e_success,
            "info_request_count": self.info_request_count,
            "info_request_hits": self.info_request_hits,
            "info_request_hit_rate": round(self.info_request_hit_rate, 4),
            "root_cause_text_preview": self.root_cause_text[:200] if self.root_cause_text else "",
            "action_text_preview": self.action_text[:200] if self.action_text else "",
            "best_root_cause_match": self.best_root_cause_match.to_dict() if self.best_root_cause_match else None,
            "best_action_matches": [m.to_dict() for m in self.best_action_matches],
            "final_agent_text_length": len(self.final_agent_text),
            "error_message": self.error_message,
            "turn_logs": [t.to_dict() for t in self.turn_logs],
        }

    @classmethod
    def from_log_record(cls, record: dict) -> "CaseRunResult":
        """
        Reconstruct a CaseRunResult from a previously-written case JSON log.

        Used by --rerun-errors to merge unchanged (successful) cases with
        freshly re-run error cases when recomputing suite metrics. Note:
        `item` payloads on MatchResult are not stored in the JSON, so they
        are reconstructed as empty dicts; this is sufficient for metrics
        and reporting which only read `item_id` and `score`.
        """
        def _mr(d: dict | None) -> "MatchResult | None":
            if not d:
                return None
            return MatchResult(
                item_id=d.get("item_id", ""),
                item={},
                score=float(d.get("score", 0.0)),
                field_scores=d.get("field_scores", {}) or {},
                matched_keywords=d.get("matched_keywords", []) or [],
                explanation=d.get("explanation", "") or "",
            )

        rc_match = _mr(record.get("best_root_cause_match"))
        action_matches = [
            m for m in (_mr(d) for d in (record.get("best_action_matches") or []))
            if m is not None
        ]

        return cls(
            case_id=record.get("case_id", ""),
            total_turns=int(record.get("total_turns", 0) or 0),
            final_agent_text=record.get("final_agent_text", "") or "",
            root_cause_text=record.get("extracted_root_cause_text", "") or "",
            action_text=record.get("extracted_action_text", "") or "",
            best_root_cause_match=rc_match,
            best_action_matches=action_matches,
            root_cause_hit=bool(record.get("root_cause_hit", False)),
            action_hit=bool(record.get("action_hit", False)),
            e2e_success=bool(record.get("e2e_success", False)),
            info_request_count=int(record.get("info_request_count", 0) or 0),
            info_request_hits=int(record.get("info_request_hits", 0) or 0),
            info_request_hit_rate=float(record.get("info_request_hit_rate", 0.0) or 0.0),
            stop_reason=record.get("stop_reason", "") or "",
            error_message=record.get("error_message") or "",
            turn_logs=[],
        )


# ---------------------------------------------------------------------------
# Benchmark Runner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    """
    Drives the DBA Agent through benchmark cases and collects evaluation results.

    Each case runs in an isolated SessionState. The runner intercepts proxy tool
    calls, matches them to the information_inventory, and feeds the content back
    to the agent, simulating a human DBA executing the requested queries.
    """

    def __init__(
        self,
        agent: "DBAAgent",
        config: EvalConfig | None = None,
        llm_client=None,
        llm_model: str | None = None,
        session_factory=None,
    ):
        self._agent = agent
        self._config = config or EvalConfig()
        self._matcher = SemanticMatcher(threshold=self._config.artifact_match_threshold)
        self._intent_recognizer = IntentRecognizer()
        self._extractor = FinalAnswerExtractor()
        # Optional LLM client reference used to reconnect on connection errors
        self._llm_client = llm_client
        self._llm_model = llm_model
        self._last_final_judge_raw: str = ""
        if session_factory is not None:
            self._session_factory = session_factory
        else:
            from tests.engine.protocol import SimpleSession
            self._session_factory = SimpleSession

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        """Return True for transient network errors that should be retried.

        Covers two families:
          - Timeouts: asyncio.TimeoutError / TimeoutError; messages containing
            "timeout" or "timed out".
          - Mid-stream connection drops: gateways like deepseek/glm sometimes
            close keep-alive sockets while a streaming response is in flight.
            urllib3 surfaces these as
              ConnectionError: ('Connection aborted.',
                                RemoteDisconnected('Remote end closed ...'))
            or as httpx.RemoteProtocolError, httpcore.ReadError, etc.
            They are recoverable on retry; previously the predicate silently
            fell through to a None / SemanticMatcher fallback, defeating the
            LLM judge whenever the network blipped.
        """
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
            return True
        msg = str(exc).lower()
        retryable_phrases = (
            "timeout",
            "timed out",
            "connection aborted",
            "connection reset",
            "remote disconnected",
            "remote end closed",
            "remote protocol",
            "broken pipe",
            "server disconnected",
            "incomplete read",
            "incompleteread",  # http.client.IncompleteRead repr
            "read timed out",
        )
        return any(p in msg for p in retryable_phrases)

    # Back-compat shim: external callers (e.g. tests, downstream wrappers)
    # may still reference the old name. Keep it as a thin alias.
    _is_timeout_error = _is_retryable_error

    # ------------------------------------------------------------------
    # Public: run a single case
    # ------------------------------------------------------------------

    async def run_case(self, case: BenchmarkCase) -> CaseRunResult:
        """
        Run a single benchmark case end-to-end.

        Creates a fresh session, drives the multi-turn loop, and returns
        a complete CaseRunResult with all metrics and turn logs.
        """
        from tests.engine.protocol import SimpleSession

        result = CaseRunResult(case_id=case.case_id)
        session = self._session_factory()
        turn_index = 0
        consecutive_misses = 0

        print(f"\n{'='*60}")
        print(f"  Case: {case.case_id}")
        print(f"  Report: {case.user_report[:80]}...")
        print(f"{'='*60}")

        # Initial message: send the user report
        current_message = case.user_report
        is_first_turn = True

        try:
            while turn_index < self._config.max_turns:
                turn_index += 1
                print(f"\n  [Turn {turn_index}] Sending message ({len(current_message)} chars)")

                # --- Collect agent response ---
                agent_text, proxy_chunk, stop_reason = await self._collect_response(
                    session=session,
                    user_message=current_message,
                )

                is_first_turn = False
                has_proxy = proxy_chunk is not None

                print(f"  [Turn {turn_index}] stop_reason={stop_reason!r}, "
                      f"has_proxy={has_proxy}, text_len={len(agent_text)}")

                # --- Print per-turn display text ---
                # Prefer proxy instructions in terminal output (more actionable than
                # long agent narrative for experiment replay).
                if has_proxy and proxy_chunk:
                    proxy_instructions = str(proxy_chunk.get("instructions", "")).strip()
                    if not proxy_instructions:
                        # Some proxy tools may omit instructions; fallback to command
                        # so terminal still shows actionable proxy context.
                        proxy_instructions = str(proxy_chunk.get("command", "")).strip()
                    print(f"  [Turn {turn_index}] Proxy instructions (matching input):\n"
                          + "\n".join(f"    {line}" for line in proxy_instructions.splitlines()))
                if agent_text:
                    print(f"  [Turn {turn_index}] Agent output:\n"
                          + "\n".join(f"    {line}" for line in agent_text.splitlines()))

                # --- Classify intent ---
                # For FINAL_ANSWER detection, prefer LLM judgment over keyword
                # patterns. Only call LLM when text request patterns and
                # proxy_command (cheaper checks) don't already resolve intent.
                llm_final: bool | None = None
                skip_final_judge_reason: str | None = None
                if self._llm_client is None:
                    skip_final_judge_reason = "no_judge_llm_client"
                elif has_proxy:
                    skip_final_judge_reason = "proxy_command_present"
                elif self._intent_recognizer.is_request_information(agent_text):
                    skip_final_judge_reason = "text_request_pattern"
                else:
                    llm_final = await self._llm_judge_final_answer(agent_text)
                    raw_preview = (self._last_final_judge_raw or "").replace("\n", "\\n")
                    if len(raw_preview) > 120:
                        raw_preview = raw_preview[:120] + "..."
                    verdict = (
                        "yes" if llm_final is True
                        else "no" if llm_final is False
                        else "unknown"
                    )
                    print(
                        f"  [Judge][Turn {turn_index}] final_answer={verdict}, "
                        f"raw={raw_preview!r}"
                    )

                if skip_final_judge_reason is not None:
                    print(
                        f"  [Judge][Turn {turn_index}] final_answer=skipped "
                        f"(reason={skip_final_judge_reason})"
                    )

                intent = self._intent_recognizer.recognize(
                    text=agent_text,
                    has_proxy_command=has_proxy,
                    stop_reason=stop_reason,
                    llm_final_answer=llm_final,
                )
                print(f"  [Turn {turn_index}] intent={intent.value}"
                      + (f" (llm_final={llm_final})" if llm_final is not None else ""))

                # --- Handle REQUEST_INFORMATION ---
                if intent == Intent.REQUEST_INFORMATION:
                    turn_log, next_message = await self._handle_info_request(
                        turn_index=turn_index,
                        agent_text=agent_text,
                        proxy_chunk=proxy_chunk,
                        stop_reason=stop_reason,
                        case=case,
                        previous_turn_logs=result.turn_logs,
                    )
                    result.turn_logs.append(turn_log)
                    result.info_request_count += 1

                    if turn_log.was_fallback:
                        consecutive_misses += 1
                        print(f"  [Turn {turn_index}] Artifact MISS (consecutive={consecutive_misses})")
                    else:
                        consecutive_misses = 0
                        result.info_request_hits += 1
                        print(f"  [Turn {turn_index}] Artifact HIT: {turn_log.matched_artifact_key} "
                              f"(score={turn_log.artifact_match_score:.3f})")

                    if consecutive_misses >= self._config.max_consecutive_misses:
                        result.stop_reason = "consecutive_misses"
                        result.total_turns = turn_index
                        print(f"  Terminating: {consecutive_misses} consecutive misses")
                        break

                    current_message = next_message
                    continue

                # --- Handle FINAL_ANSWER ---
                if intent == Intent.FINAL_ANSWER:
                    result.final_agent_text = agent_text
                    rc_text, action_text = self._extractor.extract(agent_text)
                    result.root_cause_text = rc_text
                    result.action_text = action_text

                    # Score against oracle using LLM judge (falls back to SemanticMatcher)
                    rc_match = await self._llm_judge_root_cause(
                        rc_text, case.canonical_root_causes,
                    )
                    action_matches = await self._llm_judge_actions(
                        action_text, case.acceptable_actions,
                    )

                    filtered_rc_match = self._validate_root_cause_match(
                        root_cause_text=rc_text,
                        match=rc_match,
                        candidates=case.canonical_root_causes,
                        full_agent_text=agent_text,
                    )
                    filtered_action_matches = self._validate_action_matches(
                        action_text=action_text,
                        matches=action_matches,
                        candidates=case.acceptable_actions,
                    )

                    result.best_root_cause_match = filtered_rc_match
                    result.best_action_matches = filtered_action_matches
                    result.root_cause_hit = filtered_rc_match is not None
                    result.action_hit = self._is_action_success(
                        action_matches=filtered_action_matches,
                        candidates=case.acceptable_actions,
                    )
                    result.e2e_success = result.root_cause_hit and result.action_hit

                    turn_log = TurnLog(
                        turn_index=turn_index,
                        agent_text=agent_text,
                        intent=intent.value,
                        stop_reason=stop_reason,
                    )
                    result.turn_logs.append(turn_log)
                    result.stop_reason = "final_answer"
                    result.total_turns = turn_index

                    print(f"  Final answer reached.")
                    print(f"  Root cause hit: {result.root_cause_hit} "
                          f"(score={(filtered_rc_match.score if filtered_rc_match else 0):.3f})")
                    print(f"  Action hit: {result.action_hit} "
                          f"({len(filtered_action_matches)} action(s) matched)")
                    print(f"  E2E success: {result.e2e_success}")
                    break

                # --- Handle OTHER ---
                turn_log = TurnLog(
                    turn_index=turn_index,
                    agent_text=agent_text,
                    intent=intent.value,
                    stop_reason=stop_reason,
                )
                result.turn_logs.append(turn_log)

                if stop_reason == "end_turn":
                    # Second chance: some models ask for next data in plain text
                    # without emitting proxy_command. Re-route as info request.
                    if self._intent_recognizer.is_request_information(agent_text):
                        print(
                            f"  [Turn {turn_index}] end_turn but text indicates info request, "
                            "re-routing to REQUEST_INFORMATION"
                        )
                        result.turn_logs.pop()
                        turn_log, next_message = await self._handle_info_request(
                            turn_index=turn_index,
                            agent_text=agent_text,
                            proxy_chunk=proxy_chunk,
                            stop_reason=stop_reason,
                            case=case,
                            previous_turn_logs=result.turn_logs,
                        )
                        result.turn_logs.append(turn_log)
                        result.info_request_count += 1
                        if turn_log.was_fallback:
                            consecutive_misses += 1
                            print(f"  [Turn {turn_index}] Artifact MISS (consecutive={consecutive_misses})")
                        else:
                            consecutive_misses = 0
                            result.info_request_hits += 1
                            print(f"  [Turn {turn_index}] Artifact HIT: {turn_log.matched_artifact_key} "
                                  f"(score={turn_log.artifact_match_score:.3f})")
                        current_message = next_message
                        continue

                    # Agent ended turn but no final answer detected.
                    # This may happen on the first turn if agent immediately
                    # concludes from the user report alone (no tool calls needed).
                    # Treat as final answer using full text.
                    print(f"  [Turn {turn_index}] end_turn with no clear structure, "
                          f"treating as final answer")
                    result.final_agent_text = agent_text
                    result.root_cause_text, result.action_text = self._extractor.extract(agent_text)

                    rc_match = await self._llm_judge_root_cause(
                        result.root_cause_text, case.canonical_root_causes,
                    )
                    action_matches = await self._llm_judge_actions(
                        result.action_text, case.acceptable_actions,
                    )
                    filtered_rc_match = self._validate_root_cause_match(
                        root_cause_text=result.root_cause_text,
                        match=rc_match,
                        candidates=case.canonical_root_causes,
                        full_agent_text=agent_text,
                    )
                    filtered_action_matches = self._validate_action_matches(
                        action_text=result.action_text,
                        matches=action_matches,
                        candidates=case.acceptable_actions,
                    )
                    result.best_root_cause_match = filtered_rc_match
                    result.best_action_matches = filtered_action_matches
                    result.root_cause_hit = filtered_rc_match is not None
                    result.action_hit = self._is_action_success(
                        action_matches=filtered_action_matches,
                        candidates=case.acceptable_actions,
                    )
                    result.e2e_success = result.root_cause_hit and result.action_hit
                    result.stop_reason = "final_answer"
                    result.total_turns = turn_index
                    break

                # Agent is still reasoning mid-turn (stop_reason != end_turn not expected here)
                # This shouldn't normally happen; break to avoid infinite loop
                print(f"  [Turn {turn_index}] OTHER intent with stop_reason={stop_reason!r}, stopping")
                result.stop_reason = "other_stop"
                result.total_turns = turn_index
                break

            else:
                # Loop exhausted max_turns
                result.stop_reason = "max_turns"
                result.total_turns = self._config.max_turns
                print(f"  Terminated: max_turns ({self._config.max_turns}) reached")

        except Exception as e:
            result.stop_reason = "error"
            result.error_message = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            result.total_turns = turn_index
            print(f"  ERROR in case {case.case_id}: {e}")

        # Compute info request hit rate
        if result.info_request_count > 0:
            result.info_request_hit_rate = result.info_request_hits / result.info_request_count
        else:
            result.info_request_hit_rate = 1.0  # No requests needed — perfect by definition

        return result

    # ------------------------------------------------------------------
    # Public: run a full suite
    # ------------------------------------------------------------------

    async def run_suite(self, cases: list[BenchmarkCase]) -> list[CaseRunResult]:
        """
        Run all benchmark cases sequentially and return results.

        Cases are run one at a time to avoid LLM API rate limits.
        """
        results: list[CaseRunResult] = []
        for i, case in enumerate(cases):
            print(f"\n[{i+1}/{len(cases)}] Running case: {case.case_id}")
            result = await self.run_case(case)
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Internal: collect agent response chunks
    # ------------------------------------------------------------------

    async def _collect_response(
        self,
        session: "SessionState",
        user_message: str,
    ) -> tuple[str, dict | None, str]:
        """
        Call agent.chat() and collect all chunks until 'done'.

        Retry policy (both errors retry indefinitely):
          - Timeout (asyncio.TimeoutError): wait llm_timeout_retry_delay seconds and retry.
          - Connection error (HTTP 401 "not connected"): call connect(), wait
            llm_timeout_retry_delay seconds, and retry.

        Returns:
            (agent_text, proxy_chunk | None, stop_reason)
            agent_text:  All text_delta chunks concatenated
            proxy_chunk: The first proxy_command chunk found, or None
            stop_reason: The stop_reason from the 'done' chunk
        """
        attempt = 0

        while True:
            attempt += 1
            text_parts: list[str] = []
            proxy_chunk: dict | None = None
            stop_reason = "end_turn"

            async def _collect_chunks() -> None:
                nonlocal proxy_chunk, stop_reason
                async for chunk in self._agent.chat(
                    session=session,
                    user_message=user_message,
                ):
                    ctype = chunk.get("type", "")

                    if ctype == "text_delta":
                        text_parts.append(chunk.get("text", ""))

                    elif ctype == "proxy_command":
                        if proxy_chunk is None:  # Only capture the first proxy command per turn
                            proxy_chunk = chunk

                    elif ctype == "done":
                        stop_reason = chunk.get("stop_reason", "end_turn")

                    elif ctype == "error":
                        raise RuntimeError(f"Agent error: {chunk.get('message', 'unknown')}")

                    # skill_match, state_update etc. — ignore for eval purposes

            try:
                await asyncio.wait_for(
                    _collect_chunks(),
                    timeout=self._config.llm_call_timeout,
                )
                return "".join(text_parts), proxy_chunk, stop_reason

            except asyncio.TimeoutError:
                # Timeout is transient — wait and retry indefinitely until a response arrives.
                print(
                    f"  [LLM] Timeout after {self._config.llm_call_timeout}s "
                    f"(attempt {attempt}), retrying in "
                    f"{self._config.llm_timeout_retry_delay}s..."
                )
                await asyncio.sleep(self._config.llm_timeout_retry_delay)
                continue

            except ConnectionError as exc:
                # 401 "not connected": reconnect and retry indefinitely (same as timeout).
                print(
                    f"  [LLM] Connection error (attempt {attempt}): {exc}\n"
                    f"  [LLM] Reconnecting in {self._config.llm_timeout_retry_delay}s..."
                )
                await asyncio.sleep(self._config.llm_timeout_retry_delay)
                if self._llm_client is not None and hasattr(self._llm_client, "connect"):
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._llm_client.connect
                    )

    # ------------------------------------------------------------------
    # Internal: handle information request turn
    # ------------------------------------------------------------------

    async def _handle_info_request(
        self,
        turn_index: int,
        agent_text: str,
        proxy_chunk: dict | None,
        stop_reason: str,
        case: BenchmarkCase,
        previous_turn_logs: list["TurnLog"] | None = None,
    ) -> tuple[TurnLog, str]:
        """
        Process an information request turn.

        First tries deterministic proxy-hint matching (tool metadata / command /
        instructions). If no match is found, falls back to LLM-based artifact
        selection from the agent output text. If still unresolved, returns the
        fallback message to the agent.

        Returns:
            (TurnLog, next_user_message)
        """
        request_query = self._build_artifact_query(agent_text, proxy_chunk)
        served_artifacts = {
            t.matched_artifact_key
            for t in (previous_turn_logs or [])
            if t.intent == Intent.REQUEST_INFORMATION.value and t.matched_artifact_key
        }
        dedupe_used = False
        semantic_match_score: float | None = None

        # Prefer explicit proxy/tool hints first (deterministic), then fall back to LLM.
        proxy_instruction_key = await self._match_artifact_from_proxy_instructions(
            proxy_chunk=proxy_chunk,
            artifacts=case.information_inventory,
        )
        proxy_hint_key = self._match_artifact_from_proxy_hint(
            proxy_chunk=proxy_chunk,
            artifacts=case.information_inventory,
        )
        artifact_key = proxy_instruction_key or proxy_hint_key
        if artifact_key is None:
            artifact_key = await self._llm_match_artifact(
                agent_text=agent_text,
                proxy_chunk=proxy_chunk,
                artifacts=case.information_inventory,
            )

        # Reduce repetitive loops (e.g. repeatedly selecting statement_history
        # while the agent text clearly asks for full SQL text).
        artifact_key = self._maybe_upgrade_artifact_for_goal(
            artifact_key=artifact_key,
            agent_text=agent_text,
            proxy_chunk=proxy_chunk,
            artifacts=case.information_inventory,
            previous_turn_logs=previous_turn_logs or [],
        )

        # Generic de-duplication: once an artifact has been returned in previous
        # rounds, avoid returning it again; try selecting an unseen artifact.
        if artifact_key is not None and artifact_key in served_artifacts:
            dedupe_used = True
            unseen_artifacts = {
                k: v for k, v in case.information_inventory.items()
                if k not in served_artifacts
            }
            print(
                f"  [Matcher] Dedup: '{artifact_key}' already served, "
                f"retrying with {len(unseen_artifacts)} unseen artifacts"
            )

            artifact_key = None
            if unseen_artifacts:
                alt_proxy_hint_key = self._match_artifact_from_proxy_hint(
                    proxy_chunk=proxy_chunk,
                    artifacts=unseen_artifacts,
                )
                alt_proxy_instruction_key = await self._match_artifact_from_proxy_instructions(
                    proxy_chunk=proxy_chunk,
                    artifacts=unseen_artifacts,
                )
                artifact_key = alt_proxy_instruction_key or alt_proxy_hint_key
                if artifact_key is None:
                    artifact_key = await self._llm_match_artifact(
                        agent_text=agent_text,
                        proxy_chunk=proxy_chunk,
                        artifacts=unseen_artifacts,
                    )
                artifact_key = self._maybe_upgrade_artifact_for_goal(
                    artifact_key=artifact_key,
                    agent_text=agent_text,
                    proxy_chunk=proxy_chunk,
                    artifacts=unseen_artifacts,
                    previous_turn_logs=previous_turn_logs or [],
                )

        # Final fallback: semantic matcher over request_query to reduce MISS rate
        # when proxy-hint/LLM both fail.
        if artifact_key is None:
            semantic_pool = {
                k: v for k, v in case.information_inventory.items()
                if k not in served_artifacts
            }
            if not semantic_pool:
                semantic_pool = case.information_inventory
            semantic_match = self._matcher.match_artifact(
                request_query,
                semantic_pool,
                threshold=self._config.artifact_match_threshold,
            )
            if semantic_match is not None:
                artifact_key = semantic_match.item_id
                semantic_match_score = semantic_match.score
                print(
                    f"  [Matcher] Semantic fallback selected: {artifact_key} "
                    f"(score={semantic_match_score:.3f})"
                )

        if artifact_key is not None:
            artifact = case.information_inventory[artifact_key]
            returned_content = self._format_artifact_response(artifact)
            if proxy_instruction_key == artifact_key:
                explanation = "proxy-instructions-selected"
            elif proxy_hint_key == artifact_key:
                explanation = "proxy-hint-selected"
            elif dedupe_used:
                explanation = "dedupe-selected"
            elif semantic_match_score is not None:
                explanation = "semantic-fallback-selected"
            else:
                explanation = "LLM-selected"
            score = semantic_match_score if semantic_match_score is not None else 1.0
            was_fallback = False
            print(f"  [Matcher] selected: {artifact_key} ({explanation})")
            if proxy_chunk:
                proxy_instructions = str(proxy_chunk.get("instructions", "")).strip()
                if not proxy_instructions:
                    proxy_instructions = str(proxy_chunk.get("command", "")).strip()
                proxy_preview = proxy_instructions.replace("\n", " ").strip()
                if len(proxy_preview) > 180:
                    proxy_preview = proxy_preview[:177] + "..."
                print(f"  [Matcher] proxy_instructions -> artifact: {proxy_preview!r} -> {artifact_key}")
        else:
            returned_content = self._config.fallback_message
            explanation = "no artifact matched"
            score = 0.0
            was_fallback = True

        turn_log = TurnLog(
            turn_index=turn_index,
            agent_text=agent_text,
            intent=Intent.REQUEST_INFORMATION.value,
            stop_reason=stop_reason,
            has_proxy_command=proxy_chunk is not None,
            proxy_tool_name=proxy_chunk.get("tool_name") if proxy_chunk else None,
            proxy_command=proxy_chunk.get("command") if proxy_chunk else None,
            proxy_instructions=proxy_chunk.get("instructions") if proxy_chunk else None,
            request_query=request_query,
            matched_artifact_key=artifact_key,
            artifact_match_score=score,
            artifact_match_explanation=explanation,
            returned_content=returned_content,
            was_fallback=was_fallback,
        )
        return turn_log, returned_content

    async def _match_artifact_from_proxy_instructions(
        self,
        proxy_chunk: dict | None,
        artifacts: dict[str, "Artifact"],
    ) -> str | None:
        """
        Match artifact using proxy_instructions as the primary intent signal.

        This is used to analyze what the agent wants each round based on the
        proxy instruction text, before falling back to broader signals.
        """
        if not proxy_chunk:
            return None
        instructions = str(proxy_chunk.get("instructions", "")).strip()
        if not instructions:
            return None

        # Use execution purpose inside proxy instructions as the primary
        # matching signal when available, then fall back to full instructions.
        if self._llm_client is None:
            return None

        purpose_text = self._extract_proxy_execution_purpose(instructions)
        if purpose_text:
            selected = await self._llm_match_artifact(
                agent_text=purpose_text,
                proxy_chunk=None,
                artifacts=artifacts,
            )
            if selected:
                return selected

        return await self._llm_match_artifact(
            agent_text=instructions,
            proxy_chunk=None,
            artifacts=artifacts,
        )

    @staticmethod
    def _extract_proxy_execution_purpose(instructions: str) -> str | None:
        """
        Extract execution purpose from proxy instructions.

        Example:
          执行目的：查看 orders、order_items、products 表当前索引
        """
        text = instructions.strip()
        if not text:
            return None

        patterns = [
            r"(?:执行目的|目的)\s*[:：]\s*(.+)",
            r"(?:Purpose|Execution Purpose)\s*[:：]\s*(.+)",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, flags=re.IGNORECASE)
            if m:
                line = m.group(1).strip()
                # keep only the first logical line/segment to reduce noise
                line = line.splitlines()[0].strip()
                if line:
                    return line
        return None

    @staticmethod
    def _maybe_upgrade_artifact_for_goal(
        artifact_key: str | None,
        agent_text: str,
        proxy_chunk: dict | None,
        artifacts: dict[str, "Artifact"],
        previous_turn_logs: list["TurnLog"],
    ) -> str | None:
        """
        Heuristic de-looping for repeated info requests.

        If the same intermediary artifact is repeatedly selected (notably
        statement_history), but the current turn text explicitly asks for
        "完整 SQL 文本 / query 字段", upgrade selection to sql_text when available.
        """
        if artifact_key is None:
            return None

        if artifact_key != "statement_history" or "sql_text" not in artifacts:
            return artifact_key

        prev_hits = [
            t for t in previous_turn_logs
            if t.intent == Intent.REQUEST_INFORMATION.value and t.matched_artifact_key == "statement_history"
        ]
        if len(prev_hits) < 1:
            return artifact_key

        tool_name = str((proxy_chunk or {}).get("tool_name", ""))
        command = str((proxy_chunk or {}).get("command", ""))
        instructions = str((proxy_chunk or {}).get("instructions", ""))
        merged = f"{agent_text}\n{tool_name}\n{instructions}\n{command}".lower()

        sql_text_goal_patterns = [
            r"完整\s*sql",
            r"sql\s*文本",
            r"query\s*字段",
            r"sql\s*原文",
            r"完整\s*query",
            r"具体\s*sql",
        ]
        if any(re.search(p, merged, re.IGNORECASE) for p in sql_text_goal_patterns):
            return "sql_text"

        return artifact_key

    @staticmethod
    def _match_artifact_from_proxy_hint(
        proxy_chunk: dict | None,
        artifacts: dict[str, "Artifact"],
    ) -> str | None:
        """
        Deterministic fast-path for artifact selection from proxy metadata.

        Priority:
          1) Exact artifact key mention in tool_name/command/instructions
          2) Retrieval hint match against tool_name/command/instructions
        """
        if not proxy_chunk:
            return None

        tool_name = str(proxy_chunk.get("tool_name", ""))
        command = str(proxy_chunk.get("command", ""))
        instructions = str(proxy_chunk.get("instructions", ""))
        haystack = f"{tool_name}\n{instructions}\n{command}".lower()
        haystack_spaced = haystack.replace("_", " ").replace("-", " ")

        # 1) Exact artifact key mention
        for key in artifacts.keys():
            key_l = key.lower()
            key_spaced = key_l.replace("_", " ")
            if key_l in haystack or key_spaced in haystack_spaced:
                return key

        # 2) retrieval_hints mention
        for key, artifact in artifacts.items():
            for hint in artifact.retrieval_hints:
                hint_l = hint.lower().strip()
                if not hint_l:
                    continue
                hint_spaced = hint_l.replace("_", " ").replace("-", " ")
                if hint_l in haystack or hint_spaced in haystack_spaced:
                    return key

        return None

    # ------------------------------------------------------------------
    # Internal: LLM-based artifact matching
    # ------------------------------------------------------------------

    async def _llm_match_artifact(
        self,
        agent_text: str,
        proxy_chunk: dict | None,
        artifacts: dict[str, "Artifact"],
    ) -> str | None:
        """
        Ask the LLM to choose the most appropriate artifact from agent text.

        Only the agent's own output is used as the intent signal — no tool call
        metadata is passed. The LLM reads what the agent wrote and picks the best
        matching artifact from the numbered list.

        Returns the artifact key string, or None if the call fails or the model
        returns an invalid selection.
        """
        if self._llm_client is None:
            return None

        # Build numbered choice list (1-based)
        keys = list(artifacts.keys())
        choice_lines = []
        for i, key in enumerate(keys, 1):
            artifact = artifacts[key]
            hints = ", ".join(artifact.retrieval_hints[:4])
            choice_lines.append(
                f"{i}. [{key}] {artifact.description}（关键词：{hints}）"
            )
        choice_text = "\n".join(choice_lines)

        # Strip <think> reasoning blocks, keep full agent text
        agent_text_clean = re.sub(r"<think>.*?</think>", "", agent_text, flags=re.DOTALL).strip()

        user_message = (
            f"以下是一个数据库诊断 Agent 的输出文字：\n\n"
            f"---\n{agent_text_clean}\n---\n\n"
            f"请找出 Agent 在这段文字中**明确表示要获取**的那一项数据，从下列选项中选择编号：\n\n"
            f"{choice_text}\n\n"
            f"注意：\n"
            f"- 只看 Agent 说要'获取/查询/查看/检查'什么，不要推断它的动机或背景\n"
            f"- 如果没有合适选项，输出 0\n"
            f"- 只输出一个数字，不要任何解释"
        )
        system = (
            "你是一个评测辅助系统，任务是根据 Agent 的输出文字，判断它当前这一步要获取哪项数据。\n"
            "规则：\n"
            "1. Agent 通常先叙述背景或分析，最后一句才明确说要获取什么——以最后明确的意图为准\n"
            "2. 字面匹配优先：Agent 说'获取慢 SQL 文本' → sql_text；说'查看执行计划' → explain_analyze\n"
            "3. 不要根据 Agent 的推理动机（如'为了诊断性能'）来推断选项"
        )

        max_attempts = self._config.llm_judge_max_attempts
        for attempt in range(1, max_attempts + 1):
            try:
                model_name = self._llm_model
                if not model_name:
                    raise ValueError(
                        "BenchmarkRunner: llm_model must be set when llm_client is provided. "
                        "Pass llm_model='your-model-name' to BenchmarkRunner()."
                    )
                text_parts: list[str] = []
                async for chunk in self._llm_client.stream_chat(
                    model=model_name,
                    system=system,
                    messages=[{"role": "user", "content": user_message}],
                    tools=[],
                    max_tokens=512,
                ):
                    if chunk.get("type") == "text_delta":
                        text_parts.append(chunk.get("text", ""))

                raw = "".join(text_parts).strip()
                cleaned = _strip_think_output(raw)

                # Extract all integers and take the LAST valid one.
                # Reasoning models write conclusions at the end.
                numbers = [int(m) for m in re.findall(r"\b(\d+)\b", cleaned)]
                for n in reversed(numbers):
                    if 1 <= n <= len(keys):
                        return keys[n - 1]

                print(f"  [Matcher] LLM returned no valid number (cleaned={cleaned[:80]!r}), fallback to None")
                return None

            except Exception as exc:
                if self._is_retryable_error(exc) and attempt < max_attempts:
                    print(
                        f"  [Matcher] LLM transient error ({exc}) "
                        f"[attempt {attempt}/{max_attempts}], retrying in "
                        f"{self._config.llm_timeout_retry_delay}s..."
                    )
                    await asyncio.sleep(self._config.llm_timeout_retry_delay)
                    continue
                print(f"  [Matcher] LLM call failed ({exc}) after {attempt} attempts, fallback to None")
                return None
        return None

    # ------------------------------------------------------------------
    # Internal: LLM-based intent classification (final answer detection)
    # ------------------------------------------------------------------

    async def _llm_judge_final_answer(self, agent_text: str) -> bool | None:
        """
        Ask the LLM whether the agent's output constitutes a complete diagnosis.

        A complete diagnosis requires BOTH:
          - A specific root cause (named table/column/operation, not just a guess)
          - A concrete action recommendation (not "I need to check more" or future tense)

        Returns True if complete, False if not, None if the LLM call fails
        (caller will fall back to keyword pattern matching).
        """
        agent_tail = re.sub(r"<think>.*?</think>", "", agent_text, flags=re.DOTALL).strip()
        agent_tail = agent_tail[-2000:]

        system = (
            "你是评测辅助系统，判断 Agent 的回答是否已经完成了数据库诊断。\n"
            "按以下两步判断：\n"
            "1. Agent 是否明确给出了根因——具体说明了什么组件/什么机制/什么操作导致了问题"
            "（不是'可能是'或'需要进一步确认'）。注意：根因可能是硬件故障、存储异常、"
            "网络问题、配置错误等，不限于表或SQL层面。\n"
            "2. Agent 是否明确给出了操作建议——具体说明了应该执行哪些修复步骤"
            "（不是'我之后可以给出建议'或'让我再检查一下'）\n"
            "两个条件都满足才算完成。\n"
            "\n"
            "输出格式（ANSWER 必须在第一行）：\n"
            "第一行：ANSWER: yes 或 ANSWER: no\n"
            "第二行：REASON: <一句话——根因是否明确 / 操作是否明确>"
        )
        user_message = f"Agent 输出如下：\n\n{agent_tail}"

        max_attempts = self._config.llm_judge_max_attempts
        for attempt in range(1, max_attempts + 1):
            try:
                model_name = self._llm_model
                if not model_name:
                    raise ValueError(
                        "BenchmarkRunner: llm_model must be set when llm_client is provided. "
                        "Pass llm_model='your-model-name' to BenchmarkRunner()."
                    )
                text_parts: list[str] = []
                async for chunk in self._llm_client.stream_chat(
                    model=model_name,
                    system=system,
                    messages=[{"role": "user", "content": user_message}],
                    tools=[],
                    max_tokens=64,
                ):
                    if chunk.get("type") == "text_delta":
                        text_parts.append(chunk.get("text", ""))

                raw = "".join(text_parts).strip()
                cleaned_text = _strip_think_output(raw)
                self._last_final_judge_raw = cleaned_text
                cleaned = cleaned_text.lower()
                if "yes" in cleaned:
                    return True
                if "no" in cleaned:
                    return False
                # Unexpected output — treat as unknown
                print(f"  [Intent] LLM final-answer judge unexpected output: {cleaned_text[:40]!r}")
                return None

            except Exception as exc:
                if self._is_retryable_error(exc) and attempt < max_attempts:
                    self._last_final_judge_raw = "[retrying_on_transient]"
                    print(
                        f"  [Intent] LLM final-answer judge transient error ({exc}) "
                        f"[attempt {attempt}/{max_attempts}], "
                        f"retrying in {self._config.llm_timeout_retry_delay}s..."
                    )
                    await asyncio.sleep(self._config.llm_timeout_retry_delay)
                    continue
                self._last_final_judge_raw = f"[error] {exc}"
                print(f"  [Intent] LLM final-answer judge failed ({exc}) after {attempt} attempts, fallback to patterns")
                return None
        return None

    # ------------------------------------------------------------------
    # Internal: LLM-based oracle evaluation (root cause + action)
    # ------------------------------------------------------------------

    async def _llm_judge_root_cause(
        self,
        agent_text: str,
        candidates: list,
    ) -> "MatchResult | None":
        """
        Ask the LLM whether the agent correctly identified a root cause.

        Stricter than keyword matching: the agent must name specific technical
        details (table, column, operation type) — generic terms alone are not
        enough. Falls back to SemanticMatcher if the LLM call fails.
        """
        if self._llm_client is None or not candidates:
            return self._matcher.match_root_cause(
                answer_text=agent_text,
                candidates=candidates,
                threshold=self._config.root_cause_match_threshold,
            )

        keys = [rc.root_cause_id for rc in candidates]
        choice_lines = []
        for i, rc in enumerate(candidates, 1):
            desc_snippet = rc.description[:120].replace("\n", " ")
            choice_lines.append(f"{i}. {rc.summary}（判断依据：{desc_snippet}）")
        choice_text = "\n".join(choice_lines)

        agent_tail = re.sub(r"<think>.*?</think>", "", agent_text, flags=re.DOTALL).strip()
        agent_tail = agent_tail[-1500:]

        user_message = (
            f"【Agent 提取后的根因描述】：\n{agent_tail}\n\n"
            f"【候选根因】（选命中的编号，没有则 0）：\n{choice_text}\n\n"
            f"请判断该根因描述与哪个候选根因在语义上等价："
        )
        system = (
            "你是数据库故障诊断评测裁判。按以下三步流程严格判断 agent 根因诊断是否命中候选根因。\n"
            "\n"
            "STEP 1 — 候选根因机制抽象。\n"
            "对每个候选根因，用一句话概括其指向的物理/逻辑故障机制。使用运维语言描述"
            "『什么东西以什么方式坏了』，不要照抄原文措辞。\n"
            "\n"
            "STEP 2 — Agent 预测机制抽象。\n"
            "用同样的抽象层级，一句话概括 agent 诊断指向的故障机制。\n"
            "如果 agent 只是描述下游症状、中间环节，没有指向根因，或回答为空/说『无法确认根因』，"
            "在此步骤写『(无根因承诺)』。\n"
            "\n"
            "STEP 3 — 逐候选比较。\n"
            "对每个候选，比较其 Step 1 机制与 Step 2 机制：\n"
            "\n"
            "  命中（HIT）条件：\n"
            "  - 故障层级一致（硬件/OS/文件系统/数据库内核/管控集群/网络）且触发机制相同或互相蕴含\n"
            "  - Agent 比候选更具体（给出参数/命令/表名），但指向同一机制 → 命中\n"
            "  - 同义改写：'通用计划缓存致全表扫描' ≡ '参数嗅探致 Generic Plan 偏差'\n"
            "  - 更上位措辞但同一根因：'统计信息分布失真' ≡ '统计信息采样偏差致数据分布判断失真'\n"
            "  - Agent 没提具体表名/列名，但机制描述清楚 → 仍算命中（case 数据本身无真实表名）\n"
            "\n"
            "  不命中（MISS）条件：\n"
            "  - 故障层级不同：'NAS 服务异常' vs '存储介质硬件故障'（重启服务 vs 换盘）\n"
            "  - 触发机制不同：'work_mem 不足' vs 'SQL 逻辑低效'（改参数 vs 改 SQL）\n"
            "  - 同样症状但根因路径不同：'网络丢包致重试风暴' vs '网络抖动致心跳超时阻塞选主'\n"
            "  - Agent 仅描述症状/中间环节，未指向根因 → MISS\n"
            "  - Agent 预测为空或拒绝承诺 → MISS\n"
            "\n"
            "如有多个候选都等价，选序号最小的那个。没有任何候选等价则选 0。\n"
            "\n"
            "输出格式（ANSWER 必须在第一行，防止截断丢失）：\n"
            "第一行：ANSWER: N（命中候选编号，1-based；无命中则 ANSWER: 0）\n"
            "第二行：GOLD: <选中的候选根因机制抽象；若 ANSWER:0 则写 N/A>\n"
            "第三行：PRED: <agent 诊断机制抽象>\n"
            "第四行：REASON: <一句话——同一机制 or 为何不命中>"
        )

        max_attempts = self._config.llm_judge_max_attempts
        for attempt in range(1, max_attempts + 1):
            try:
                model_name = self._llm_model
                if not model_name:
                    raise ValueError(
                        "BenchmarkRunner: llm_model must be set when llm_client is provided. "
                        "Pass llm_model='your-model-name' to BenchmarkRunner()."
                    )
                text_parts: list[str] = []
                async for chunk in self._llm_client.stream_chat(
                    model=model_name,
                    system=system,
                    messages=[{"role": "user", "content": user_message}],
                    tools=[],
                    max_tokens=256,
                ):
                    if chunk.get("type") == "text_delta":
                        text_parts.append(chunk.get("text", ""))

                raw = "".join(text_parts).strip()
                cleaned = _strip_think_output(raw)
                numbers = [int(m) for m in re.findall(r"\b(\d+)\b", cleaned)]
                for n in reversed(numbers):
                    if 1 <= n <= len(keys):
                        rc = candidates[n - 1]
                        print(f"  [Judge] Root cause hit: {keys[n - 1]}")
                        return MatchResult(
                            item_id=rc.root_cause_id,
                            item=rc.to_dict(),
                            score=1.0,
                            field_scores={},
                            matched_keywords=[],
                            explanation=f"LLM judge selected #{n}: {rc.summary}",
                        )

                # Distinguish "LLM said 0 (strict no-match)" from "LLM output
                # malformed". The strict judge exists precisely to filter out
                # the keyword matcher's false positives — we MUST trust a clean
                # 0 verdict and not fall back to the lenient matcher.
                if 0 in numbers:
                    print(f"  [Judge] Root cause: LLM strict NO-MATCH (cleaned={cleaned[:60]!r})")
                    return None

                print(
                    f"  [Judge] Root cause: malformed LLM output, fallback to "
                    f"SemanticMatcher (cleaned={cleaned[:60]!r})"
                )
                return self._matcher.match_root_cause(
                    answer_text=agent_text,
                    candidates=candidates,
                    threshold=self._config.root_cause_match_threshold,
                )

            except Exception as exc:
                if self._is_retryable_error(exc) and attempt < max_attempts:
                    print(
                        f"  [Judge] Root cause judge transient error ({exc}) "
                        f"[attempt {attempt}/{max_attempts}], retrying in "
                        f"{self._config.llm_timeout_retry_delay}s..."
                    )
                    await asyncio.sleep(self._config.llm_timeout_retry_delay)
                    continue
                print(f"  [Judge] LLM failed for root cause ({exc}) after {attempt} attempts, fallback to SemanticMatcher")
                return self._matcher.match_root_cause(
                    answer_text=agent_text,
                    candidates=candidates,
                    threshold=self._config.root_cause_match_threshold,
                )
        return self._matcher.match_root_cause(
            answer_text=agent_text,
            candidates=candidates,
            threshold=self._config.root_cause_match_threshold,
        )

    async def _llm_judge_actions(
        self,
        agent_text: str,
        candidates: list,
    ) -> "list[MatchResult]":
        """
        Ask the LLM which acceptable actions the agent recommended.

        Multiple actions can be matched (comma-separated numbers). Stricter than
        keyword matching: agent must specify concrete details, not vague advice.
        Falls back to SemanticMatcher if the LLM call fails.
        """
        if self._llm_client is None or not candidates:
            return self._matcher.match_all_actions(
                answer_text=agent_text,
                candidates=candidates,
                threshold=self._config.action_match_threshold,
            )

        choice_lines = []
        for i, action in enumerate(candidates, 1):
            cmd_example = action.command_templates[0] if action.command_templates else ""
            choice_lines.append(
                f"{i}. {action.summary}"
                + (f"（参考命令：{cmd_example}）" if cmd_example else "")
            )
        choice_text = "\n".join(choice_lines)

        agent_tail = re.sub(r"<think>.*?</think>", "", agent_text, flags=re.DOTALL).strip()
        agent_tail = agent_tail[-1500:]

        user_message = (
            f"【Agent 提取后的操作建议】：\n{agent_tail}\n\n"
            f"【可接受操作】（可多选，最多选3个，逗号分隔，没有则 0）：\n{choice_text}\n\n"
            f"请判断这些操作建议与哪些候选操作语义等价："
        )
        system = (
            "你是数据库故障运维评测裁判。判断 agent 的修复建议是否能兑现候选操作"
            "——即『按 agent 给的方案做下去，能否完成候选操作要做的事』。\n"
            "\n"
            "判定原则（从运维实用视角，不要咬文嚼字）：\n"
            "1. 看『方案的实际操作效果』是否一致，不要看措辞或命令模板是否完全相同。\n"
            "   例如候选 '调整temp_file_limit参数' 与 agent 的 'ALTER SYSTEM SET "
            "   temp_file_limit = 10GB' 等价（同一动作，更具体的写法）。\n"
            "2. agent 必须**真的写了可执行操作**——SQL/SHELL命令/配置修改/明确的运维步骤。"
            "   空话不算：'这样我就能给建议' 不命中。\n"
            "3. 如果 agent 把候选拆成了多个更细的步骤，仍算命中；反之候选粗 agent 细也算。\n"
            "4. **从 DBA 操作视角判断效果等价，不要纠结硬件 vs 服务层的文字区别。**\n"
            "   例如：'修复异常磁盘' ≡ 'umount -l + 重新 mount NAS + 验证读写'（DBA 能做"
            "   的就是在 OS 层面恢复存储可用性，这本身就是修复）。\n"
            "   例如：'修复异常磁盘' ≡ '重启节点恢复 I/O'（重启后 D 状态进程清除，"
            "   磁盘恢复可访问，效果一样）。\n"
            "\n"
            "重要——以下情况都算等价：\n"
            "- 候选 '查杀idle会话' ≡ agent 'pg_terminate_backend(pid) WHERE state=idle'"
            "（同一动作）\n"
            "- 候选 '手动删除临时文件' ≡ agent 'rm -rf $GAUSSDATA/pgsql_temp/*'（同一动作）\n"
            "- 候选 '客户侧修复异常磁盘' ≡ agent 'umount -l + 重新挂载 NAS + 验证'"
            "（从 DBA 视角，恢复存储可用性就是修复）\n"
            "- agent 的方案没有点出具体表/列名（case 数据本身就没有真实表名时这是常态），"
            "  但操作类型和方向对得上，仍算命中。\n"
            "\n"
            "以下情况算不命中：\n"
            "- 候选 '调整 work_mem 参数' vs agent '改写 SQL 减少排序'（虽然都是"
            "  优化方向，但操作手段不同）\n"
            "- 候选 '扩容磁盘' vs agent '删除过期备份释放空间'（都解决空间问题，"
            "  但操作手段不同）\n"
            "- agent 只是讨论可能性，没给具体操作。\n"
            "\n"
            "可以有多个命中，用逗号分隔（最多 3 个）。没有命中输出 0。"
            "**只输出编号，不要任何解释。**"
        )

        max_attempts = self._config.llm_judge_max_attempts
        for attempt in range(1, max_attempts + 1):
            try:
                model_name = self._llm_model
                if not model_name:
                    raise ValueError(
                        "BenchmarkRunner: llm_model must be set when llm_client is provided. "
                        "Pass llm_model='your-model-name' to BenchmarkRunner()."
                    )
                text_parts: list[str] = []
                async for chunk in self._llm_client.stream_chat(
                    model=model_name,
                    system=system,
                    messages=[{"role": "user", "content": user_message}],
                    tools=[],
                    max_tokens=256,
                ):
                    if chunk.get("type") == "text_delta":
                        text_parts.append(chunk.get("text", ""))

                raw = "".join(text_parts).strip()
                cleaned = _strip_think_output(raw)
                numbers = [int(m) for m in re.findall(r"\b(\d+)\b", cleaned)]

                hits: list[MatchResult] = []
                seen: set[int] = set()
                for n in numbers:
                    if 1 <= n <= len(candidates) and n not in seen:
                        seen.add(n)
                        action = candidates[n - 1]
                        hits.append(MatchResult(
                            item_id=action.action_id,
                            item=action.to_dict(),
                            score=1.0,
                            field_scores={},
                            matched_keywords=[],
                            explanation=f"LLM judge selected #{n}: {action.summary}",
                        ))
                        if len(hits) >= 3:
                            break

                if hits:
                    ids = ", ".join(h.item_id for h in hits)
                    print(f"  [Judge] Action hits: {ids}")
                    return hits

                # Trust strict-0 from the LLM, same as in root cause judge.
                if 0 in numbers:
                    print(f"  [Judge] Actions: LLM strict NO-MATCH (cleaned={cleaned[:60]!r})")
                    return []

                print(
                    f"  [Judge] Actions: malformed LLM output, fallback to "
                    f"SemanticMatcher (cleaned={cleaned[:60]!r})"
                )
                return self._matcher.match_all_actions(
                    answer_text=agent_text,
                    candidates=candidates,
                    threshold=self._config.action_match_threshold,
                )

            except Exception as exc:
                if self._is_retryable_error(exc) and attempt < max_attempts:
                    print(
                        f"  [Judge] Action judge transient error ({exc}) "
                        f"[attempt {attempt}/{max_attempts}], retrying in "
                        f"{self._config.llm_timeout_retry_delay}s..."
                    )
                    await asyncio.sleep(self._config.llm_timeout_retry_delay)
                    continue
                print(f"  [Judge] LLM failed for actions ({exc}) after {attempt} attempts, fallback to SemanticMatcher")
                return self._matcher.match_all_actions(
                    answer_text=agent_text,
                    candidates=candidates,
                    threshold=self._config.action_match_threshold,
                )
        return self._matcher.match_all_actions(
            answer_text=agent_text,
            candidates=candidates,
            threshold=self._config.action_match_threshold,
        )

    # ------------------------------------------------------------------
    # Internal: build artifact query from proxy context
    # ------------------------------------------------------------------

    def _build_artifact_query(
        self,
        agent_text: str,
        proxy_chunk: dict | None,
    ) -> str:
        """
        Construct the query string used to match an artifact.

        Combines:
          - Tool name (e.g., "get_explain_plan" → strong signal for explain_analyze)
          - Tool instructions (natural language description of what's needed)
          - SQL command text (table names, columns, etc.)
          - Agent text (may include natural language info requests)
        """
        parts: list[str] = []

        if proxy_chunk:
            tool_name = proxy_chunk.get("tool_name", "")
            command = proxy_chunk.get("command", "")
            instructions = proxy_chunk.get("instructions", "")

            # Map common tool names to their semantic equivalents
            tool_name_expanded = _TOOL_NAME_EXPANSION.get(tool_name, tool_name)
            parts.append(tool_name_expanded)

            if instructions:
                parts.append(instructions)
            if command:
                # Include command but limit length (SQL can be very long)
                parts.append(command[:500])

        # Include relevant agent text (last 400 chars to capture recent request)
        if agent_text:
            parts.append(agent_text[-400:])

        return " ".join(parts)

    @staticmethod
    def _extract_hint_tokens(candidate) -> list[str]:
        """Extract searchable anchors from a candidate's matching_hints / summary.

        Returns a deduplicated list of:
          - ASCII identifiers of length >= 3 (e.g. rownum, limit, SQL)
          - Whole Chinese runs of length >= 3 (e.g. 优化器改写, 存储介质硬件故障)

        2-char shingles are deliberately dropped because terms like
        "故障"/"异常"/"超时" appear in nearly every diagnostic answer and
        turn the consistency check into a rubber stamp.

        Empty hints/summaries return [], signaling the caller to either
        skip the gate or fall back to a different strategy.
        """
        sources: list[str] = []
        for h in getattr(candidate, "matching_hints", None) or []:
            if isinstance(h, str) and h.strip():
                sources.append(h)
        summary = getattr(candidate, "summary", "") or ""
        if summary.strip():
            sources.append(summary)

        tokens: set[str] = set()
        for s in sources:
            for m in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", s):
                tokens.add(m.lower())
            # Whole CJK runs only — no 2-char shingles. Require length >= 3
            # so common nouns like "故障" don't pollute the anchor set.
            for run in re.findall(r"[一-鿿]+", s):
                if len(run) >= 3:
                    tokens.add(run)
        return sorted(tokens)

    # Minimum number of distinct anchor hits required to consider the answer
    # consistent with a candidate. With shingles removed, single-anchor hits
    # are too unreliable — require at least 2 unless the candidate has only
    # 1 anchor available.
    _MIN_ANCHOR_HITS = 2

    def _has_key_field_consistency(self, answer_text: str, candidate) -> bool:
        """
        Sanity-check that the answer is topically consistent with the candidate.

        Strategy:
          - Pull anchor tokens from candidate.matching_hints + candidate.summary.
          - Require the answer (case-insensitive substring match) to contain
            at least min(_MIN_ANCHOR_HITS, len(tokens)) distinct anchors.
          - If no usable anchors are extractable, fall back to a soft pass
            (return True) since we have nothing to check against — but this
            should be rare given matching_hints is a required field on cases.
        """
        tokens = self._extract_hint_tokens(candidate)
        if not tokens:
            return True

        answer_l = answer_text.lower()
        hits = sum(1 for tok in tokens if tok in answer_l)
        required = min(self._MIN_ANCHOR_HITS, len(tokens))
        return hits >= required

    # Pattern matching root-cause IDs the agent declares in its final text:
    # examples: "A1-RC-03", "B1-RC-04", "E3-RC-08", "C5-RC-12".
    _CLAIMED_RC_ID_PATTERN = re.compile(r"\b([A-Z]\d-RC-\d{2,3})\b")

    @classmethod
    def _extract_claimed_rc_ids(cls, *texts: str) -> set[str]:
        """Pull out root-cause IDs the agent explicitly declared in its text.

        We deliberately do NOT extract these from oracle hint text the
        candidates were built from — the input here is the agent's own
        output (final_agent_text / rc_text), so any RC-style identifier
        present is something the agent typed.
        """
        ids: set[str] = set()
        for t in texts:
            if not t:
                continue
            ids.update(cls._CLAIMED_RC_ID_PATTERN.findall(t))
        return ids

    def _is_off_target_claim(
        self,
        claimed_ids: set[str],
        candidates: list,
    ) -> bool:
        """True when the agent declared explicit RC IDs but none of them
        are in the oracle candidate set (i.e. agent is confidently wrong
        about the ID, regardless of whether keyword scoring incidentally
        matched a correct candidate).

        If claimed_ids is empty, returns False — agents that don't quote
        IDs are not penalized.
        """
        if not claimed_ids:
            return False
        oracle_ids = {c.root_cause_id for c in candidates}
        return not (claimed_ids & oracle_ids)

    def _validate_root_cause_match(
        self,
        root_cause_text: str,
        match: "MatchResult | None",
        candidates: list,
        full_agent_text: str | None = None,
    ) -> "MatchResult | None":
        if match is None:
            return None
        candidate = next(
            (c for c in candidates if c.root_cause_id == match.item_id),
            None,
        )
        if candidate is None:
            return None
        # LLM-judge hits use score=1.0 as a sentinel (not a real similarity
        # score). They represent a semantic judgment that is stronger than
        # the keyword-based threshold, so bypass the threshold gate entirely.
        is_llm_judge_hit = abs(match.score - 1.0) < 1e-9

        threshold = self._config.root_cause_match_threshold
        if match.score < threshold and not is_llm_judge_hit:
            return None

        # Bypass consistency on high-confidence matcher scores. Empirically
        # at >=1.5× threshold the score is dominated by matching_hints +
        # summary overlap (not description spillover), so it's safe to
        # trust. Configurable via EvalConfig.consistency_bypass_score_ratio.
        bypass_score = threshold * self._config.consistency_bypass_score_ratio
        consistency_bypassed = is_llm_judge_hit or match.score >= bypass_score

        if not consistency_bypassed and not self._has_key_field_consistency(
            root_cause_text, candidate
        ):
            return None

        # Agent self-claimed-ID gate. Only enforced when the operator
        # explicitly opts into strict-claimed-ids mode (default off). For
        # the default ops-perspective evaluation we don't care which label
        # the agent wrote — only whether the diagnosis is semantically
        # correct, which the matcher / LLM judge already covers.
        if self._config.require_claimed_id_match:
            claimed_ids = self._extract_claimed_rc_ids(
                root_cause_text, full_agent_text or ""
            )
            if self._is_off_target_claim(claimed_ids, candidates):
                return None
        return match

    def _validate_action_matches(
        self,
        action_text: str,
        matches: list["MatchResult"],
        candidates: list,
    ) -> list["MatchResult"]:
        candidate_map = {c.action_id: c for c in candidates}
        threshold = self._config.action_match_threshold
        bypass_score = threshold * self._config.consistency_bypass_score_ratio
        valid: list[MatchResult] = []
        seen: set[str] = set()
        for m in matches:
            if m.item_id in seen or m.item_id not in candidate_map:
                continue
            # LLM-judge hits use score=1.0 as a sentinel (not a real
            # similarity score). Bypass the keyword threshold gate.
            is_llm_judge_hit = abs(m.score - 1.0) < 1e-9
            if m.score < threshold and not is_llm_judge_hit:
                continue

            # Same bypass logic as RC: skip the substring consistency check
            # for LLM-judge hits and high-confidence matcher scores.
            if not (is_llm_judge_hit or m.score >= bypass_score):
                if not self._has_key_field_consistency(
                    action_text, candidate_map[m.item_id]
                ):
                    continue
            seen.add(m.item_id)
            valid.append(m)
        return valid

    def _is_action_success(self, action_matches: list["MatchResult"], candidates: list) -> bool:
        if len(action_matches) < self._config.min_action_hits:
            return False
        if not self._config.require_primary_action:
            return True

        # Primary gate: only enforced when the case has actually annotated at
        # least one action with primary=True. Cases with no primary annotation
        # still pass on min_action_hits — this allows incremental annotation
        # without breaking unannotated cases.
        primary_ids = [a.action_id for a in candidates if getattr(a, "primary", False)]
        if not primary_ids:
            return True
        hit_ids = {m.item_id for m in action_matches}
        return any(pid in hit_ids for pid in primary_ids)

    # ------------------------------------------------------------------
    # Internal: format artifact for return
    # ------------------------------------------------------------------

    @staticmethod
    def _format_artifact_response(artifact: Artifact) -> str:
        """Format artifact content as the 'user result' message returned to agent."""
        return (
            f"[查询结果 - {artifact.description}]\n\n"
            f"{artifact.content}"
        )


# ---------------------------------------------------------------------------
# Tool name → semantic expansion mapping
# ---------------------------------------------------------------------------

_TOOL_NAME_EXPANSION: dict[str, str] = {
    "get_explain_plan":          "执行计划 explain plan explain analyze",
    "query_active_sessions":     "活跃会话 active sessions pg_stat_activity",
    "query_slow_statements":     "慢SQL 慢查询 slow statements slow query",
    "query_locks":               "锁等待 lock info lock waits pg_locks",
    "query_table_stats":         "表统计信息 table stats pg_stat_user_tables",
    "query_index_usage":         "索引使用情况 index usage pg_stat_user_indexes",
    "query_wait_events":         "等待事件 wait events wait info",
    "analyze_sql":               "SQL文本 SQL原文 query text",
    "analyze_execution_plan":    "执行计划 explain analyze plan tree",
    "parse_db_logs":             "数据库日志 db logs error log slow log",
    "query_wlm_statistics":      "WLM资源管理 workload management",
    "query_temp_space":          "临时空间 temp space spill to disk",
    "query_buffer_stats":        "缓冲区统计 buffer stats shared_buffers",
    "query_config_params":       "配置参数 GUC settings config parameters",
}

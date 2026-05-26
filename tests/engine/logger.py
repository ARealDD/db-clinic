"""
InteractionLogger — structured JSONL logging for benchmark runs.

Log file layout:
  <output_dir>/
    run_<timestamp>/
      <case_id>.jsonl    — one JSON object per case (complete interaction trace)
      summary.json       — suite-level aggregate metrics

Each case JSONL line contains:
  - case metadata (id, user_report)
  - per-turn logs (agent text, intent, proxy command, artifact match, returned content)
  - final answer analysis (extracted root cause / action, oracle match scores)
  - evaluation verdicts (root_cause_hit, action_hit, e2e_success)
  - performance counters (total_turns, info_request_hit_rate)

This format enables:
  - Full replay of any diagnostic session
  - Offline LLM judge evaluation of final answers
  - Aggregate statistical analysis
  - Error investigation for failed cases
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.engine.runner import CaseRunResult
    from tests.engine.metrics import SuiteResult
    from tests.engine.schema import BenchmarkCase


class InteractionLogger:
    """
    Writes benchmark results to structured JSONL and JSON files.

    Usage:
        logger = InteractionLogger(output_dir="experiments/results")
        logger.log_case(case, result)          # Call after each case
        logger.log_suite_summary(suite_result)  # Call after full suite
        print(f"Results in: {logger.run_dir}")
    """

    def __init__(
        self,
        output_dir: str = "experiments/results",
        existing_run_dir: str | None = None,
    ):
        if existing_run_dir is not None:
            # Reuse an existing run directory in place (e.g. for --rerun-errors).
            self.run_dir = Path(existing_run_dir)
            self.run_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._output_dir = Path(output_dir)
            # Create a timestamped run directory
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.run_dir = self._output_dir / f"run_{ts}"
            self.run_dir.mkdir(parents=True, exist_ok=True)
        self._logged_cases: list[str] = []
        print(f"[Logger] Output directory: {self.run_dir}")

    def log_case(self, case: "BenchmarkCase", result: "CaseRunResult") -> None:
        """
        Write the complete interaction log for a single case.

        File: <run_dir>/<case_id>.jsonl (one JSON object, pretty-printed for readability)
        """
        record = self._build_case_record(case, result)
        log_path = self.run_dir / f"{_sanitize_filename(case.case_id)}.json"

        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        self._logged_cases.append(case.case_id)
        print(f"[Logger] Case logged → {log_path.name}")

    def log_suite_summary(self, suite: "SuiteResult") -> None:
        """
        Write aggregated suite metrics to summary.json.

        Also writes a CSV-format quick summary for easy spreadsheet import.
        """
        summary_data = {
            "run_timestamp": datetime.now().isoformat(),
            "metrics": suite.to_dict(),
            "case_ids": self._logged_cases,
        }

        summary_path = self.run_dir / "summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)

        # Also write a flat CSV for quick analysis
        csv_path = self.run_dir / "results.csv"
        self._write_csv(suite, csv_path)

        print(f"[Logger] Suite summary → {summary_path.name}")
        print(f"[Logger] CSV results  → {csv_path.name}")
        print(f"[Logger] Run dir: {self.run_dir}")

    # ------------------------------------------------------------------
    # Internal: build case record
    # ------------------------------------------------------------------

    def _build_case_record(
        self, case: "BenchmarkCase", result: "CaseRunResult"
    ) -> dict:
        return {
            # --- Case metadata ---
            "case_id": case.case_id,
            "user_report": case.user_report,
            "artifact_keys": list(case.information_inventory.keys()),
            "oracle_root_cause_ids": [rc.root_cause_id for rc in case.canonical_root_causes],
            "oracle_action_ids": [a.action_id for a in case.acceptable_actions],

            # --- Evaluation verdicts ---
            "root_cause_hit": result.root_cause_hit,
            "action_hit": result.action_hit,
            "e2e_success": result.e2e_success,

            # --- Quantitative metrics ---
            "total_turns": result.total_turns,
            "info_request_count": result.info_request_count,
            "info_request_hits": result.info_request_hits,
            "info_request_hit_rate": round(result.info_request_hit_rate, 4),
            "stop_reason": result.stop_reason,

            # --- Final answer analysis ---
            "final_agent_text": result.final_agent_text,
            "extracted_root_cause_text": result.root_cause_text,
            "extracted_action_text": result.action_text,
            "best_root_cause_match": (
                result.best_root_cause_match.to_dict()
                if result.best_root_cause_match else None
            ),
            "best_action_matches": [m.to_dict() for m in result.best_action_matches],

            # --- Error info (if any) ---
            "error_message": result.error_message or None,

            # --- Full interaction trace ---
            "turns": [self._build_turn_record(t) for t in result.turn_logs],
        }

    @staticmethod
    def _build_turn_record(turn: "CaseRunResult") -> dict:
        """Serialize a TurnLog to dict (already implemented in TurnLog.to_dict)."""
        # Use the TurnLog.to_dict() method — the type hint above is wrong for lint
        # but the object is always a TurnLog at runtime
        return turn.to_dict()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Internal: CSV writer
    # ------------------------------------------------------------------

    @staticmethod
    def _write_csv(suite: "SuiteResult", path: Path) -> None:
        """Write a flat CSV with one row per case."""
        header = (
            "case_id,root_cause_hit,action_hit,e2e_success,"
            "total_turns,info_request_count,info_request_hit_rate,"
            "rc_score,top_action_score,stop_reason\n"
        )
        rows = []
        for r in suite.case_results:
            rc_score = round(r.best_root_cause_match.score, 4) if r.best_root_cause_match else 0.0
            top_action_score = round(r.best_action_matches[0].score, 4) if r.best_action_matches else 0.0
            rows.append(
                f"{r.case_id},"
                f"{int(r.root_cause_hit)},"
                f"{int(r.action_hit)},"
                f"{int(r.e2e_success)},"
                f"{r.total_turns},"
                f"{r.info_request_count},"
                f"{round(r.info_request_hit_rate, 4)},"
                f"{rc_score},"
                f"{top_action_score},"
                f"{r.stop_reason}\n"
            )

        with open(path, "w", encoding="utf-8") as f:
            f.write(header)
            f.writelines(rows)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    """Replace characters invalid in filenames."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)

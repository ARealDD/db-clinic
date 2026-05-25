"""
Evaluation metrics — aggregation and reporting for benchmark suite results.

Per-case metrics (in CaseRunResult):
  - root_cause_hit:       Was the correct root cause identified?
  - action_hit:           Was at least one acceptable action recommended?
  - e2e_success:          Both root_cause_hit AND action_hit
  - total_turns:          How many agent turns were used
  - info_request_count:   How many times did the agent request information
  - info_request_hits:    How many requests matched an artifact (score >= threshold)
  - info_request_hit_rate: hits / count (quality of information-seeking behavior)

Suite-level metrics (in SuiteResult):
  - root_cause_accuracy:  % of cases with root_cause_hit
  - action_accuracy:      % of cases with action_hit
  - e2e_accuracy:         % of cases with e2e_success
  - avg_turns:            Mean total_turns across all cases
  - avg_info_hit_rate:    Mean info_request_hit_rate across all cases
  - breakdown by stop_reason
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eval.engine.runner import CaseRunResult


@dataclass
class SuiteResult:
    """Aggregated metrics for a complete benchmark suite run."""
    total_cases: int
    cases_with_final_answer: int        # Cases that reached final_answer stop reason
    cases_terminated_early: int         # max_turns | consecutive_misses | error

    root_cause_accuracy: float          # root_cause_hit / total_cases
    action_accuracy: float              # action_hit / total_cases
    e2e_accuracy: float                 # e2e_success / total_cases

    avg_turns: float                    # Mean total_turns
    avg_info_hit_rate: float            # Mean info_request_hit_rate
    avg_info_requests_per_case: float   # Mean info_request_count

    stop_reason_counts: dict[str, int]  # stop_reason -> count

    case_results: list["CaseRunResult"] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        return {
            "total_cases": self.total_cases,
            "cases_with_final_answer": self.cases_with_final_answer,
            "cases_terminated_early": self.cases_terminated_early,
            "root_cause_accuracy": round(self.root_cause_accuracy, 4),
            "action_accuracy": round(self.action_accuracy, 4),
            "e2e_accuracy": round(self.e2e_accuracy, 4),
            "avg_turns": round(self.avg_turns, 2),
            "avg_info_hit_rate": round(self.avg_info_hit_rate, 4),
            "avg_info_requests_per_case": round(self.avg_info_requests_per_case, 2),
            "stop_reason_counts": self.stop_reason_counts,
        }

    def print_report(self) -> None:
        """Print a formatted evaluation report to stdout."""
        _print_suite_report(self)


def compute_suite_metrics(case_results: list["CaseRunResult"]) -> SuiteResult:
    """Aggregate per-case results into suite-level metrics."""
    if not case_results:
        return SuiteResult(
            total_cases=0,
            cases_with_final_answer=0,
            cases_terminated_early=0,
            root_cause_accuracy=0.0,
            action_accuracy=0.0,
            e2e_accuracy=0.0,
            avg_turns=0.0,
            avg_info_hit_rate=0.0,
            avg_info_requests_per_case=0.0,
            stop_reason_counts={},
            case_results=[],
        )

    n = len(case_results)

    root_cause_hits = sum(1 for r in case_results if r.root_cause_hit)
    action_hits = sum(1 for r in case_results if r.action_hit)
    e2e_hits = sum(1 for r in case_results if r.e2e_success)

    cases_with_final_answer = sum(
        1 for r in case_results if r.stop_reason == "final_answer"
    )
    cases_terminated_early = n - cases_with_final_answer

    avg_turns = sum(r.total_turns for r in case_results) / n
    avg_info_hit_rate = sum(r.info_request_hit_rate for r in case_results) / n
    avg_info_requests = sum(r.info_request_count for r in case_results) / n

    stop_reason_counts: dict[str, int] = {}
    for r in case_results:
        stop_reason_counts[r.stop_reason] = stop_reason_counts.get(r.stop_reason, 0) + 1

    return SuiteResult(
        total_cases=n,
        cases_with_final_answer=cases_with_final_answer,
        cases_terminated_early=cases_terminated_early,
        root_cause_accuracy=root_cause_hits / n,
        action_accuracy=action_hits / n,
        e2e_accuracy=e2e_hits / n,
        avg_turns=avg_turns,
        avg_info_hit_rate=avg_info_hit_rate,
        avg_info_requests_per_case=avg_info_requests,
        stop_reason_counts=stop_reason_counts,
        case_results=case_results,
    )


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _print_suite_report(suite: SuiteResult) -> None:
    """Print a human-readable evaluation report."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        _print_rich_report(suite)
    except ImportError:
        _print_plain_report(suite)


def _print_rich_report(suite: SuiteResult) -> None:
    """Rich-formatted evaluation report."""
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()

    # Suite summary
    console.print("\n[bold cyan]╔══════════════════════════════════════╗[/]")
    console.print("[bold cyan]║   Benchmark Suite Evaluation Report  ║[/]")
    console.print("[bold cyan]╚══════════════════════════════════════╝[/]\n")

    # Aggregate metrics
    summary = Table(title="Suite Metrics", box=box.ROUNDED, show_header=True)
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")

    summary.add_row("Total Cases", str(suite.total_cases))
    summary.add_row("Cases → Final Answer", str(suite.cases_with_final_answer))
    summary.add_row("Cases → Early Stop", str(suite.cases_terminated_early))
    summary.add_row("", "")
    rc_color = "green" if suite.root_cause_accuracy >= 0.7 else "yellow" if suite.root_cause_accuracy >= 0.4 else "red"
    ac_color = "green" if suite.action_accuracy >= 0.7 else "yellow" if suite.action_accuracy >= 0.4 else "red"
    e2e_color = "green" if suite.e2e_accuracy >= 0.7 else "yellow" if suite.e2e_accuracy >= 0.4 else "red"
    summary.add_row("Root Cause Accuracy", f"[{rc_color}]{suite.root_cause_accuracy:.1%}[/]")
    summary.add_row("Action Accuracy", f"[{ac_color}]{suite.action_accuracy:.1%}[/]")
    summary.add_row("E2E Accuracy", f"[{e2e_color}]{suite.e2e_accuracy:.1%}[/]")
    summary.add_row("", "")
    summary.add_row("Avg Turns", f"{suite.avg_turns:.1f}")
    summary.add_row("Avg Info Request Hit Rate", f"{suite.avg_info_hit_rate:.1%}")
    summary.add_row("Avg Info Requests / Case", f"{suite.avg_info_requests_per_case:.1f}")

    console.print(summary)

    # Stop reason breakdown
    if suite.stop_reason_counts:
        console.print("\n[bold]Stop Reason Breakdown:[/]")
        for reason, count in sorted(suite.stop_reason_counts.items()):
            pct = count / suite.total_cases * 100
            console.print(f"  {reason:25s}: {count:3d} ({pct:.0f}%)")

    # Per-case detail
    if suite.case_results:
        console.print("")
        detail = Table(title="Per-Case Results", box=box.SIMPLE_HEAVY, show_header=True)
        detail.add_column("Case ID", style="dim")
        detail.add_column("Root Cause", justify="center")
        detail.add_column("Action", justify="center")
        detail.add_column("E2E", justify="center")
        detail.add_column("Turns", justify="right")
        detail.add_column("Info Hit%", justify="right")
        detail.add_column("Stop Reason", style="dim")

        for r in suite.case_results:
            rc_icon = "✓" if r.root_cause_hit else "✗"
            ac_icon = "✓" if r.action_hit else "✗"
            e2e_icon = "✓" if r.e2e_success else "✗"
            rc_style = "green" if r.root_cause_hit else "red"
            ac_style = "green" if r.action_hit else "red"
            e2e_style = "green" if r.e2e_success else "red"

            detail.add_row(
                r.case_id,
                f"[{rc_style}]{rc_icon}[/]",
                f"[{ac_style}]{ac_icon}[/]",
                f"[{e2e_style}]{e2e_icon}[/]",
                str(r.total_turns),
                f"{r.info_request_hit_rate:.0%}",
                r.stop_reason,
            )

        console.print(detail)
    console.print("")


def _print_plain_report(suite: SuiteResult) -> None:
    """Plain text fallback report (no rich dependency)."""
    sep = "=" * 55
    print(f"\n{sep}")
    print("  Benchmark Suite Evaluation Report")
    print(sep)
    print(f"  Total Cases          : {suite.total_cases}")
    print(f"  → Final Answer       : {suite.cases_with_final_answer}")
    print(f"  → Early Stop         : {suite.cases_terminated_early}")
    print(f"  Root Cause Accuracy  : {suite.root_cause_accuracy:.1%}")
    print(f"  Action Accuracy      : {suite.action_accuracy:.1%}")
    print(f"  E2E Accuracy         : {suite.e2e_accuracy:.1%}")
    print(f"  Avg Turns            : {suite.avg_turns:.1f}")
    print(f"  Avg Info Hit Rate    : {suite.avg_info_hit_rate:.1%}")
    print(f"  Avg Info Req/Case    : {suite.avg_info_requests_per_case:.1f}")
    print(f"\n  Stop Reason Breakdown:")
    for reason, count in sorted(suite.stop_reason_counts.items()):
        pct = count / suite.total_cases * 100
        print(f"    {reason:25s}: {count:3d} ({pct:.0f}%)")
    print(f"\n  {'Case ID':<25} RC  Action E2E Turns InfoHit% StopReason")
    print(f"  {'-'*70}")
    for r in suite.case_results:
        print(
            f"  {r.case_id:<25} "
            f"{'✓' if r.root_cause_hit else '✗'}   "
            f"{'✓' if r.action_hit else '✗'}      "
            f"{'✓' if r.e2e_success else '✗'}   "
            f"{r.total_turns:3d}   "
            f"{r.info_request_hit_rate:6.0%}   "
            f"{r.stop_reason}"
        )
    print(f"\n{sep}\n")

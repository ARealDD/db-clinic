"""
cli.py — db-clinic evaluation CLI entry point.

Usage:
  python -m eval.engine --cases tests/cases/
  python -m eval.engine --case case_ops_023.yaml
  python -m eval.engine --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from tests.engine.schema import BenchmarkCase
from tests.engine.runner import BenchmarkRunner, CaseRunResult, EvalConfig
from tests.engine.metrics import compute_suite_metrics
from tests.engine.logger import InteractionLogger, _sanitize_filename

# ---------------------------------------------------------------------------
# Default paths (relative to the tests/ directory)
# ---------------------------------------------------------------------------

_EVAL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES_DIR = str(_EVAL_DIR / "cases")
DEFAULT_OUTPUT_DIR = str(_EVAL_DIR / "results")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tests.engine",
        description="db-clinic evaluation — multi-turn diagnostic agent benchmark.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--cases",
        default=DEFAULT_CASES_DIR,
        help=f"YAML case file or directory. Default: {DEFAULT_CASES_DIR}",
    )
    p.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Results output directory. Default: {DEFAULT_OUTPUT_DIR}",
    )
    p.add_argument(
        "--list-cases",
        action="store_true",
        help="List all available cases and exit (no evaluation).",
    )
    p.add_argument(
        "--rerun-errors",
        metavar="RUN_DIR",
        default=None,
        help="Re-run cases with stop_reason=error from a previous run directory.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="SemanticMatcher self-consistency test only (no LLM calls).",
    )
    # EvalConfig overrides
    p.add_argument("--max-turns", type=int, default=20)
    p.add_argument("--max-misses", type=int, default=5)
    p.add_argument("--artifact-threshold", type=float, default=0.05)
    p.add_argument(
        "--rc-threshold", type=float, default=11.0,
        help="Root cause match threshold (SemanticMatcher fallback only).",
    )
    p.add_argument(
        "--action-threshold", type=float, default=8.0,
        help="Action match threshold (SemanticMatcher fallback only).",
    )
    p.add_argument("--min-action-hits", type=int, default=1)
    p.add_argument(
        "--require-primary-action",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    p.add_argument(
        "--strict-claimed-ids",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    p.add_argument("--consistency-bypass-ratio", type=float, default=1.5)
    p.add_argument(
        "--direct",
        action="store_true",
        default=False,
        help="Use direct Rust agent-eval binary instead of gRPC path.",
    )
    return p


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def run_dry_run(cases: list[BenchmarkCase], config: EvalConfig) -> None:
    from tests.engine.matcher import SemanticMatcher

    matcher = SemanticMatcher(threshold=config.artifact_match_threshold)
    print("\n[DRY RUN] 测试 SemanticMatcher 自一致性...\n")

    for case in cases:
        print(f"  Case: {case.case_id}")
        print(f"    Artifact 自匹配 ({len(case.information_inventory)} 个):")
        for key, artifact in case.information_inventory.items():
            query = artifact.description + " " + " ".join(artifact.retrieval_hints[:2])
            result = matcher.match_artifact(query, case.information_inventory)
            hit = result is not None and result.item_id == key
            score = result.score if result else 0.0
            status = "✓" if hit else f"✗ → {result.item_id if result else 'None'}"
            print(f"      {key:25s}: {status} (score={score:.3f})")

        print(f"    根因自匹配 ({len(case.canonical_root_causes)} 个):")
        for rc in case.canonical_root_causes:
            query = rc.summary + " " + " ".join(rc.matching_hints[:3])
            result = matcher.match_root_cause(
                query, case.canonical_root_causes,
                threshold=config.root_cause_match_threshold,
            )
            hit = result is not None and result.item_id == rc.root_cause_id
            score = result.score if result else 0.0
            status = "✓" if hit else f"✗ → {result.item_id if result else 'None'}"
            print(f"      {rc.root_cause_id:35s}: {status} (score={score:.3f})")

        print(f"    调优动作自匹配 ({len(case.acceptable_actions)} 个):")
        for action in case.acceptable_actions:
            query = action.summary + " " + " ".join(action.matching_hints[:3])
            hits = matcher.match_all_actions(
                query, case.acceptable_actions,
                threshold=config.action_match_threshold,
            )
            hit = any(m.item_id == action.action_id for m in hits)
            top_score = hits[0].score if hits else 0.0
            status = "✓" if hit else f"✗ (top={hits[0].item_id if hits else 'None'})"
            print(f"      {action.action_id:40s}: {status} (top_score={top_score:.3f})")
        print()

    print("[DRY RUN] 完成。所有项目显示 ✓ 说明 matcher 配置正常。")


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------

async def run_eval(
    agent,
    session_factory,
    eval_llm_client,
    eval_model: str | None,
    cases: list[BenchmarkCase],
    config: EvalConfig,
    output_dir: str,
) -> None:
    if eval_llm_client is not None:
        print(f"[Eval] 裁判 LLM 已启用: model={eval_model}")
    else:
        print("[Eval] 裁判 LLM 未配置，oracle 评分降级为 SemanticMatcher")

    logger = InteractionLogger(output_dir=output_dir)
    runner = BenchmarkRunner(
        agent=agent,
        config=config,
        llm_client=eval_llm_client,
        llm_model=eval_model,
        session_factory=session_factory,
    )

    print(f"\n[Eval] 开始评测：{len(cases)} 个 case")
    print(f"[Eval] 最大轮次: {config.max_turns}，根因阈值: {config.root_cause_match_threshold}")
    print(f"[Eval] 输出目录: {logger.run_dir}\n")

    case_results: list[CaseRunResult] = []
    try:
        for i, case in enumerate(cases):
            print(f"\n[{i+1}/{len(cases)}] Running case: {case.case_id}")
            result = await runner.run_case(case)
            case_results.append(result)
            logger.log_case(case, result)
            logger.log_suite_summary(compute_suite_metrics(case_results))
    finally:
        if case_results:
            suite = compute_suite_metrics(case_results)
            logger.log_suite_summary(suite)
            suite.print_report()


# ---------------------------------------------------------------------------
# Load our adapter (hardwired — we own the evaluation now)
# ---------------------------------------------------------------------------

def _load_adapter(direct: bool = False):
    import importlib

    factory_module = importlib.import_module("tests.eval_adapter")
    factory_name = "create_agent_direct" if direct else "create_agent"
    factory = getattr(factory_module, factory_name, None)
    if factory is None:
        print(
            f"ERROR: adapter function `{factory_name}` not found in eval.eval_adapter",
            file=sys.stderr,
        )
        sys.exit(1)

    result = factory()
    if isinstance(result, tuple) and len(result) == 4:
        agent, session_factory, eval_llm_client, eval_model = result
        return agent, session_factory, eval_llm_client, eval_model
    if isinstance(result, tuple) and len(result) == 2:
        agent, session_factory = result
        return agent, session_factory, None, None
    print(
        f"ERROR: create_agent() 应返回 2/4 元组，实际: {type(result)}",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    config = EvalConfig(
        max_turns=args.max_turns,
        max_consecutive_misses=args.max_misses,
        artifact_match_threshold=args.artifact_threshold,
        root_cause_match_threshold=args.rc_threshold,
        action_match_threshold=args.action_threshold,
        min_action_hits=args.min_action_hits,
        require_primary_action=args.require_primary_action,
        require_claimed_id_match=args.strict_claimed_ids,
        consistency_bypass_score_ratio=args.consistency_bypass_ratio,
    )

    # Load cases
    cases_path = Path(args.cases)
    if cases_path.is_file():
        try:
            cases = [BenchmarkCase.load_yaml(str(cases_path))]
        except Exception as e:
            print(f"ERROR: 加载 case 文件失败 {cases_path}: {e}", file=sys.stderr)
            return 1
    elif cases_path.is_dir():
        cases = BenchmarkCase.load_dir(str(cases_path))
        if not cases:
            print(f"ERROR: 目录中没有找到 YAML case: {cases_path}", file=sys.stderr)
            return 1
    else:
        print(f"ERROR: cases 路径不存在: {cases_path}", file=sys.stderr)
        return 1

    # --list-cases
    if args.list_cases:
        print(f"\n可用 case（共 {len(cases)} 个）：")
        for case in cases:
            print(f"  {case.case_id}")
            print(f"    {case.user_report[:60]}...")
            print(f"    artifacts={len(case.information_inventory)}, "
                  f"root_causes={len(case.canonical_root_causes)}, "
                  f"actions={len(case.acceptable_actions)}")
        return 0

    # --dry-run (no agent needed)
    if args.dry_run:
        run_dry_run(cases, config)
        return 0

    # Full evaluation — load our adapter
    agent, session_factory, eval_llm_client, eval_model = _load_adapter(direct=args.direct)

    asyncio.run(run_eval(
        agent=agent,
        session_factory=session_factory,
        eval_llm_client=eval_llm_client,
        eval_model=eval_model,
        cases=cases,
        config=config,
        output_dir=args.output,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())

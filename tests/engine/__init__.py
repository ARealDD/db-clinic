"""
tests/engine — db-clinic evaluation engine (extracted from dba-bench).

Core modules:
  schema    — BenchmarkCase, Artifact, OracleRootCause, OracleAction
  runner    — BenchmarkRunner (multi-turn interactive eval loop)
  matcher   — SemanticMatcher (keyword-weighted scoring)
  intent    — IntentRecognizer, FinalAnswerExtractor
  metrics   — compute_suite_metrics, SuiteResult
  logger    — InteractionLogger (JSON/CSV output)
  protocol  — AgentProtocol interface
  cli       — CLI entry point
"""

from tests.engine.schema import BenchmarkCase, Artifact, OracleRootCause, OracleAction
from tests.engine.runner import BenchmarkRunner, EvalConfig, CaseRunResult, TurnLog
from tests.engine.metrics import SuiteResult, compute_suite_metrics
from tests.engine.logger import InteractionLogger
from tests.engine.protocol import AgentProtocol, SimpleSession

__version__ = "0.1.0"

__all__ = [
    "BenchmarkRunner",
    "EvalConfig",
    "CaseRunResult",
    "TurnLog",
    "BenchmarkCase",
    "Artifact",
    "OracleRootCause",
    "OracleAction",
    "SuiteResult",
    "compute_suite_metrics",
    "InteractionLogger",
    "AgentProtocol",
    "SimpleSession",
]

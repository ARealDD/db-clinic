# db-clinic Evaluation

End-to-end evaluation of the db-clinic diagnostic agent. The evaluation engine (`eval/engine/`) is self-contained — no external dba-bench dependency.

## Directory Structure

```
eval/
  engine/                 # Evaluation core (self-contained)
    __init__.py            # Public API
    __main__.py            # python -m eval.engine entry point
    cli.py                 # CLI argument parsing
    runner.py              # BenchmarkRunner (multi-turn eval loop)
    schema.py              # BenchmarkCase, Artifact, Oracle*
    matcher.py             # SemanticMatcher (keyword-weighted scoring)
    intent.py              # IntentRecognizer, FinalAnswerExtractor
    metrics.py             # compute_suite_metrics, SuiteResult
    logger.py              # InteractionLogger (JSON/CSV output)
    protocol.py            # AgentProtocol interface
  llm/                    # LLM API configuration
    config.yaml            # Template (git-tracked)
    config.local.yaml      # Your API keys (git-ignored, copy from config.yaml)
    config.py              # Config loader (file + env vars)
    judge_client.py        # LLM judge client for oracle scoring
  eval_adapter.py          # gRPC agent adapter (AgentProtocol impl)
  run_eval.sh              # Build server + run eval in one command
  requirements.txt         # Python dependencies
  cases -> ../dba-bench/cases/  # Symlink to test cases
  results/                 # Evaluation output (git-ignored)
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r eval/requirements.txt
```

### 2. Configure LLM API

```bash
cp eval/llm/config.yaml eval/llm/config.local.yaml
# Edit eval/llm/config.local.yaml with your API key and model
```

Supported providers: `anthropic`, `openai`, `custom`, `xai`, `dashscope`.

The config is shared by both the agent (gRPC server) and the judge (oracle scoring). Env vars like `DB_CLINIC_API_KEY` take priority over file config.

### 3. Run evaluation

```bash
# Full suite
eval/run_eval.sh

# Single case
eval/run_eval.sh --case case_ops_023.yaml

# Dry run (matcher only, no LLM calls)
eval/run_eval.sh --dry-run

# Custom cases directory
eval/run_eval.sh --cases /path/to/cases
```

### 4. Or run manually

```bash
# Start the gRPC server
source ~/.cargo/env
cargo run -p agent-grpc-server -- --addr 0.0.0.0:50051 --skills-dir skills

# In another terminal, run the eval engine
python -m eval.engine --cases eval/cases --output eval/results
```

## Test Cases

Test cases are symlinked from the dba-bench repository (`../doer_experiment/dba-bench/cases/`). Each case is a YAML file defining:

- **User report** — the initial problem description
- **Information inventory** — available evidence/artifacts
- **Oracle** — expected root causes and corrective actions

## Results

Results are written to `eval/results/run_<timestamp>/`:

- `<case_id>.json` — per-case detailed results
- `summary.json` — aggregate metrics
- `results.csv` — tabular export

## Scoring

- **Without judge LLM**: Falls back to `SemanticMatcher` (keyword overlap)
- **With judge LLM**: Uses the same API config for LLM-as-Judge scoring against oracle labels
- The judge LLM is enabled automatically when you provide an API key in config

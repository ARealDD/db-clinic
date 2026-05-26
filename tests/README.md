# db-clinic Evaluation

End-to-end evaluation of the db-clinic diagnostic agent. The evaluation engine (`tests/engine/`) is self-contained — no external dba-bench dependency.

## Directory Structure

```
tests/
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
  eval_adapter.py          # Agent adapters (gRPC + Direct Rust)
  run_eval.sh              # Build + run eval in one command
  eval.sh                  # Lightweight wrapper (run only)
  requirements.txt         # Python dependencies
  cases -> ../dba-bench/cases/  # Symlink to test cases
  results/                 # Evaluation output (git-ignored)
```

## Setup (both approaches)

### 1. Install Python dependencies

```bash
pip install -r tests/requirements.txt
```

### 2. Configure LLM API

```bash
cp tests/llm/config.yaml tests/llm/config.local.yaml
# Edit tests/llm/config.local.yaml with your API key and model
```

Supported providers: `anthropic`, `openai`, `custom`, `xai`, `dashscope`.

The config is shared by the agent, the judge (oracle scoring), and both approaches below. Env vars like `DB_CLINIC_API_KEY` take priority over file config.

### 3. Ensure Rust toolchain

Source the Rust environment if needed:

```bash
source ~/.cargo/env
```

## Approach 1: gRPC (full tool execution)

This starts a Rust gRPC server, then runs the evaluation client against it. Tools are executed for real, skills are matched, and operator approval is supported.

### Build the gRPC server

The Rust workspace is in `rust/` — build from that directory:

```bash
cd rust
cargo build -p agent-grpc-server
cd ..
```

### Run

#### One-command (build + start server + run eval)

```bash
tests/run_eval.sh
```

#### Manual (two terminals)

Terminal 1 — start the gRPC server:

```bash
cd rust
cargo run -p agent-grpc-server -- --addr 0.0.0.0:50051 --skills-dir ../skills
```

Terminal 2 — run the evaluation:

```bash
# From repo root
python -m eval.engine --cases tests/cases --output tests/results

# Or from any directory
tests/eval.sh --cases tests/cases --output tests/results
```

## Approach 2: Direct Rust Agent (no gRPC)

This bypasses the gRPC server and invokes the Rust agent directly via a lightweight stdin/stdout JSON-lines binary. Simpler and faster for evaluation, but does not execute real tools or match skills — the agent produces text-only responses.

### Architecture

```
Python eval engine     agent-eval (Rust binary)
     stdin ──────────>  runtime::ConversationRuntime ──> LLM API
     stdout <──────────       (no gRPC, no protobuf)
```

### Build the agent-eval binary

The Rust workspace is in `rust/` — build from that directory:

```bash
cd rust
cargo build -p agent-eval
cd ..
```

The Python adapter auto-discovers the binary at `rust/target/debug/agent-eval`.

### Run

#### One-command (build + run eval)

```bash
tests/run_eval.sh --direct
```

#### Manual

```bash
# From repo root
python -m eval.engine --direct --cases tests/cases --output tests/results

# From any directory
tests/eval.sh --direct --cases tests/cases --output tests/results
```

### What's different from the gRPC path

| Aspect | gRPC | Direct |
|--------|------|--------|
| Communication | Protobuf / tonic streaming | JSON lines on stdin/stdout |
| Tools | Real tool execution (proxied to operator) | Stubbed (eval mode) |
| Skills | SkillEngine matched server-side | Not loaded |
| Permissions | Operator approval (proxy) | Auto-approve |
| Session management | Actor-based thread | In-process HashMap |

## Options

```bash
# Single case
tests/run_eval.sh --case case_ops_023.yaml
tests/run_eval.sh --direct --case case_ops_023.yaml

# Custom cases directory
tests/run_eval.sh --cases /path/to/cases

# Dry run (matcher only, no LLM calls)
tests/run_eval.sh --dry-run
```

## Test Cases

Each case is a YAML file defining:

- **User report** — the initial problem description
- **Information inventory** — available evidence/artifacts
- **Oracle** — expected root causes and corrective actions

## Results

Results are written to `tests/results/run_<timestamp>/`:

- `<case_id>.json` — per-case detailed results
- `summary.json` — aggregate metrics
- `results.csv` — tabular export

## Scoring

- **Without judge LLM**: Falls back to `SemanticMatcher` (keyword overlap)
- **With judge LLM**: Uses the same API config for LLM-as-Judge scoring against oracle labels
- The judge LLM is enabled automatically when you provide an API key in config

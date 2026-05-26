"""
eval_adapter.py — db-clinic agent adapter for dba-bench BenchmarkRunner.

Usage:
    python -m dba_bench --agent eval.eval_adapter:create_agent --cases ../dba-bench/cases/

The adapter bridges db-clinic's gRPC Chat protocol to dba-bench's AgentProtocol
async generator interface. Each db-clinic conversation turn is a complete
gRPC Chat stream (send user message, collect all response chunks).

dba-bench multi-turn flow:
  - agent.chat(session, user_report) → response chunks until 'done'
  - If response contains proxy_command or text patterns indicate info request,
    dba-bench matches an artifact and calls agent.chat(session, artifact_content)
  - Final answer is scored against oracle

Mock mode (default): Run the gRPC server with --mock to use canned LLM responses.
Set DB_CLINIC_MODEL and DB_CLINIC_API_KEY to use a real LLM provider.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any, AsyncIterator

import grpc

# Ensure the generated proto stubs are importable.
_GENERATED_DIR = os.path.join(os.path.dirname(__file__), "..", "python", "generated")
if _GENERATED_DIR not in sys.path:
    sys.path.insert(0, _GENERATED_DIR)

import agent_pb2  # type: ignore
import agent_pb2_grpc  # type: ignore

# ---------------------------------------------------------------------------
# Configuration (priority: env var > config.local.yaml > config.yaml > default)
# ---------------------------------------------------------------------------

from .llm.config import _load_config

_cfg = _load_config()

GRPC_ADDR = os.environ.get("DB_CLINIC_GRPC_ADDR", "localhost:50051")
MODEL = _cfg.get("model", "")
API_KEY = _cfg.get("api_key", "")
PROVIDER = _cfg.get("provider", "anthropic")
BASE_URL = _cfg.get("base_url", "")


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class DbClinicSession:
    """Holds the gRPC session state for one benchmark case."""

    def __init__(self) -> None:
        self.session_id: str = ""
        self._created: bool = False

    def has_pending_proxy(self) -> bool:
        """v1: proxy interception is not yet supported."""
        return False


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class DbClinicAgent:
    """Wraps db-clinic's gRPC Chat protocol as a dba-bench AgentProtocol."""

    def __init__(self, grpc_addr: str = GRPC_ADDR) -> None:
        self._grpc_addr = grpc_addr

    async def chat(
        self, session: DbClinicSession, user_message: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one conversation turn via gRPC Chat and yield dba-bench chunks."""
        channel = grpc.insecure_channel(self._grpc_addr)
        stub = agent_pb2_grpc.AgentServiceStub(channel)

        try:
            # Create session on first call.
            if not session._created:
                api_config = None
                if API_KEY:
                    api_config = agent_pb2.ApiConfig(
                        provider=PROVIDER,
                        api_key=API_KEY,
                        base_url=BASE_URL,
                    )
                resp = stub.CreateSession(
                    agent_pb2.CreateSessionRequest(
                        model=MODEL,
                        system_prompts=["You are a database diagnosis assistant."],
                        api_config=api_config,
                    )
                )
                session.session_id = resp.session_id
                session._created = True

            # Build the Chat input stream (send one message, then wait for response).
            input_stop = threading.Event()
            input_iter = _ChatInputIterator(session.session_id, user_message, input_stop)

            # Collect response in a background thread, bridge to async.
            response_queue: queue.Queue = queue.Queue()
            error_ref: list[Exception] = []

            def _read_stream() -> None:
                try:
                    for out in stub.Chat(iter(input_iter)):
                        response_queue.put(out)
                    response_queue.put(None)  # sentinel: stream ended
                except Exception as exc:
                    error_ref.append(exc)
                    response_queue.put(None)

            reader_thread = threading.Thread(target=_read_stream, daemon=True)
            reader_thread.start()

            # Yield chunks as they arrive.
            text_parts: list[str] = []
            saw_proxy_command = False
            # Buffered TurnComplete data (mock mode bundles text here, not in text_delta).
            turn_complete_out: agent_pb2.ChatOutput | None = None

            while True:
                out = await asyncio.to_thread(response_queue.get)
                if out is None:
                    break

                field = out.WhichOneof("payload")
                if field == "text_delta":
                    text = out.text_delta.content
                    text_parts.append(text)
                    yield {"type": "text_delta", "text": text}

                elif field == "thinking_delta":
                    # Accumulate but don't yield (dba-bench doesn't use thinking).
                    pass

                elif field == "tool_execution":
                    te = out.tool_execution
                    # Yield a proxy_command chunk so dba-bench can match artifacts.
                    # The tool's input_json contains what the LLM wanted to execute.
                    saw_proxy_command = True
                    yield {
                        "type": "proxy_command",
                        "tool_name": te.tool_name,
                        "command": te.input_json,
                        "instructions": te.output,
                    }

                elif field == "skill_match":
                    # dba-bench doesn't consume skill_match; log for debugging.
                    names = [s.skill_name for s in out.skill_match.skills]
                    if names:
                        print(f"  [db-clinic] Skills matched: {', '.join(names)}")

                elif field == "turn_complete":
                    turn_complete_out = out  # Process below after stream ends

                elif field == "error":
                    yield {
                        "type": "error",
                        "message": out.error.message,
                    }

            reader_thread.join(timeout=5)
            input_stop.set()  # Release the input stream iterator.

            # In mock mode, text arrives via TurnComplete messages, not text_delta events.
            # Extract any text from TurnComplete messages and yield it now.
            if turn_complete_out is not None:
                for msg in turn_complete_out.turn_complete.messages:
                    if msg.role == agent_pb2.MESSAGE_ROLE_ASSISTANT:
                        for block in msg.blocks:
                            field = block.WhichOneof("block")
                            if field == "text" and block.text:
                                text = block.text.text
                                text_parts.append(text)
                                yield {"type": "text_delta", "text": text}

            if error_ref:
                yield {
                    "type": "error",
                    "message": str(error_ref[0]),
                }

            # Signal turn completion.
            yield {
                "type": "done",
                "stop_reason": "end_turn",
            }

        finally:
            channel.close()


# ---------------------------------------------------------------------------
# ChatInput iterator (feeds gRPC input stream from a queue)
# ---------------------------------------------------------------------------


class _ChatInputIterator:
    """Thread-safe iterator that feeds ChatInput messages into the gRPC stream.

    Blocks until a stop event is signaled to prevent premature stream closure
    (gRPC needs the input stream to remain open while the response is processed).
    """

    def __init__(self, session_id: str, user_message: str, stop_event: threading.Event) -> None:
        self._q: queue.Queue = queue.Queue()
        self._stop_event = stop_event
        # Push the initial message.
        chat_input = agent_pb2.ChatInput(session_id=session_id)
        chat_input.user_message.CopyFrom(agent_pb2.UserMessage(content=user_message))
        self._q.put(chat_input)
        # Don't close immediately — wait for the stop event so the server
        # has time to process the turn and stream responses back.
        self._q.put(("_WAIT", stop_event))

    def __iter__(self):
        return self

    def __next__(self):
        item = self._q.get()
        if item is None:
            raise StopIteration
        if isinstance(item, tuple) and item[0] == "_WAIT":
            # Block until the response stream finishes or 120s timeout.
            if not item[1].wait(timeout=120):
                raise StopIteration
            raise StopIteration
        return item


# ---------------------------------------------------------------------------
# Factory function (entry point for dba-bench)
# ---------------------------------------------------------------------------


def create_agent(skill_mode: str = "full"):
    """dba-bench factory. Returns (agent, session_factory, judge_client, judge_model) 4-tuple.

    skill_mode is accepted for CLI compatibility but not used by db-clinic's
    adapter. Skill matching is handled server-side by the gRPC SkillEngine.
    """
    _ = skill_mode
    from .llm.judge_client import JudgeLLMClient

    agent = DbClinicAgent(grpc_addr=GRPC_ADDR)
    judge = JudgeLLMClient()
    return agent, DbClinicSession, judge, judge.model or "default"


def _default_eval_binary_path() -> str:
    """Resolve the agent-eval binary path, preferring cargo build output."""
    # Check relative to this file: eval/ → rust/target/debug/agent-eval
    eval_dir = Path(__file__).resolve().parent
    for candidate in (
        eval_dir / ".." / "rust" / "target" / "debug" / "agent-eval",
        eval_dir / ".." / "rust" / "target" / "release" / "agent-eval",
    ):
        resolved = candidate.resolve()
        if resolved.exists():
            return str(resolved)
    return "agent-eval"  # fall back to PATH


# ---------------------------------------------------------------------------
# Direct Rust agent (bypass gRPC, invoke agent-eval binary via subprocess)
# ---------------------------------------------------------------------------


class DirectEvalSession:
    """Holds the agent-eval session state for one benchmark case."""

    def __init__(self) -> None:
        self.session_id: str = ""

    def has_pending_proxy(self) -> bool:
        return False


class DirectEvalAgent:
    """AgentProtocol implementation that spawns agent-eval as a subprocess.

    Communicates via JSON lines on stdin/stdout — no gRPC, no protobuf.
    """

    def __init__(self, binary_path: str | None = None) -> None:
        if binary_path:
            self._binary_path = binary_path
        else:
            self._binary_path = os.environ.get(
                "DB_CLINIC_EVAL_BINARY", _default_eval_binary_path()
            )
        self._process: asyncio.subprocess.Process | None = None

    async def _ensure_process(self) -> None:
        if self._process is not None:
            return
        _ensure_env_vars()
        self._process = await asyncio.create_subprocess_exec(
            self._binary_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,  # inherit stderr so errors are visible
        )

    async def chat(
        self, session: DirectEvalSession, user_message: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one conversation turn via the agent-eval binary."""
        await self._ensure_process()
        assert self._process is not None and self._process.stdin is not None
        assert self._process.stdout is not None

        # Send JSON request.
        req = {"message": user_message, "session_id": session.session_id or None}
        self._process.stdin.write((json.dumps(req) + "\n").encode())
        await self._process.stdin.drain()

        # Read JSON response.
        line = await self._process.stdout.readline()
        raw = line.decode().strip()
        if not raw:
            yield {"type": "error", "message": "empty response from agent-eval"}
            return

        try:
            resp = json.loads(raw)
        except json.JSONDecodeError as exc:
            yield {"type": "error", "message": f"invalid JSON from agent-eval: {exc}"}
            return

        if resp.get("error"):
            yield {"type": "error", "message": resp["error"]}
            return

        # Store session_id on first turn.
        if not session.session_id:
            session.session_id = resp["session_id"]

        text = resp.get("text") or ""
        if text:
            yield {"type": "text_delta", "text": text}

        yield {"type": "done", "stop_reason": resp.get("stop_reason", "end_turn")}

    async def close(self) -> None:
        if self._process is not None and self._process.stdin is not None:
            self._process.stdin.close()
            await self._process.wait()
            self._process = None


def _ensure_env_vars() -> None:
    """Set provider env vars from config so the agent-eval subprocess can find them."""
    cfg = _load_config()

    model = cfg.get("model", "")
    if model:
        os.environ.setdefault("DB_CLINIC_MODEL", model)

    provider = cfg.get("provider", "anthropic")
    api_key = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "")

    if provider == "anthropic":
        if api_key:
            os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
        if base_url:
            os.environ.setdefault("ANTHROPIC_BASE_URL", base_url)
    elif provider == "deepseek":
        # DeepSeek speaks the Anthropic wire format.
        if api_key:
            os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
        if base_url:
            os.environ.setdefault("ANTHROPIC_BASE_URL", base_url)
    elif provider == "openai":
        if api_key:
            os.environ.setdefault("OPENAI_API_KEY", api_key)
        if base_url:
            os.environ.setdefault("OPENAI_BASE_URL", base_url)
    elif provider == "xai":
        if api_key:
            os.environ.setdefault("XAI_API_KEY", api_key)
        if base_url:
            os.environ.setdefault("XAI_BASE_URL", base_url)


def create_agent_direct(skill_mode: str = "full") -> tuple:
    """Factory for the direct Rust agent path (no gRPC).

    Returns (agent, session_factory, judge_client, judge_model) 4-tuple.
    """
    _ = skill_mode
    from .llm.judge_client import JudgeLLMClient

    agent = DirectEvalAgent()
    judge = JudgeLLMClient()
    return agent, DirectEvalSession, judge, judge.model or "default"

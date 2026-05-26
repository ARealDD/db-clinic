"""
protocol.py — dba-bench 的核心接口定义。

任何 agent 只需实现 AgentProtocol 即可接入 BenchmarkRunner。
SimpleSession 是内置的最小 session 实现，适用于独立运行和单元测试。

接入示例（以 D-bot 为例）：

    # d_bot/eval_adapter.py
    from tests.engine.protocol import SimpleSession

    def create_agent():
        agent = DBotAgent(...)   # 实现 AgentProtocol.chat() 即可
        return agent, SimpleSession

    # 运行评测
    python -m dba_bench --agent d_bot.eval_adapter:create_agent --cases ./cases/
"""
from __future__ import annotations

import uuid
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class AgentProtocol(Protocol):
    """
    任何 agent 只需实现此接口即可接入 BenchmarkRunner。

    chat() 为异步生成器，逐块 yield 以下类型的 dict：

      {"type": "text_delta",    "text": str}
      {"type": "proxy_command", "tool_name": str, "command": str, "instructions": str}
      {"type": "done",          "stop_reason": str}
      {"type": "error",         "message": str}

    runner 对 session 的类型没有要求，只要 agent 自己能处理即可。
    """

    def chat(self, session: Any, user_message: str) -> AsyncIterator[dict]: ...


class SimpleSession:
    """
    内置最小 session 实现，供独立运行和单元测试使用。

    真实项目（sql_agent、D-bot 等）通常会传入自己更完整的 SessionState，
    只要那个 SessionState 实现了 agent 内部所需的方法（如 has_pending_proxy()）即可，
    runner 本身对 session 类型没有约束，它只负责创建 session 并传给 agent。
    """

    def __init__(self) -> None:
        self.session_id: str = str(uuid.uuid4())
        self.messages: list[dict] = []
        self.pending_proxy: Any = None

    def has_pending_proxy(self) -> bool:
        return self.pending_proxy is not None

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str | list) -> None:
        self.messages.append({"role": "assistant", "content": content})

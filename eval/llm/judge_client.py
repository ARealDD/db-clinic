"""
llm/judge_client.py — LLM judge client for dba-bench oracle scoring.

Reads the same config files as the adapter and provides an async
stream_chat interface that dba-bench's BenchmarkRunner expects.

Supports both Anthropic Messages API and OpenAI Chat Completions streaming.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from .config import _load_config


class JudgeLLMClient:
    """Async LLM client for dba-bench's judge LLM interface.

    dba-bench expects:
      - connect() -> None (optional)
      - stream_chat(model, system, messages, tools, max_tokens) -> AsyncIterator[dict]
        yielding {"type": "text_delta", "text": "..."}
    """

    def __init__(self) -> None:
        cfg = _load_config()
        self._provider = cfg.get("provider", "anthropic")
        self._api_key = cfg.get("api_key", "")
        self._base_url = cfg.get("base_url", "").rstrip("/")
        self._model = cfg.get("model", "")

    @property
    def model(self) -> str:
        return self._model

    def connect(self) -> None:
        pass

    async def stream_chat(
        self,
        model: str = "",
        system: str = "",
        messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 512,
    ) -> AsyncIterator[dict[str, Any]]:
        _ = tools
        model = model or self._model
        messages = messages or []

        if self._provider == "anthropic":
            async for chunk in self._stream_anthropic(model, system, messages, max_tokens):
                yield chunk
        else:
            async for chunk in self._stream_openai(model, system, messages, max_tokens):
                yield chunk

    # ------------------------------------------------------------------
    # Anthropic Messages API (streaming)
    # ------------------------------------------------------------------

    async def _stream_anthropic(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "stream": True,
            "thinking": {"type": "disabled"},
        }
        if system:
            body["system"] = system

        async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/messages",
                headers=headers,
                json=body,
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield {
                                    "type": "text_delta",
                                    "text": delta.get("text", ""),
                                }

    # ------------------------------------------------------------------
    # OpenAI-compatible Chat Completions API (streaming)
    # ------------------------------------------------------------------

    async def _stream_openai(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        openai_messages: list[dict[str, Any]] = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        openai_messages.extend(messages)

        body = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=body,
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        event = json.loads(data_str)
                        choices = event.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield {
                                    "type": "text_delta",
                                    "text": content,
                                }

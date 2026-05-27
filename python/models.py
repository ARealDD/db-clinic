# -*- coding: utf-8 -*-
"""Pydantic data models for the FastAPI gateway — Python 3.9 compatible."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)


class UserLogin(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    username: str


class LLMConfig(BaseModel):
    provider: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    # Three-segment system prompt. The Rust kernel receives these as
    # `repeated string system_prompts` (in this order: role, background,
    # rules) and then appends its own TOOL_USAGE_GUIDANCE. Empty segments
    # are dropped before transmission so the operator sees no spurious
    # `\n\n` separators in the JSONL prompt log.
    system_prompt_role: str = "You are a database diagnosis assistant."
    system_prompt_background: str = ""
    system_prompt_rules: str = ""

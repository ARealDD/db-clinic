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
    password: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    username: str
    role: str = "user"


class LLMConfig(BaseModel):
    provider: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    system_prompt: str = "You are a database diagnosis assistant."
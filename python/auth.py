# -*- coding: utf-8 -*-
"""JWT authentication and password hashing — Python 3.9 compatible."""
from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt

log = logging.getLogger("gateway.auth")
SECRET_KEY = os.environ.get(
    "JWT_SECRET_KEY",
    "db-clinic-dev-secret-change-in-production",
)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.environ.get("JWT_EXPIRE_MINUTES", "60")
)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token. Returns payload dict or None on failure."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        log.warning("JWT decode error: %s", e)
        return None
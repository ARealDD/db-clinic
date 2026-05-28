# -*- coding: utf-8 -*-
"""SQLite metadatabase operations — Python 3.9 compatible."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional, Dict, Any, List

import aiosqlite

from paths import DB_PATH as _DB_PATH

log = logging.getLogger("gateway.db")

# Re-exported as a string so existing call sites (and tests that patch
# `db.DB_PATH`) keep working unchanged. paths.DB_PATH already honours the
# META_DB_PATH env var, so no additional env wiring is needed here.
DB_PATH = str(_DB_PATH)

# SQL for creating tables
_CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    username  TEXT    NOT NULL UNIQUE,
    hashed_pw TEXT    NOT NULL,
    role      TEXT    NOT NULL DEFAULT 'user',
    created_at REAL   NOT NULL DEFAULT (strftime('%s','now'))
);
"""

_CREATE_USER_CONFIGS_TABLE = """
CREATE TABLE IF NOT EXISTS user_configs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    key     TEXT    NOT NULL,
    value   TEXT    NOT NULL,
    UNIQUE(user_id, key),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

_CREATE_USER_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS user_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    session_id TEXT    NOT NULL UNIQUE,
    created_at REAL   NOT NULL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

_CREATE_SKILLS_TABLE = """
CREATE TABLE IF NOT EXISTS skills (
    id               TEXT    PRIMARY KEY,
    name             TEXT    NOT NULL,
    description      TEXT    NOT NULL DEFAULT '',
    content          TEXT    NOT NULL DEFAULT '',
    metadata         TEXT    NOT NULL DEFAULT '{}',
    owner_id         INTEGER NOT NULL,
    is_official      INTEGER NOT NULL DEFAULT 0,
    is_published     INTEGER NOT NULL DEFAULT 0,
    source_skill_id  TEXT,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (source_skill_id) REFERENCES skills(id) ON DELETE SET NULL
);
"""

_CREATE_USER_ACTIVE_SKILLS_TABLE = """
CREATE TABLE IF NOT EXISTS user_active_skills (
    user_id  INTEGER NOT NULL,
    skill_id TEXT    NOT NULL,
    PRIMARY KEY (user_id, skill_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
);
"""


# Shared aiosqlite connection. aiosqlite serializes all operations through a
# single internal worker thread per connection, so a process-wide connection is
# safe and avoids opening+closing a connection (≈2 ms each) for every request.
# At 20 QPS that's 40 ms/sec of pure-overhead CPU; here it costs us nothing.
# WAL mode (set in init_db) lets readers and one writer coexist on the file.
_SHARED_DB: Optional[aiosqlite.Connection] = None
_SHARED_DB_LOCK: Optional[asyncio.Lock] = None


async def init_db() -> None:
    """Create the database file and tables if they do not exist yet.

    Called once at application startup; subsequent calls are safe but
    redundant (everything uses CREATE TABLE IF NOT EXISTS).
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(
        _CREATE_USERS_TABLE
        + _CREATE_USER_CONFIGS_TABLE
        + _CREATE_USER_SESSIONS_TABLE
        + _CREATE_SKILLS_TABLE
        + _CREATE_USER_ACTIVE_SKILLS_TABLE
    )
    # Migration: add role column if missing (existing databases)
    try:
        await db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        log.info("migration: added role column to users table")
    except Exception:
        pass  # column already exists
    await db.commit()
    await db.close()
    log.info("metadatabase initialised at %s", DB_PATH)


async def _get_db() -> aiosqlite.Connection:
    """Return the process-wide aiosqlite connection (lazy-initialised).

    Callers MUST NOT call `.close()` on the returned object — it's shared.
    """
    global _SHARED_DB, _SHARED_DB_LOCK
    if _SHARED_DB is not None:
        return _SHARED_DB
    if _SHARED_DB_LOCK is None:
        _SHARED_DB_LOCK = asyncio.Lock()
    async with _SHARED_DB_LOCK:
        if _SHARED_DB is None:
            conn = await aiosqlite.connect(DB_PATH)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            _SHARED_DB = conn
    return _SHARED_DB


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------

async def create_user(username: str, hashed_pw: str) -> Optional[int]:
    """Insert a new user. Returns user id on success, None if username exists."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO users (username, hashed_pw) VALUES (?, ?)",
            (username, hashed_pw),
        )
        await db.commit()
        return cursor.lastrowid
    except aiosqlite.IntegrityError:
        log.warning("duplicate username: %s", username)
        return None


async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Look up a user by username. Returns dict with id, username, hashed_pw, role or None."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT id, username, hashed_pw, role FROM users WHERE username = ?",
        (username,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"id": row["id"], "username": row["username"], "hashed_pw": row["hashed_pw"], "role": row["role"]}


async def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Look up a user by id. Returns dict with id, username, hashed_pw, role or None."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT id, username, hashed_pw, role FROM users WHERE id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"id": row["id"], "username": row["username"], "hashed_pw": row["hashed_pw"], "role": row["role"]}


# ---------------------------------------------------------------------------
# User config operations (key-value per user, stored as JSON strings)
# ---------------------------------------------------------------------------

async def set_user_config(user_id: int, key: str, value: Any) -> None:
    """Upsert a config key-value pair for a user. value is auto-serialized to JSON."""
    db = await _get_db()
    json_value = json.dumps(value) if not isinstance(value, str) else value
    await db.execute(
        "INSERT INTO user_configs (user_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
        (user_id, key, json_value),
    )
    await db.commit()


async def get_user_config(user_id: int, key: str) -> Optional[Any]:
    """Get a single config value for a user. Returns None if not found."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT value FROM user_configs WHERE user_id = ? AND key = ?",
        (user_id, key),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


async def get_all_user_configs(user_id: int) -> Dict[str, Any]:
    """Get all config key-value pairs for a user as a dict."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT key, value FROM user_configs WHERE user_id = ?",
        (user_id,),
    )
    rows = await cursor.fetchall()
    result: Dict[str, Any] = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            result[row["key"]] = row["value"]
    return result


# ---------------------------------------------------------------------------
# Session ownership operations
# ---------------------------------------------------------------------------

async def register_session(user_id: int, session_id: str) -> None:
    db = await _get_db()
    try:
        await db.execute(
            "INSERT INTO user_sessions (user_id, session_id) VALUES (?, ?)",
            (user_id, session_id),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        log.warning("duplicate session_id: %s", session_id)


async def get_session_owner(session_id: str) -> Optional[int]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT user_id FROM user_sessions WHERE session_id = ?",
        (session_id,),
    )
    row = await cursor.fetchone()
    return row["user_id"] if row else None


async def list_user_sessions(user_id: int) -> List[Dict[str, Any]]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT session_id, created_at FROM user_sessions WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [{"session_id": r["session_id"], "created_at": r["created_at"], "created_at_ms": int(r["created_at"] * 1000) if r["created_at"] else None} for r in rows]


async def delete_session(session_id: str) -> bool:
    db = await _get_db()
    cursor = await db.execute(
        "DELETE FROM user_sessions WHERE session_id = ?",
        (session_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Admin operations
# ---------------------------------------------------------------------------

async def get_all_users() -> List[Dict[str, Any]]:
    """Return id, username, role, created_at for all users (no hashed_pw)."""
    db = await _get_db()
    cursor = await db.execute(
        "SELECT id, username, role, created_at FROM users ORDER BY id",
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def set_user_role(user_id: int, role: str) -> None:
    """Set the role for a user (e.g. 'admin', 'user')."""
    db = await _get_db()
    await db.execute(
        "UPDATE users SET role = ? WHERE id = ?",
        (role, user_id),
    )
    await db.commit()


async def update_user_password(user_id: int, hashed_pw: str) -> None:
    """Update password hash for a user."""
    db = await _get_db()
    await db.execute(
        "UPDATE users SET hashed_pw = ? WHERE id = ?",
        (hashed_pw, user_id),
    )
    await db.commit()


async def delete_user_cascade(user_id: int) -> bool:
    """Delete a user from metadb (user_sessions cascade via FK)."""
    db = await _get_db()
    cursor = await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()
    return cursor.rowcount > 0

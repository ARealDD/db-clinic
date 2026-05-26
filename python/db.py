# -*- coding: utf-8 -*-
"""SQLite metadatabase operations — Python 3.9 compatible."""
from __future__ import annotations

import os
import json
import logging
from typing import Optional, Dict, Any, List

import aiosqlite

log = logging.getLogger("gateway.db")

DB_PATH = os.environ.get(
    "META_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "metadatabase.db"),
)

# SQL for creating tables
_CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    username  TEXT    NOT NULL UNIQUE,
    hashed_pw TEXT    NOT NULL,
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
    await db.executescript(_CREATE_USERS_TABLE + _CREATE_USER_CONFIGS_TABLE + _CREATE_USER_SESSIONS_TABLE)
    await db.commit()
    await db.close()
    log.info("metadatabase initialised at %s", DB_PATH)


async def _get_db() -> aiosqlite.Connection:
    """Open an existing database connection (tables must already exist)."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    return db


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
    finally:
        await db.close()


async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Look up a user by username. Returns dict with id, username, hashed_pw or None."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT id, username, hashed_pw FROM users WHERE username = ?",
            (username,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {"id": row["id"], "username": row["username"], "hashed_pw": row["hashed_pw"]}
    finally:
        await db.close()


async def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Look up a user by id. Returns dict with id, username, hashed_pw or None."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT id, username, hashed_pw FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {"id": row["id"], "username": row["username"], "hashed_pw": row["hashed_pw"]}
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# User config operations (key-value per user, stored as JSON strings)
# ---------------------------------------------------------------------------

async def set_user_config(user_id: int, key: str, value: Any) -> None:
    """Upsert a config key-value pair for a user. value is auto-serialized to JSON."""
    db = await _get_db()
    try:
        json_value = json.dumps(value) if not isinstance(value, str) else value
        await db.execute(
            "INSERT INTO user_configs (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
            (user_id, key, json_value),
        )
        await db.commit()
    finally:
        await db.close()


async def get_user_config(user_id: int, key: str) -> Optional[Any]:
    """Get a single config value for a user. Returns None if not found."""
    db = await _get_db()
    try:
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
    finally:
        await db.close()


async def get_all_user_configs(user_id: int) -> Dict[str, Any]:
    """Get all config key-value pairs for a user as a dict."""
    db = await _get_db()
    try:
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
    finally:
        await db.close()


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
    finally:
        await db.close()


async def get_session_owner(session_id: str) -> Optional[int]:
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT user_id FROM user_sessions WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row["user_id"] if row else None
    finally:
        await db.close()


async def list_user_sessions(user_id: int) -> List[Dict[str, Any]]:
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT session_id, created_at FROM user_sessions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [{"session_id": r["session_id"], "created_at": r["created_at"], "created_at_ms": int(r["created_at"] * 1000) if r["created_at"] else None} for r in rows]
    finally:
        await db.close()


async def delete_session(session_id: str) -> bool:
    db = await _get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM user_sessions WHERE session_id = ?",
            (session_id,),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()
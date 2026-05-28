"""python.db.user_service — User and authentication management."""

from __future__ import annotations

import uuid
from typing import Any

from .manager import DatabaseManager


class UserService:
    """User get-or-create and session management."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def ensure_user(self, user_id: str, username: str) -> None:
        """Ensure a user exists in the app DB (create if not).

        If the username already exists with a different ID (from the old
        UUID-based flow), migrate the existing row and all FK references
        to the new metadb-derived ID.
        """
        # Already exists with matching ID — nothing to do
        rows = await self._db.execute(
            "SELECT id FROM users WHERE id = ?", (user_id,)
        )
        if rows:
            return

        # Exists with same username but different ID (old UUID flow)
        rows = await self._db.execute(
            "SELECT id FROM users WHERE username = ?", (username.strip(),)
        )
        if rows:
            old_id = rows[0]["id"]
            await self._db.execute_write(
                "UPDATE skills SET owner_id = ? WHERE owner_id = ?", (user_id, old_id)
            )
            await self._db.execute_write(
                "UPDATE user_active_skills SET user_id = ? WHERE user_id = ?", (user_id, old_id)
            )
            await self._db.execute_write(
                "UPDATE users SET id = ? WHERE id = ?", (user_id, old_id)
            )
            return

        # New user — insert
        await self._db.execute_write(
            "INSERT INTO users (id, username) VALUES (?, ?)",
            (user_id, username.strip()),
        )

    async def login(self, username: str) -> dict[str, Any]:
        """Get or create a user by username. Returns user row as dict."""
        rows = await self._db.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip(),)
        )
        if rows:
            return dict(rows[0])

        user_id = str(uuid.uuid4())
        await self._db.execute_write(
            "INSERT INTO users (id, username) VALUES (?, ?)",
            (user_id, username.strip()),
        )
        return {"id": user_id, "username": username.strip()}

    async def save_api_config(
        self, user_id: str, config: dict[str, Any]
    ) -> None:
        """Upsert API config for a user."""
        await self._db.execute_write(
            """INSERT INTO user_api_configs
               (user_id, provider, api_key, base_url, model, system_prompt, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
               provider = excluded.provider,
               api_key = excluded.api_key,
               base_url = excluded.base_url,
               model = excluded.model,
               system_prompt = excluded.system_prompt,
               updated_at = CURRENT_TIMESTAMP""",
            (
                user_id,
                config.get("provider", ""),
                config.get("api_key", ""),
                config.get("base_url", ""),
                config.get("model", ""),
                config.get("system_prompt", "You are a database diagnosis assistant."),
            ),
        )

    async def get_api_config(self, user_id: str) -> dict[str, Any] | None:
        """Get API config for a user. Returns None if no config exists."""
        rows = await self._db.execute(
            "SELECT * FROM user_api_configs WHERE user_id = ?",
            (user_id,),
        )
        if rows:
            return dict(rows[0])
        return None

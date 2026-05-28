"""python.db.admin_service — Admin-level operations spanning metadb and app DB."""

from __future__ import annotations

import json
import logging
from typing import Any

from .manager import DatabaseManager
from .skill_service import SkillService

log = logging.getLogger("gateway.admin")


class AdminService:
    """Admin-only operations: user management, skill override."""

    def __init__(self, db: DatabaseManager, skill_service: SkillService) -> None:
        self._db = db
        self._skill_service = skill_service

    async def get_all_users_with_counts(self) -> list[dict[str, Any]]:
        """Get all users from metadb enriched with skill counts from app DB."""
        import metadb as meta

        users = await meta.get_all_users()
        for u in users:
            rows = await self._db.execute(
                "SELECT COUNT(*) as cnt FROM skills WHERE owner_id = ?",
                (str(u["id"]),),
            )
            u["skill_count"] = rows[0]["cnt"] if rows else 0
        return users

    async def delete_user(self, user_id: int) -> None:
        """Delete a user and all their data from both databases."""
        uid_str = str(user_id)

        # 1a. Detach cloned skills
        await self._db.execute_write(
            """UPDATE skills SET source_skill_id = NULL
               WHERE source_skill_id IN (SELECT id FROM skills WHERE owner_id = ?)""",
            (uid_str,),
        )
        # 1b. Delete user_active_skills entries
        await self._db.execute_write(
            "DELETE FROM user_active_skills WHERE user_id = ?", (uid_str,),
        )
        await self._db.execute_write(
            "DELETE FROM user_active_skills WHERE skill_id IN "
            "(SELECT id FROM skills WHERE owner_id = ?)",
            (uid_str,),
        )
        # 1c. Delete all skills owned by the user
        await self._db.execute_write(
            "DELETE FROM skills WHERE owner_id = ?", (uid_str,),
        )
        # 1d. Delete API config
        await self._db.execute_write(
            "DELETE FROM user_api_configs WHERE user_id = ?", (uid_str,),
        )
        # 1e. Delete user from app DB
        await self._db.execute_write(
            "DELETE FROM users WHERE id = ?", (uid_str,),
        )

        # 2. Delete from metadb (cascades to user_configs)
        import metadb as meta

        await meta.delete_user_cascade(user_id)
        log.info("admin deleted user id=%s", user_id)

    async def update_any_skill(
        self, skill_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update ANY skill (official, community, or personal) — bypasses owner check."""
        skill = await self._skill_service.get_skill(skill_id)
        if not skill:
            return None

        name = data.get("name", skill["name"])
        description = data.get("description", skill["description"])
        content = data.get("content", skill["content"])

        old_meta = json.loads(skill.get("metadata", "{}"))
        new_meta = dict(data)
        for key in (
            "id", "name", "description", "content", "owner_id", "is_official",
            "is_published", "source_skill_id", "created_at", "updated_at",
        ):
            new_meta.pop(key, None)
        old_meta.update(new_meta)

        # No owner_id check in WHERE — this is the admin override
        await self._db.execute_write(
            """UPDATE skills
               SET name = ?, description = ?, content = ?, metadata = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (name, description, content, json.dumps(old_meta, ensure_ascii=False), skill_id),
        )
        return await self._skill_service.get_skill(skill_id)

    async def delete_any_skill(self, skill_id: str) -> None:
        """Delete ANY skill — bypasses owner check."""
        # Remove activation entries
        await self._db.execute_write(
            "DELETE FROM user_active_skills WHERE skill_id = ?",
            (skill_id,),
        )
        # Detach cloned copies
        await self._db.execute_write(
            "UPDATE skills SET source_skill_id = NULL WHERE source_skill_id = ?",
            (skill_id,),
        )
        # Delete the skill itself — no owner_id check
        await self._db.execute_write(
            "DELETE FROM skills WHERE id = ?",
            (skill_id,),
        )
        log.info("admin deleted skill id=%s", skill_id)

    async def toggle_any_publish(self, skill_id: str) -> bool | None:
        """Toggle publish status on ANY skill — bypasses owner check."""
        skill = await self._skill_service.get_skill(skill_id)
        if not skill:
            return None
        new_status = 0 if skill["is_published"] else 1
        await self._db.execute_write(
            "UPDATE skills SET is_published = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, skill_id),
        )
        return bool(new_status)

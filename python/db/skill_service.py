"""python.db.skill_service — Business logic for skill CRUD."""

from __future__ import annotations

import json
import uuid
from typing import Any

import yaml

from .manager import DatabaseManager


class SkillService:
    """High-level operations wrapping DatabaseManager."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Skills — CRUD
    # ------------------------------------------------------------------

    async def create_skill(
        self, user_id: str | None, data: dict[str, Any], is_official: bool = False,
    ) -> dict[str, Any]:
        """Create a new personal skill from form data or parsed file.

        When *is_official* is True the skill is created as an official skill
        (owner_id = NULL, is_official = 1). This is an admin-only operation.
        """
        skill_id = data.get("id", str(uuid.uuid4()))
        name = data.get("name", "Untitled Skill")
        description = data.get("description", "")
        content = data.get("content", "")

        # Everything except the fixed columns goes into metadata
        meta = dict(data)
        for key in ("id", "name", "description", "content"):
            meta.pop(key, None)

        if is_official:
            await self._db.execute_write(
                """INSERT INTO skills
                   (id, name, description, content, metadata, owner_id, is_official, is_published)
                   VALUES (?, ?, ?, ?, ?, NULL, 1, 1)""",
                (skill_id, name, description, content, json.dumps(meta, ensure_ascii=False)),
            )
        else:
            await self._db.execute_write(
                """INSERT INTO skills
                   (id, name, description, content, metadata, owner_id, is_published)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (
                    skill_id,
                    name,
                    description,
                    content,
                    json.dumps(meta, ensure_ascii=False),
                    user_id,
                ),
            )
        return await self.get_skill(skill_id)

    async def update_skill(
        self, skill_id: str, user_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a personal skill (only if owned by user_id)."""
        skill = await self.get_skill(skill_id)
        if not skill or skill.get("owner_id") != user_id:
            return None

        name = data.get("name", skill["name"])
        description = data.get("description", skill["description"])
        content = data.get("content", skill["content"])

        # Merge existing metadata with update
        old_meta = json.loads(skill.get("metadata", "{}"))
        new_meta = dict(data)
        for key in ("id", "name", "description", "content", "owner_id", "is_official",
                     "is_published", "source_skill_id", "created_at", "updated_at"):
            new_meta.pop(key, None)
        old_meta.update(new_meta)

        await self._db.execute_write(
            """UPDATE skills
               SET name = ?, description = ?, content = ?, metadata = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND owner_id = ?""",
            (
                name,
                description,
                content,
                json.dumps(old_meta, ensure_ascii=False),
                skill_id,
                user_id,
            ),
        )
        return await self.get_skill(skill_id)

    async def delete_skill(self, skill_id: str, user_id: str) -> bool:
        """Delete a personal skill (only if owned by user_id).

        Cleans up user_active_skills references and cloned copies first.
        """
        # Remove activation entries
        await self._db.execute_write(
            "DELETE FROM user_active_skills WHERE skill_id = ?",
            (skill_id,),
        )
        # Detach cloned copies (set source_skill_id to NULL)
        await self._db.execute_write(
            "UPDATE skills SET source_skill_id = NULL WHERE source_skill_id = ?",
            (skill_id,),
        )
        # Now delete the skill itself
        await self._db.execute_write(
            "DELETE FROM skills WHERE id = ? AND owner_id = ?",
            (skill_id, user_id),
        )
        return True

    # ------------------------------------------------------------------
    # Skills — queries
    # ------------------------------------------------------------------

    async def get_user_skills(self, user_id: str) -> list[dict[str, Any]]:
        """Get all skills in user's personal repository (own + copied).

        Each row includes a source_is_official flag indicating whether
        source_skill_id refers to an official (1) or community (0) skill,
        or None for user-created skills.
        """
        rows = await self._db.execute(
            """SELECT s.*, src.is_official AS source_is_official
               FROM skills s
               LEFT JOIN skills src ON s.source_skill_id = src.id
               WHERE s.owner_id = ?
               ORDER BY s.is_official DESC, s.created_at DESC""",
            (user_id,),
        )
        return [dict(r) for r in rows]

    async def get_all_skills(self) -> list[dict[str, Any]]:
        """Get ALL skills (admin only). No owner filter."""
        rows = await self._db.execute(
            """SELECT s.*, src.is_official AS source_is_official
               FROM skills s
               LEFT JOIN skills src ON s.source_skill_id = src.id
               ORDER BY s.is_official DESC, s.created_at DESC""",
        )
        return [dict(r) for r in rows]

    async def get_square_skills(
        self, category: str | None = None, search: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Get skills for the Skill Square, separated by official/published."""
        # Official skills (respect is_published so admin can hide/show)
        official = await self._db.execute(
            "SELECT * FROM skills WHERE is_official = 1 AND is_published = 1 ORDER BY name"
        )
        # Published personal skills
        published = await self._db.execute(
            """SELECT s.*, u.username FROM skills s
               LEFT JOIN users u ON s.owner_id = u.id
               WHERE s.is_official = 0 AND s.is_published = 1
               ORDER BY s.created_at DESC""",
        )
        result = {
            "official": [dict(r) for r in official],
            "published": [dict(r) for r in published],
        }

        # Filter by category (search within metadata JSON)
        if category:
            for key in ("official", "published"):
                result[key] = [
                    s for s in result[key]
                    if json.loads(s.get("metadata", "{}")).get("category") == category
                ]
        if search:
            q = search.lower()
            for key in ("official", "published"):
                result[key] = [
                    s for s in result[key]
                    if q in s["name"].lower() or q in s["description"].lower()
                ]

        return result

    async def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM skills WHERE id = ?", (skill_id,)
        )
        return dict(rows[0]) if rows else None

    # ------------------------------------------------------------------
    # Skills — actions
    # ------------------------------------------------------------------

    async def clone_skill(
        self, skill_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Copy a skill from the square into user's personal repository."""
        original = await self.get_skill(skill_id)
        if not original:
            return None
        # Don't clone if user already has this skill
        existing = await self._db.execute(
            "SELECT id FROM skills WHERE source_skill_id = ? AND owner_id = ?",
            (skill_id, user_id),
        )
        if existing:
            return await self.get_skill(existing[0]["id"])

        new_id = str(uuid.uuid4())
        meta = json.loads(original.get("metadata", "{}"))
        await self._db.execute_write(
            """INSERT INTO skills
               (id, name, description, content, metadata, owner_id,
                is_official, is_published, source_skill_id)
               VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)""",
            (
                new_id,
                original["name"],
                original["description"],
                original["content"],
                json.dumps(meta, ensure_ascii=False),
                user_id,
                skill_id,
            ),
        )
        return await self.get_skill(new_id)

    async def toggle_publish(self, skill_id: str, user_id: str) -> bool | None:
        """Toggle publish status. Only for skills owned by the user.

        Cloned skills (with source_skill_id) cannot be published.
        Returns new is_published status, or None if not found/not owned / not publishable.
        If another personal skill of the same name is already published,
        it is automatically unpublished first (replaced).
        """
        skill = await self.get_skill(skill_id)
        if not skill or skill.get("owner_id") != user_id:
            return None
        if not skill["is_published"]:
            # Cloned skills cannot be published back to community
            if skill.get("source_skill_id"):
                return None
            # Replace existing published skill with the same name
            await self._db.execute_write(
                "UPDATE skills SET is_published = 0 WHERE owner_id = ? AND name = ? AND is_published = 1 AND id != ?",
                (user_id, skill["name"], skill_id),
            )
        new_status = 0 if skill["is_published"] else 1
        await self._db.execute_write(
            "UPDATE skills SET is_published = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, skill_id),
        )
        return bool(new_status)

    async def toggle_active(self, user_id: str, skill_id: str) -> bool:
        """Toggle whether a skill is active for chat sessions."""
        rows = await self._db.execute(
            "SELECT 1 FROM user_active_skills WHERE user_id = ? AND skill_id = ?",
            (user_id, skill_id),
        )
        if rows:
            await self._db.execute_write(
                "DELETE FROM user_active_skills WHERE user_id = ? AND skill_id = ?",
                (user_id, skill_id),
            )
            return False
        else:
            await self._db.execute_write(
                "INSERT INTO user_active_skills (user_id, skill_id) VALUES (?, ?)",
                (user_id, skill_id),
            )
            return True

    async def get_active_skill_ids(self, user_id: str) -> list[str]:
        rows = await self._db.execute(
            "SELECT skill_id FROM user_active_skills WHERE user_id = ?",
            (user_id,),
        )
        return [r["skill_id"] for r in rows]

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_skill_file(filepath: str) -> dict[str, Any]:
        """Parse an uploaded SKILL.md file into a flat dict for create_skill."""
        path = __import__("pathlib").Path(filepath)
        raw = path.read_text(encoding="utf-8")

        frontmatter: dict[str, Any] = {}
        content = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1]) or {}
                content = parts[2].strip()

        data = dict(frontmatter)
        data["content"] = content
        return data

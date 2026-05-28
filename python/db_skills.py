# -*- coding: utf-8 -*-
"""Skills database (app DB: data/db_clinic.db) — Python 3.9 compatible."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

log = logging.getLogger("gateway.db_skills")

APP_DB_PATH = os.environ.get(
    "APP_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "db_clinic.db"),
)


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(APP_DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _row_to_skill(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "content": row["content"],
        "metadata": row["metadata"],
        "owner_id": row["owner_id"],
        "is_official": row["is_official"],
        "is_published": row["is_published"],
        "source_skill_id": row["source_skill_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_user_skills(user_id: str, is_admin: bool = False) -> List[Dict[str, Any]]:
    """Get skills accessible by a user. Admin sees all skills."""
    db = _get_db()
    try:
        if is_admin:
            cur = db.execute("SELECT s.* FROM skills s ORDER BY s.updated_at DESC")
        else:
            cur = db.execute(
                "SELECT s.* FROM skills s WHERE s.owner_id = ? OR s.source_skill_id IS NOT NULL ORDER BY s.updated_at DESC",
                (user_id,),
            )
        results = []
        for row in cur.fetchall():
            skill = _row_to_skill(row)
            # For cloned skills, add source info
            if skill["source_skill_id"]:
                src = db.execute(
                    "SELECT is_official FROM skills WHERE id = ?", (skill["source_skill_id"],)
                ).fetchone()
                skill["source_is_official"] = src["is_official"] if src else None
            else:
                skill["source_is_official"] = None
            results.append(skill)
        return results
    finally:
        db.close()


def get_user_active_ids(user_id: str) -> List[str]:
    db = _get_db()
    try:
        cur = db.execute(
            "SELECT skill_id FROM user_active_skills WHERE user_id = ?", (user_id,)
        )
        return [r["skill_id"] for r in cur.fetchall()]
    finally:
        db.close()


def get_square_skills(search: str = "", category: str = "") -> Dict[str, List[Dict[str, Any]]]:
    """Get official and published skills for the skill square."""
    db = _get_db()
    try:
        off_cur = db.execute(
            "SELECT * FROM skills WHERE is_official = 1 ORDER BY name"
        )
        official = [dict(r) for r in off_cur.fetchall()]

        pub_cur = db.execute(
            "SELECT s.*, u.username FROM skills s LEFT JOIN users u ON s.owner_id = u.id "
            "WHERE s.is_official = 0 AND s.is_published = 1 ORDER BY s.updated_at DESC"
        )
        published = [dict(r) for r in pub_cur.fetchall()]

        # Post-filter since metadata is a JSON string stored in TEXT field
        if search:
            sl = search.lower()
            official = [s for s in official if sl in s["name"].lower() or sl in s["description"].lower()]
            published = [s for s in published if sl in s["name"].lower() or sl in s["description"].lower()]
        if category:
            official = [s for s in official if category in s.get("metadata", "")]
            published = [s for s in published if category in s.get("metadata", "")]

        return {"official": official, "published": published}
    finally:
        db.close()


def create_skill(
    user_id: str,
    name: str,
    content: str = "",
    description: str = "",
    skill_type: str = "case",
    category: str = "",
    keywords: Optional[List[str]] = None,
    triggers: Optional[List[str]] = None,
    symptoms: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
) -> str:
    db = _get_db()
    try:
        skill_id = str(uuid.uuid4())
        metadata = {
            "skill_type": skill_type,
            "category": category,
            "keywords": keywords or [],
            "triggers": triggers or [],
            "symptoms": symptoms or [],
            "tags": tags or [],
        }
        db.execute(
            "INSERT INTO skills (id, name, description, content, metadata, owner_id) VALUES (?, ?, ?, ?, ?, ?)",
            (skill_id, name, description, content, json.dumps(metadata), user_id),
        )
        db.commit()
        log.info("created skill %s for user %s", skill_id, user_id)
        return skill_id
    finally:
        db.close()


def update_skill(skill_id: str, user_id: str, data: Dict[str, Any]) -> bool:
    db = _get_db()
    try:
        existing = db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if not existing:
            return False

        # Check ownership (admin bypasses, done at endpoint level)
        updates = []
        params: List[Any] = []
        for field in ("name", "description", "content"):
            if field in data:
                updates.append(f"{field} = ?")
                params.append(data[field])

        if any(k in data for k in ("skill_type", "category", "keywords", "triggers", "symptoms", "tags")):
            meta = json.loads(existing["metadata"] or "{}")
            if "skill_type" in data:
                meta["skill_type"] = data["skill_type"]
            if "category" in data:
                meta["category"] = data["category"]
            if "keywords" in data:
                meta["keywords"] = data["keywords"]
            if "triggers" in data:
                meta["triggers"] = data["triggers"]
            if "symptoms" in data:
                meta["symptoms"] = data["symptoms"]
            if "tags" in data:
                meta["tags"] = data["tags"]
            updates.append("metadata = ?")
            params.append(json.dumps(meta))

        if not updates:
            return True

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(skill_id)
        db.execute(f"UPDATE skills SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()
        return True
    finally:
        db.close()


def delete_skill(skill_id: str) -> bool:
    db = _get_db()
    try:
        db.execute("DELETE FROM user_active_skills WHERE skill_id = ?", (skill_id,))
        cur = db.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def clone_skill(skill_id: str, user_id: str) -> Optional[str]:
    db = _get_db()
    try:
        original = db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if not original:
            return None
        new_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO skills (id, name, description, content, metadata, owner_id, source_skill_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id, original["name"], original["description"], original["content"], original["metadata"], user_id, skill_id),
        )
        db.commit()
        return new_id
    finally:
        db.close()


def toggle_active(skill_id: str, user_id: str) -> bool:
    db = _get_db()
    try:
        existing = db.execute(
            "SELECT 1 FROM user_active_skills WHERE user_id = ? AND skill_id = ?",
            (user_id, skill_id),
        ).fetchone()
        if existing:
            db.execute("DELETE FROM user_active_skills WHERE user_id = ? AND skill_id = ?", (user_id, skill_id))
            is_active = False
        else:
            db.execute("INSERT OR IGNORE INTO user_active_skills (user_id, skill_id) VALUES (?, ?)", (user_id, skill_id))
            is_active = True
        db.commit()
        return is_active
    finally:
        db.close()


def toggle_publish(skill_id: str, user_id: str) -> bool:
    db = _get_db()
    try:
        skill = db.execute("SELECT owner_id, is_published FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if not skill or skill["owner_id"] != user_id:
            return False
        new_val = 0 if skill["is_published"] else 1
        db.execute("UPDATE skills SET is_published = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_val, skill_id))
        db.commit()
        return bool(new_val)
    finally:
        db.close()


def ensure_user_exists(user_id: str, username: str) -> None:
    """Ensure a user record exists in the app DB (created on login/register)."""
    db = _get_db()
    try:
        db.execute("INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)", (user_id, username))
        db.commit()
    finally:
        db.close()

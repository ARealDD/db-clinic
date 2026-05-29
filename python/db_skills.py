# -*- coding: utf-8 -*-
"""Skills CRUD against the shared metadatabase. Python 3.9 compatible.

Schema lives in db.py (init_db); this module only reads/writes the
skills and user_active_skills tables. user_id columns are INTEGER FKs
into users.id — the previous denormalised users table in db_clinic.db
is gone.

All public functions in this module are SYNC by design — they're meant
to be awaited via `asyncio.to_thread(...)` from the FastAPI handlers so
they don't block the event loop. A single process-wide sqlite3
connection is shared across worker threads (check_same_thread=False)
and serialised by `_DB_SKILLS_LOCK`. Opening a connection per call
costs ~2 ms; at 20 QPS that's pure overhead the lock-around-shared
pattern avoids. Each public function takes the lock for its full body
so multi-statement operations (SELECT-then-UPDATE in update_skill,
DELETE-then-INSERT in toggle_active, etc.) stay atomic.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional

from db import DB_PATH

log = logging.getLogger("gateway.db_skills")


_SHARED_DB_SKILLS: Optional[sqlite3.Connection] = None
_DB_SKILLS_LOCK = threading.Lock()


def _get_db() -> sqlite3.Connection:
    """Return the process-wide sqlite3 connection (lazy-init, thread-safe).

    Callers MUST take `_DB_SKILLS_LOCK` for the operation they perform —
    sqlite3 connections are not safe for concurrent use even with
    check_same_thread=False.
    """
    global _SHARED_DB_SKILLS
    if _SHARED_DB_SKILLS is not None:
        return _SHARED_DB_SKILLS
    with _DB_SKILLS_LOCK:
        if _SHARED_DB_SKILLS is None:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            _SHARED_DB_SKILLS = conn
    return _SHARED_DB_SKILLS


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


def get_user_skills(user_id: int, is_admin: bool = False) -> List[Dict[str, Any]]:
    """Get skills accessible by a user. Admin sees all skills."""
    db = _get_db()
    with _DB_SKILLS_LOCK:
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
            if skill["source_skill_id"]:
                src = db.execute(
                    "SELECT is_official FROM skills WHERE id = ?", (skill["source_skill_id"],)
                ).fetchone()
                skill["source_is_official"] = src["is_official"] if src else None
            else:
                skill["source_is_official"] = None
            results.append(skill)
        return results


def get_user_active_ids(user_id: int) -> List[str]:
    db = _get_db()
    with _DB_SKILLS_LOCK:
        cur = db.execute(
            "SELECT skill_id FROM user_active_skills WHERE user_id = ?", (user_id,)
        )
        return [r["skill_id"] for r in cur.fetchall()]


def get_skill_count() -> int:
    db = _get_db()
    with _DB_SKILLS_LOCK:
        return db.execute("SELECT COUNT(*) FROM skills").fetchone()[0]


def create_official_skill(
    skill_id: str,
    name: str,
    description: str,
    content: str,
    metadata: str,
) -> None:
    db = _get_db()
    with _DB_SKILLS_LOCK:
        db.execute(
            "INSERT OR IGNORE INTO skills (id, name, description, content, metadata, owner_id, is_official, is_published) VALUES (?, ?, ?, ?, ?, 1, 1, 1)",
            (skill_id, name, description, content, metadata),
        )
        db.commit()


def get_square_skills(search: str = "", category: str = "") -> Dict[str, List[Dict[str, Any]]]:
    """Get official and published skills for the skill square."""
    db = _get_db()
    with _DB_SKILLS_LOCK:
        off_cur = db.execute(
            "SELECT * FROM skills WHERE is_official = 1 ORDER BY name"
        )
        official = [dict(r) for r in off_cur.fetchall()]

        pub_cur = db.execute(
            "SELECT s.*, u.username FROM skills s LEFT JOIN users u ON s.owner_id = u.id "
            "WHERE s.is_official = 0 AND s.is_published = 1 ORDER BY s.updated_at DESC"
        )
        published = [dict(r) for r in pub_cur.fetchall()]

        if search:
            sl = search.lower()
            official = [s for s in official if sl in s["name"].lower() or sl in s["description"].lower()]
            published = [s for s in published if sl in s["name"].lower() or sl in s["description"].lower()]
        if category:
            official = [s for s in official if category in s.get("metadata", "")]
            published = [s for s in published if category in s.get("metadata", "")]

        return {"official": official, "published": published}


def create_skill(
    user_id: int,
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
    with _DB_SKILLS_LOCK:
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


def update_skill(skill_id: str, user_id: int, data: Dict[str, Any]) -> bool:
    db = _get_db()
    with _DB_SKILLS_LOCK:
        existing = db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if not existing:
            return False

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


def delete_skill(skill_id: str) -> bool:
    db = _get_db()
    with _DB_SKILLS_LOCK:
        db.execute("DELETE FROM user_active_skills WHERE skill_id = ?", (skill_id,))
        cur = db.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        db.commit()
        return cur.rowcount > 0


def clone_skill(skill_id: str, user_id: int) -> Optional[str]:
    db = _get_db()
    with _DB_SKILLS_LOCK:
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


def toggle_active(skill_id: str, user_id: int) -> bool:
    db = _get_db()
    with _DB_SKILLS_LOCK:
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


def toggle_publish(skill_id: str, user_id: int) -> bool:
    db = _get_db()
    with _DB_SKILLS_LOCK:
        skill = db.execute("SELECT owner_id, is_published FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if not skill or skill["owner_id"] != user_id:
            return False
        new_val = 0 if skill["is_published"] else 1
        db.execute("UPDATE skills SET is_published = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_val, skill_id))
        db.commit()
        return bool(new_val)

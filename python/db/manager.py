"""
python.db.manager — SQLite database wrapper.

Default database path: <repo-root>/data/db_clinic.db
Override via DB_PATH env var.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import yaml


class DatabaseManager:
    """Async SQLite wrapper for skill and memory persistence.

    Usage:
        db = DatabaseManager()
        await db.initialize()
        rows = await db.execute("SELECT 1")
        await db.close()
    """

    def __init__(self, db_path: str | None = None) -> None:
        if not db_path:
            db_path = os.environ.get("DB_PATH", "")
        if not db_path:
            repo_root = Path(__file__).resolve().parent.parent.parent
            db_path = str(repo_root / "data" / "db_clinic.db")
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> str:
        return self._db_path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the database directory, connect, and create tables."""

        def _init() -> sqlite3.Connection:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._migrate_schema(conn)
            self._create_tables(conn)
            conn.commit()
            return conn

        self._conn = await asyncio.to_thread(_init)

    async def close(self) -> None:
        if self._conn is not None:

            def _close() -> None:
                self._conn.close()  # type: ignore[union-attr]

            await asyncio.to_thread(_close)
            self._conn = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def execute(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Execute a read query and return all rows."""
        assert self._conn is not None, "DatabaseManager not initialized"

        def _run() -> list[sqlite3.Row]:
            cur = self._conn.execute(query, params)
            return cur.fetchall()

        return await asyncio.to_thread(_run)

    async def execute_write(self, query: str, params: tuple = ()) -> None:
        """Execute a write query and commit."""
        assert self._conn is not None, "DatabaseManager not initialized"

        def _run() -> None:
            self._conn.execute(query, params)
            self._conn.commit()

        await asyncio.to_thread(_run)

    async def executemany(self, query: str, params_list: list[tuple]) -> None:
        """Execute a write query for multiple parameter sets and commit."""
        assert self._conn is not None, "DatabaseManager not initialized"

        def _run() -> None:
            self._conn.executemany(query, params_list)
            self._conn.commit()

        await asyncio.to_thread(_run)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_skill_file(filepath: str) -> dict[str, Any]:
        """Parse a SKILL.md file into DB columns.

        Returns a dict with keys: id, name, description, content, metadata.
        """
        path = Path(filepath)
        raw = path.read_text(encoding="utf-8")

        # Split YAML frontmatter (--- ... ---) from body
        content = raw
        frontmatter: dict[str, Any] = {}
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1]) or {}
                content = parts[2].strip()

        # Extract fixed columns; everything else goes into metadata
        skill_id = str(frontmatter.pop("id", path.parent.name))
        name = str(frontmatter.pop("name", skill_id))
        description = str(frontmatter.pop("description", ""))

        return {
            "id": skill_id,
            "name": name,
            "description": description,
            "content": content,
            "metadata": json.dumps(frontmatter, ensure_ascii=False),
        }

    async def seed_official_skills(self, skills_root: str | None = None) -> int:
        """Load official skills from skills/case/ and skills/knowledge/ into DB.

        Returns count of newly inserted skills (skips existing IDs).
        """
        if skills_root is None:
            repo_root = Path(__file__).resolve().parent.parent.parent
            skills_root = str(repo_root / "skills")
        skills_root = Path(skills_root)

        def _seed() -> int:
            count = 0
            for subdir in ["case", "knowledge"]:
                search_path = skills_root / subdir
                if not search_path.exists():
                    continue
                for entry in sorted(search_path.iterdir()):
                    skill_file = entry / "SKILL.md"
                    if not skill_file.exists():
                        continue
                    parsed = DatabaseManager._parse_skill_file(str(skill_file))
                    existing = self._conn.execute(
                        "SELECT 1 FROM skills WHERE id = ? AND is_official = 1",
                        (parsed["id"],),
                    ).fetchone()
                    if existing:
                        continue
                    self._conn.execute(
                        """INSERT INTO skills
                           (id, name, description, content, metadata, is_official)
                           VALUES (?, ?, ?, ?, ?, 1)""",
                        (
                            parsed["id"],
                            parsed["name"],
                            parsed["description"],
                            parsed["content"],
                            parsed["metadata"],
                        ),
                    )
                    count += 1
            self._conn.commit()
            return count

        return await asyncio.to_thread(_seed)

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        """Migrate old schema if needed (development-only, recreates tables)."""
        cursor = conn.execute("PRAGMA table_info(skills)")
        cols = {row[1] for row in cursor.fetchall()}
        # Old schema had 'category' instead of 'owner_id'
        if "category" in cols and "owner_id" not in cols:
            conn.executescript("""
                DROP TABLE IF EXISTS user_active_skills;
                DROP TABLE IF EXISTS skills;
            """)

    @staticmethod
    def _create_tables(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         TEXT PRIMARY KEY,
                username   TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id               TEXT PRIMARY KEY,
                name             TEXT NOT NULL,
                description      TEXT DEFAULT '',
                content          TEXT NOT NULL DEFAULT '',
                metadata         TEXT DEFAULT '{}',
                owner_id         TEXT REFERENCES users(id),
                is_official      INTEGER DEFAULT 0,
                is_published     INTEGER DEFAULT 0,
                source_skill_id  TEXT,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_active_skills (
                user_id  TEXT NOT NULL REFERENCES users(id),
                skill_id TEXT NOT NULL REFERENCES skills(id),
                PRIMARY KEY (user_id, skill_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_api_configs (
                user_id        TEXT PRIMARY KEY REFERENCES users(id),
                provider       TEXT DEFAULT '',
                api_key        TEXT DEFAULT '',
                base_url       TEXT DEFAULT '',
                model          TEXT DEFAULT '',
                system_prompt  TEXT DEFAULT 'You are a database diagnosis assistant.',
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

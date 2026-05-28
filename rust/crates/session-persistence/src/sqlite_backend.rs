use std::path::{Path, PathBuf};
use std::sync::Mutex;

use rusqlite::{params, OptionalExtension};
use rusqlite::Connection;

use crate::{
    Checkpoint, CompactionRecord, MessageRecord, PersistenceError, PromptHistoryRecord,
    SessionBackend, SessionRecord,
};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS session_meta (
    session_id        TEXT PRIMARY KEY,
    created_at_ms     INTEGER NOT NULL,
    updated_at_ms     INTEGER NOT NULL,
    model             TEXT,
    workspace_root    TEXT,
    fork_parent_id    TEXT,
    fork_branch_name  TEXT,
    username          TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL,
    role              TEXT NOT NULL,
    content_json      TEXT NOT NULL,
    timestamp_ms      INTEGER NOT NULL,
    step_index        INTEGER NOT NULL DEFAULT 0,
    usage_json        TEXT,
    FOREIGN KEY (session_id) REFERENCES session_meta(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_id     TEXT NOT NULL,
    session_id        TEXT NOT NULL,
    step_index        INTEGER NOT NULL,
    timestamp_ms      INTEGER NOT NULL,
    message_count     INTEGER NOT NULL,
    summary           TEXT,
    FOREIGN KEY (session_id) REFERENCES session_meta(session_id) ON DELETE CASCADE,
    UNIQUE (session_id, step_index)
);

CREATE TABLE IF NOT EXISTS prompt_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL,
    timestamp_ms      INTEGER NOT NULL,
    text              TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES session_meta(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS compaction (
    session_id        TEXT PRIMARY KEY,
    count             INTEGER NOT NULL,
    removed_message_count INTEGER NOT NULL,
    summary           TEXT NOT NULL,
    timestamp_ms      INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES session_meta(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, step_index);
CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints(session_id);
CREATE INDEX IF NOT EXISTS idx_prompt_history_session ON prompt_history(session_id);
";

pub struct SqliteBackend {
    conn: Mutex<Connection>,
    #[allow(dead_code)]
    db_path: PathBuf,
}

impl SqliteBackend {
    pub fn open(root: &Path) -> Result<Self, PersistenceError> {
        let db_path = root.join("sessions.db");
        let conn = Connection::open(&db_path)
            .map_err(|e| PersistenceError::Sqlite(e.to_string()))?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")
            .map_err(|e| PersistenceError::Sqlite(e.to_string()))?;
        conn.execute_batch(SCHEMA)
            .map_err(|e| PersistenceError::Sqlite(e.to_string()))?;
        Ok(Self { conn: Mutex::new(conn), db_path })
    }
}

fn sqlite_err(e: rusqlite::Error) -> PersistenceError {
    PersistenceError::Sqlite(e.to_string())
}

fn lock_err(e: std::sync::PoisonError<std::sync::MutexGuard<Connection>>) -> PersistenceError {
    PersistenceError::Sqlite(e.to_string())
}

macro_rules! conn {
    ($self:expr) => {
        $self.conn.lock().map_err(lock_err)?
    };
}

impl SessionBackend for SqliteBackend {
    fn save_session_meta(&self, record: &SessionRecord) -> Result<(), PersistenceError> {
        conn!(self)
            .execute(
                "INSERT OR REPLACE INTO session_meta
                 (session_id, created_at_ms, updated_at_ms, model, workspace_root, fork_parent_id, fork_branch_name, username)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                params![
                    record.session_id,
                    record.created_at_ms,
                    record.updated_at_ms,
                    record.model,
                    record.workspace_root,
                    record.fork_parent_id,
                    record.fork_branch_name,
                    record.username,
                ],
            )
            .map_err(sqlite_err)?;
        Ok(())
    }

    fn load_session_meta(
        &self,
        session_id: &str,
    ) -> Result<Option<SessionRecord>, PersistenceError> {
        let conn = conn!(self);
        let mut stmt = conn
            .prepare(
                "SELECT session_id, created_at_ms, updated_at_ms, model, workspace_root, fork_parent_id, fork_branch_name, username
                 FROM session_meta WHERE session_id = ?1",
            )
            .map_err(sqlite_err)?;
        let result = stmt
            .query_row(params![session_id], |row| {
                Ok(SessionRecord {
                    session_id: row.get(0)?,
                    created_at_ms: row.get(1)?,
                    updated_at_ms: row.get(2)?,
                    model: row.get(3)?,
                    workspace_root: row.get(4)?,
                    fork_parent_id: row.get(5)?,
                    fork_branch_name: row.get(6)?,
                    username: row.get(7)?,
                })
            })
            .optional()
            .map_err(sqlite_err)?;
        Ok(result)
    }

    fn list_sessions(&self) -> Result<Vec<SessionRecord>, PersistenceError> {
        let conn = conn!(self);
        let mut stmt = conn
            .prepare(
                "SELECT session_id, created_at_ms, updated_at_ms, model, workspace_root, fork_parent_id, fork_branch_name, username
                 FROM session_meta ORDER BY created_at_ms ASC",
            )
            .map_err(sqlite_err)?;
        let rows = stmt
            .query_map(params![], |row| {
                Ok(SessionRecord {
                    session_id: row.get(0)?,
                    created_at_ms: row.get(1)?,
                    updated_at_ms: row.get(2)?,
                    model: row.get(3)?,
                    workspace_root: row.get(4)?,
                    fork_parent_id: row.get(5)?,
                    fork_branch_name: row.get(6)?,
                    username: row.get(7)?,
                })
            })
            .map_err(sqlite_err)?;
        let mut results = Vec::new();
        for row in rows {
            results.push(row.map_err(sqlite_err)?);
        }
        Ok(results)
    }

    fn delete_session(&self, session_id: &str) -> Result<bool, PersistenceError> {
        let count = conn!(self)
            .execute("DELETE FROM session_meta WHERE session_id = ?1", params![session_id])
            .map_err(sqlite_err)?;
        Ok(count > 0)
    }

    fn append_message(&self, msg: &MessageRecord) -> Result<(), PersistenceError> {
        let conn = conn!(self);
        conn
            .execute(
                "INSERT INTO messages (session_id, role, content_json, timestamp_ms, step_index, usage_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    msg.session_id,
                    msg.role,
                    msg.content_json,
                    msg.timestamp_ms,
                    msg.step_index,
                    msg.usage_json,
                ],
            )
            .map_err(sqlite_err)?;
        conn
            .execute(
                "UPDATE session_meta SET updated_at_ms = ?1 WHERE session_id = ?2",
                params![msg.timestamp_ms, msg.session_id],
            )
            .map_err(sqlite_err)?;
        Ok(())
    }

    fn load_messages(&self, session_id: &str) -> Result<Vec<MessageRecord>, PersistenceError> {
        let conn = conn!(self);
        let mut stmt = conn
            .prepare(
                "SELECT session_id, role, content_json, timestamp_ms, step_index, usage_json
                 FROM messages WHERE session_id = ?1 ORDER BY step_index ASC, id ASC",
            )
            .map_err(sqlite_err)?;
        let rows = stmt
            .query_map(params![session_id], |row| {
                Ok(MessageRecord {
                    session_id: row.get(0)?,
                    role: row.get(1)?,
                    content_json: row.get(2)?,
                    timestamp_ms: row.get(3)?,
                    step_index: row.get(4)?,
                    usage_json: row.get(5)?,
                })
            })
            .map_err(sqlite_err)?;
        let mut messages = Vec::new();
        for row in rows {
            messages.push(row.map_err(sqlite_err)?);
        }
        Ok(messages)
    }

    fn load_messages_from_step(
        &self,
        session_id: &str,
        step_index: usize,
    ) -> Result<Vec<MessageRecord>, PersistenceError> {
        let conn = conn!(self);
        let mut stmt = conn
            .prepare(
                "SELECT session_id, role, content_json, timestamp_ms, step_index, usage_json
                 FROM messages WHERE session_id = ?1 AND step_index >= ?2 ORDER BY step_index ASC, id ASC",
            )
            .map_err(sqlite_err)?;
        let rows = stmt
            .query_map(params![session_id, step_index], |row| {
                Ok(MessageRecord {
                    session_id: row.get(0)?,
                    role: row.get(1)?,
                    content_json: row.get(2)?,
                    timestamp_ms: row.get(3)?,
                    step_index: row.get(4)?,
                    usage_json: row.get(5)?,
                })
            })
            .map_err(sqlite_err)?;
        let mut messages = Vec::new();
        for row in rows {
            messages.push(row.map_err(sqlite_err)?);
        }
        Ok(messages)
    }

    fn message_count(&self, session_id: &str) -> Result<usize, PersistenceError> {
        let count: i64 = conn!(self)
            .query_row(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?1",
                params![session_id],
                |row| row.get(0),
            )
            .map_err(sqlite_err)?;
        Ok(usize::try_from(count).unwrap_or(0))
    }

    fn save_checkpoint(&self, checkpoint: &Checkpoint) -> Result<(), PersistenceError> {
        conn!(self)
            .execute(
                "INSERT OR REPLACE INTO checkpoints (checkpoint_id, session_id, step_index, timestamp_ms, message_count, summary)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    checkpoint.checkpoint_id,
                    checkpoint.session_id,
                    checkpoint.step_index,
                    checkpoint.timestamp_ms,
                    checkpoint.message_count,
                    checkpoint.summary,
                ],
            )
            .map_err(sqlite_err)?;
        Ok(())
    }

    fn load_latest_checkpoint(
        &self,
        session_id: &str,
    ) -> Result<Option<Checkpoint>, PersistenceError> {
        let conn = conn!(self);
        let mut stmt = conn
            .prepare(
                "SELECT checkpoint_id, session_id, step_index, timestamp_ms, message_count, summary
                 FROM checkpoints WHERE session_id = ?1 ORDER BY step_index DESC LIMIT 1",
            )
            .map_err(sqlite_err)?;
        let result = stmt
            .query_row(params![session_id], |row| {
                Ok(Checkpoint {
                    checkpoint_id: row.get(0)?,
                    session_id: row.get(1)?,
                    step_index: row.get::<_, i64>(2)? as usize,
                    timestamp_ms: row.get(3)?,
                    message_count: row.get::<_, i64>(4)? as usize,
                    summary: row.get(5)?,
                })
            })
            .optional()
            .map_err(sqlite_err)?;
        Ok(result)
    }

    fn load_checkpoint_at_step(
        &self,
        session_id: &str,
        step_index: usize,
    ) -> Result<Option<Checkpoint>, PersistenceError> {
        let conn = conn!(self);
        let mut stmt = conn
            .prepare(
                "SELECT checkpoint_id, session_id, step_index, timestamp_ms, message_count, summary
                 FROM checkpoints WHERE session_id = ?1 AND step_index = ?2",
            )
            .map_err(sqlite_err)?;
        let result = stmt
            .query_row(params![session_id, step_index], |row| {
                Ok(Checkpoint {
                    checkpoint_id: row.get(0)?,
                    session_id: row.get(1)?,
                    step_index: row.get::<_, i64>(2)? as usize,
                    timestamp_ms: row.get(3)?,
                    message_count: row.get::<_, i64>(4)? as usize,
                    summary: row.get(5)?,
                })
            })
            .optional()
            .map_err(sqlite_err)?;
        Ok(result)
    }

    fn list_checkpoints(&self, session_id: &str) -> Result<Vec<Checkpoint>, PersistenceError> {
        let conn = conn!(self);
        let mut stmt = conn
            .prepare(
                "SELECT checkpoint_id, session_id, step_index, timestamp_ms, message_count, summary
                 FROM checkpoints WHERE session_id = ?1 ORDER BY step_index ASC",
            )
            .map_err(sqlite_err)?;
        let rows = stmt
            .query_map(params![session_id], |row| {
                Ok(Checkpoint {
                    checkpoint_id: row.get(0)?,
                    session_id: row.get(1)?,
                    step_index: row.get::<_, i64>(2)? as usize,
                    timestamp_ms: row.get(3)?,
                    message_count: row.get::<_, i64>(4)? as usize,
                    summary: row.get(5)?,
                })
            })
            .map_err(sqlite_err)?;
        let mut checkpoints = Vec::new();
        for row in rows {
            checkpoints.push(row.map_err(sqlite_err)?);
        }
        Ok(checkpoints)
    }

    fn delete_checkpoints(&self, session_id: &str) -> Result<(), PersistenceError> {
        conn!(self)
            .execute("DELETE FROM checkpoints WHERE session_id = ?1", params![session_id])
            .map_err(sqlite_err)?;
        Ok(())
    }

    fn save_prompt_history(&self, record: &PromptHistoryRecord) -> Result<(), PersistenceError> {
        conn!(self)
            .execute(
                "INSERT INTO prompt_history (session_id, timestamp_ms, text) VALUES (?1, ?2, ?3)",
                params![record.session_id, record.timestamp_ms, record.text],
            )
            .map_err(sqlite_err)?;
        Ok(())
    }

    fn load_prompt_history(
        &self,
        session_id: &str,
    ) -> Result<Vec<PromptHistoryRecord>, PersistenceError> {
        let conn = conn!(self);
        let mut stmt = conn
            .prepare(
                "SELECT session_id, timestamp_ms, text FROM prompt_history WHERE session_id = ?1 ORDER BY timestamp_ms ASC",
            )
            .map_err(sqlite_err)?;
        let rows = stmt
            .query_map(params![session_id], |row| {
                Ok(PromptHistoryRecord {
                    session_id: row.get(0)?,
                    timestamp_ms: row.get(1)?,
                    text: row.get(2)?,
                })
            })
            .map_err(sqlite_err)?;
        let mut history = Vec::new();
        for row in rows {
            history.push(row.map_err(sqlite_err)?);
        }
        Ok(history)
    }

    fn save_compaction(&self, record: &CompactionRecord) -> Result<(), PersistenceError> {
        conn!(self)
            .execute(
                "INSERT OR REPLACE INTO compaction (session_id, count, removed_message_count, summary, timestamp_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    record.session_id,
                    record.count,
                    record.removed_message_count,
                    record.summary,
                    record.timestamp_ms,
                ],
            )
            .map_err(sqlite_err)?;
        Ok(())
    }

    fn load_compaction(&self, session_id: &str) -> Result<Option<CompactionRecord>, PersistenceError> {
        let conn = conn!(self);
        let mut stmt = conn
            .prepare(
                "SELECT session_id, count, removed_message_count, summary, timestamp_ms FROM compaction WHERE session_id = ?1",
            )
            .map_err(sqlite_err)?;
        let result = stmt
            .query_row(params![session_id], |row| {
                Ok(CompactionRecord {
                    session_id: row.get(0)?,
                    count: row.get(1)?,
                    removed_message_count: row.get::<_, i64>(2)? as usize,
                    summary: row.get(3)?,
                    timestamp_ms: row.get(4)?,
                })
            })
            .optional()
            .map_err(sqlite_err)?;
        Ok(result)
    }

    fn flush(&self) -> Result<(), PersistenceError> {
        Ok(())
    }
}
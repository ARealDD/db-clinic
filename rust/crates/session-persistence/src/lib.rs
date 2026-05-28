mod error;
mod jsonl_backend;
mod memory_backend;
#[cfg(feature = "sqlite")]
mod sqlite_backend;

pub use error::PersistenceError;
pub use jsonl_backend::JsonlBackend;
pub use memory_backend::MemoryBackend;
#[cfg(feature = "sqlite")]
pub use sqlite_backend::SqliteBackend;

use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Checkpoint {
    pub checkpoint_id: String,
    pub session_id: String,
    pub step_index: usize,
    pub timestamp_ms: u64,
    pub message_count: usize,
    pub summary: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionRecord {
    pub session_id: String,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
    pub model: Option<String>,
    pub workspace_root: Option<String>,
    pub fork_parent_id: Option<String>,
    pub fork_branch_name: Option<String>,
    #[serde(default)]
    pub username: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MessageRecord {
    pub session_id: String,
    pub role: String,
    pub content_json: String,
    pub timestamp_ms: u64,
    pub step_index: usize,
    pub usage_json: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PromptHistoryRecord {
    pub session_id: String,
    pub timestamp_ms: u64,
    pub text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompactionRecord {
    pub session_id: String,
    pub count: u32,
    pub removed_message_count: usize,
    pub summary: String,
    pub timestamp_ms: u64,
}

pub trait SessionBackend: Send + Sync {
    fn save_session_meta(&self, record: &SessionRecord) -> Result<(), PersistenceError>;
    fn load_session_meta(&self, session_id: &str) -> Result<Option<SessionRecord>, PersistenceError>;
    fn list_sessions(&self) -> Result<Vec<SessionRecord>, PersistenceError>;
    fn delete_session(&self, session_id: &str) -> Result<bool, PersistenceError>;

    fn append_message(&self, msg: &MessageRecord) -> Result<(), PersistenceError>;
    fn load_messages(&self, session_id: &str) -> Result<Vec<MessageRecord>, PersistenceError>;
    fn load_messages_from_step(&self, session_id: &str, step_index: usize) -> Result<Vec<MessageRecord>, PersistenceError>;
    fn message_count(&self, session_id: &str) -> Result<usize, PersistenceError>;

    fn save_checkpoint(&self, checkpoint: &Checkpoint) -> Result<(), PersistenceError>;
    fn load_latest_checkpoint(&self, session_id: &str) -> Result<Option<Checkpoint>, PersistenceError>;
    fn load_checkpoint_at_step(&self, session_id: &str, step_index: usize) -> Result<Option<Checkpoint>, PersistenceError>;
    fn list_checkpoints(&self, session_id: &str) -> Result<Vec<Checkpoint>, PersistenceError>;
    fn delete_checkpoints(&self, session_id: &str) -> Result<(), PersistenceError>;

    fn save_prompt_history(&self, record: &PromptHistoryRecord) -> Result<(), PersistenceError>;
    fn load_prompt_history(&self, session_id: &str) -> Result<Vec<PromptHistoryRecord>, PersistenceError>;

    fn save_compaction(&self, record: &CompactionRecord) -> Result<(), PersistenceError>;
    fn load_compaction(&self, session_id: &str) -> Result<Option<CompactionRecord>, PersistenceError>;

    fn flush(&self) -> Result<(), PersistenceError>;
}

pub fn open_backend(
    kind: &str,
    data_dir: &Path,
) -> Result<Box<dyn SessionBackend>, PersistenceError> {
    std::fs::create_dir_all(data_dir).map_err(PersistenceError::Io)?;
    match kind {
        "jsonl" => Ok(Box::new(JsonlBackend::open(data_dir)?)),
        #[cfg(feature = "sqlite")]
        "sqlite" => Ok(Box::new(SqliteBackend::open(data_dir)?)),
        #[cfg(not(feature = "sqlite"))]
        "sqlite" => Err(PersistenceError::Config(
            "sqlite backend not available (feature not enabled)".to_string(),
        )),
        "memory" => Ok(Box::new(MemoryBackend::new())),
        other => Err(PersistenceError::Config(format!(
            "unknown backend kind: {other}"
        ))),
    }
}
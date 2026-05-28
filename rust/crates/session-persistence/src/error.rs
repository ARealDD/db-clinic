use std::io;

#[derive(Debug, thiserror::Error)]
pub enum PersistenceError {
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("SQLite error: {0}")]
    Sqlite(String),
    #[error("Session not found: {0}")]
    NotFound(String),
    #[error("Config error: {0}")]
    Config(String),
    #[error("Format error: {0}")]
    Format(String),
}
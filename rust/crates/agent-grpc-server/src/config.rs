use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Default, Deserialize)]
pub struct AppConfig {
    #[serde(default)]
    pub logging: LoggingConfig,
}

#[derive(Debug, Deserialize)]
pub struct LoggingConfig {
    #[serde(default = "default_log_dir")]
    pub dir: PathBuf,
    #[serde(default = "default_grpc_prefix")]
    pub grpc_prefix: String,
    // Only the Python gateway enforces retention via TimedRotatingFileHandler.
    // The Rust side parses it so the unified config validates, but tracing-appender's
    // daily rolling does not self-prune; tracked as a known limitation in the plan.
    #[serde(default = "default_retention")]
    #[allow(dead_code)]
    pub retention_days: u32,
}

impl Default for LoggingConfig {
    fn default() -> Self {
        Self {
            dir: default_log_dir(),
            grpc_prefix: default_grpc_prefix(),
            retention_days: default_retention(),
        }
    }
}

fn default_log_dir() -> PathBuf {
    PathBuf::from("logs")
}

fn default_grpc_prefix() -> String {
    "agent-grpc-server".to_string()
}

fn default_retention() -> u32 {
    14
}

pub fn load(path: &Path) -> AppConfig {
    match std::fs::read_to_string(path) {
        Ok(text) => toml::from_str(&text).unwrap_or_else(|e| {
            eprintln!(
                "config.toml parse error at {}: {e}; using defaults",
                path.display()
            );
            AppConfig::default()
        }),
        Err(_) => AppConfig::default(),
    }
}

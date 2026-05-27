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
    // Per-turn full-prompt JSONL log. Lives in its own directory so it can be
    // tailed / grepped without competing with tracing's rolling file. Daily
    // rotation; no retention enforcement on the Rust side (same caveat as `dir`).
    #[serde(default = "default_prompt_log_dir")]
    pub prompt_log_dir: PathBuf,
    #[serde(default = "default_prompt_log_prefix_grpc")]
    pub prompt_log_prefix_grpc: String,
}

impl Default for LoggingConfig {
    fn default() -> Self {
        Self {
            dir: default_log_dir(),
            grpc_prefix: default_grpc_prefix(),
            retention_days: default_retention(),
            prompt_log_dir: default_prompt_log_dir(),
            prompt_log_prefix_grpc: default_prompt_log_prefix_grpc(),
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

fn default_prompt_log_dir() -> PathBuf {
    PathBuf::from("logs/prompts")
}

fn default_prompt_log_prefix_grpc() -> String {
    "prompt-grpc".to_string()
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

use std::fmt::{Display, Formatter};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RuntimeError {
    ApiFailure(ApiFailure),
    ToolFailure {
        tool: String,
        message: String,
    },
    SessionState(String),
    MaxIterations,
    Cancelled,
    StreamInvalid(String),
    Internal {
        source: &'static str,
        message: String,
    },
    Other(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApiFailure {
    pub class: String,
    pub retryable: bool,
    pub provider: Option<String>,
    pub status: Option<u16>,
    pub request_id: Option<String>,
    pub message: String,
}

impl RuntimeError {
    #[must_use]
    pub fn new(message: impl Into<String>) -> Self {
        Self::Other(message.into())
    }
}

impl Display for RuntimeError {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ApiFailure(api) => write!(f, "{}", api.message),
            Self::ToolFailure { tool, message } => write!(f, "tool '{tool}' failed: {message}"),
            Self::SessionState(msg) | Self::StreamInvalid(msg) | Self::Other(msg) => {
                write!(f, "{msg}")
            }
            Self::MaxIterations => {
                write!(
                    f,
                    "conversation loop exceeded the maximum number of iterations"
                )
            }
            Self::Cancelled => write!(f, "turn cancelled"),
            Self::Internal { source, message } => {
                write!(f, "internal runtime error ({source}): {message}")
            }
        }
    }
}

impl std::error::Error for RuntimeError {}

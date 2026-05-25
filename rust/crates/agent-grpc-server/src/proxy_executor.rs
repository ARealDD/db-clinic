use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::RecvTimeoutError;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use runtime::{ToolError, ToolExecutor};
use tokio::sync::mpsc as async_mpsc;

use crate::instruction_card::build_instruction_card;
use crate::proto;

const PROXY_TIMEOUT: Duration = Duration::from_mins(30);
const POLL_INTERVAL: Duration = Duration::from_millis(500);

#[derive(Debug, Clone)]
pub struct ProxyInstructionEvent {
    pub instruction_id: String,
    pub tool_use_id: String,
    pub tool_name: String,
    pub card: proto::InstructionCard,
}

#[derive(Debug, Clone)]
pub struct ProxyTimeoutEvent {
    pub instruction_id: String,
    pub tool_use_id: String,
    pub reason: String,
}

#[derive(Debug, Clone)]
pub struct ProxyResultPayload {
    pub output: String,
    pub is_error: bool,
}

pub type PendingMap = Arc<Mutex<HashMap<String, std::sync::mpsc::Sender<ProxyResultPayload>>>>;

pub struct ProxyToolExecutor {
    session_id: String,
    instruction_tx: async_mpsc::Sender<ProxyInstructionEvent>,
    timeout_tx: async_mpsc::Sender<ProxyTimeoutEvent>,
    pending: PendingMap,
    cancel_flag: Arc<AtomicBool>,
    next_seq: u64,
}

impl ProxyToolExecutor {
    pub fn new(
        session_id: String,
        instruction_tx: async_mpsc::Sender<ProxyInstructionEvent>,
        timeout_tx: async_mpsc::Sender<ProxyTimeoutEvent>,
        pending: PendingMap,
        cancel_flag: Arc<AtomicBool>,
    ) -> Self {
        Self {
            session_id,
            instruction_tx,
            timeout_tx,
            pending,
            cancel_flag,
            next_seq: 0,
        }
    }

    fn next_instruction_id(&mut self) -> String {
        self.next_seq += 1;
        format!("inst-{}-{}", self.session_id, self.next_seq)
    }
}

impl ToolExecutor for ProxyToolExecutor {
    fn execute(&mut self, tool_name: &str, input: &str) -> Result<String, ToolError> {
        if self.cancel_flag.load(Ordering::Relaxed) {
            return Err(ToolError::new("turn cancelled before proxy dispatch"));
        }

        let instruction_id = self.next_instruction_id();
        let tool_use_id = format!("{instruction_id}-toolcall");
        let card = build_instruction_card(tool_name, input);

        let (result_tx, result_rx) = std::sync::mpsc::channel::<ProxyResultPayload>();
        {
            let mut guard = self
                .pending
                .lock()
                .map_err(|e| ToolError::new(format!("pending map poisoned: {e}")))?;
            guard.insert(instruction_id.clone(), result_tx);
        }

        let event = ProxyInstructionEvent {
            instruction_id: instruction_id.clone(),
            tool_use_id: tool_use_id.clone(),
            tool_name: tool_name.to_string(),
            card,
        };
        if let Err(e) = self.instruction_tx.blocking_send(event) {
            self.remove_pending(&instruction_id);
            return Err(ToolError::new(format!(
                "failed to dispatch proxy instruction: {e}"
            )));
        }

        let deadline = Instant::now() + PROXY_TIMEOUT;
        loop {
            if self.cancel_flag.load(Ordering::Relaxed) {
                self.remove_pending(&instruction_id);
                return Err(ToolError::new("proxy tool cancelled by user"));
            }
            let now = Instant::now();
            if now >= deadline {
                self.remove_pending(&instruction_id);
                let _ = self.timeout_tx.blocking_send(ProxyTimeoutEvent {
                    instruction_id: instruction_id.clone(),
                    tool_use_id,
                    reason: "timeout".to_string(),
                });
                return Err(ToolError::new(format!(
                    "proxy tool timed out after {} seconds",
                    PROXY_TIMEOUT.as_secs()
                )));
            }
            let remaining = deadline.saturating_duration_since(now);
            let wait = remaining.min(POLL_INTERVAL);
            match result_rx.recv_timeout(wait) {
                Ok(payload) => {
                    self.remove_pending(&instruction_id);
                    if payload.is_error {
                        return Err(ToolError::new(payload.output));
                    }
                    return Ok(payload.output);
                }
                Err(RecvTimeoutError::Timeout) => (),
                Err(RecvTimeoutError::Disconnected) => {
                    self.remove_pending(&instruction_id);
                    return Err(ToolError::new("proxy result channel disconnected"));
                }
            }
        }
    }
}

impl ProxyToolExecutor {
    fn remove_pending(&self, instruction_id: &str) {
        if let Ok(mut guard) = self.pending.lock() {
            guard.remove(instruction_id);
        }
    }
}

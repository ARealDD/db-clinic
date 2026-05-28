use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use runtime::{ToolError, ToolExecutor};
use tokio::sync::{mpsc as async_mpsc, oneshot, Mutex as AsyncMutex};
use tokio_util::sync::CancellationToken;

use crate::instruction_card::build_instruction_card;
use crate::proto;

const PROXY_TIMEOUT: Duration = Duration::from_mins(30);

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

/// Pending-instruction map.
///
/// Plan ② switched this from a `std::sync::Mutex` over `std::sync::mpsc::Sender`
/// to a `tokio::sync::Mutex` over `tokio::sync::oneshot::Sender`. Reasons:
///
/// - The map is now touched from async contexts on both sides
///   (`ProxyToolExecutor::execute` awaits while holding nothing; the gateway's
///   `deliver_proxy_result` is also called from an async handler). Using a
///   sync `Mutex` would risk holding it across an `.await` if a future caller
///   forgets, and clippy will not catch every such case.
/// - `oneshot` matches the actual contract — exactly one result per instruction
///   — and gives us a `Future` we can race in `tokio::select!` instead of
///   polling with `recv_timeout`.
pub type PendingMap = Arc<AsyncMutex<HashMap<String, oneshot::Sender<ProxyResultPayload>>>>;

pub struct ProxyToolExecutor {
    session_id: String,
    instruction_tx: async_mpsc::Sender<ProxyInstructionEvent>,
    timeout_tx: async_mpsc::Sender<ProxyTimeoutEvent>,
    pending: PendingMap,
    /// Sync cancel mirror. Kept for parity with `ConversationRuntime::cancel_signal`
    /// so non-async paths (e.g. health probes, hook abort checks) can still
    /// observe the same cancel state without an `.await`.
    cancel_flag: Arc<AtomicBool>,
    /// Awaitable cancel signal — the actual mechanism we race against the
    /// long oneshot recv. Both this and `cancel_flag` are flipped together by
    /// `SessionManager::cancel_turn`.
    cancel_token: CancellationToken,
    next_seq: u64,
}

impl ProxyToolExecutor {
    pub fn new(
        session_id: String,
        instruction_tx: async_mpsc::Sender<ProxyInstructionEvent>,
        timeout_tx: async_mpsc::Sender<ProxyTimeoutEvent>,
        pending: PendingMap,
        cancel_flag: Arc<AtomicBool>,
        cancel_token: CancellationToken,
    ) -> Self {
        Self {
            session_id,
            instruction_tx,
            timeout_tx,
            pending,
            cancel_flag,
            cancel_token,
            next_seq: 0,
        }
    }

    fn next_instruction_id(&mut self) -> String {
        self.next_seq += 1;
        format!("inst-{}-{}", self.session_id, self.next_seq)
    }

    async fn remove_pending(&self, instruction_id: &str) {
        let mut guard = self.pending.lock().await;
        guard.remove(instruction_id);
    }
}

#[async_trait]
impl ToolExecutor for ProxyToolExecutor {
    async fn execute(&mut self, tool_name: &str, input: &str) -> Result<String, ToolError> {
        // Fast-path cancel check — both the sync flag and the async token must
        // agree. They are flipped together; if they disagree it means a
        // half-cancel from a misbehaving caller, but we still treat either as
        // "cancelled" to preserve the strict B1 invariant: cancel never blocks.
        if self.cancel_flag.load(Ordering::Relaxed) || self.cancel_token.is_cancelled() {
            tracing::debug!(
                session_id = %self.session_id,
                tool_name,
                "ProxyToolExecutor: cancel observed before dispatch"
            );
            return Err(ToolError::new("turn cancelled before proxy dispatch"));
        }

        let instruction_id = self.next_instruction_id();
        let tool_use_id = format!("{instruction_id}-toolcall");
        let card = build_instruction_card(tool_name, input);

        let (result_tx, result_rx) = oneshot::channel::<ProxyResultPayload>();
        {
            let mut guard = self.pending.lock().await;
            guard.insert(instruction_id.clone(), result_tx);
            tracing::debug!(
                session_id = %self.session_id,
                instruction_id = %instruction_id,
                tool_name,
                pending_after_insert = guard.len(),
                "ProxyToolExecutor: pending registered"
            );
        }

        let event = ProxyInstructionEvent {
            instruction_id: instruction_id.clone(),
            tool_use_id: tool_use_id.clone(),
            tool_name: tool_name.to_string(),
            card,
        };
        if let Err(e) = self.instruction_tx.send(event).await {
            self.remove_pending(&instruction_id).await;
            tracing::warn!(
                session_id = %self.session_id,
                instruction_id = %instruction_id,
                error = %e,
                "ProxyToolExecutor: instruction dispatch failed (subscriber gone)"
            );
            return Err(ToolError::new(format!(
                "failed to dispatch proxy instruction: {e}"
            )));
        }

        // Race three futures:
        // 1. Operator returns a result via oneshot.
        // 2. The cancel token fires (turn cancelled, or session abandoned).
        // 3. The 30-min timeout elapses.
        //
        // tokio::select! drops the losing futures cleanly; this is the whole
        // reason for going async. The previous poll-loop version held a
        // worker thread blocked on recv_timeout for the entire 30 minutes,
        // which made the cancel path fight an O(POLL_INTERVAL) latency floor
        // and burned a full OS thread per pending proxy.
        let outcome = tokio::select! {
            biased;
            () = self.cancel_token.cancelled() => {
                tracing::info!(
                    session_id = %self.session_id,
                    instruction_id = %instruction_id,
                    "ProxyToolExecutor: cancel token fired during wait"
                );
                ProxyOutcome::Cancelled
            }
            res = result_rx => match res {
                Ok(payload) => ProxyOutcome::Result(payload),
                Err(_) => {
                    tracing::warn!(
                        session_id = %self.session_id,
                        instruction_id = %instruction_id,
                        "ProxyToolExecutor: oneshot sender dropped without sending"
                    );
                    ProxyOutcome::Disconnected
                }
            },
            () = tokio::time::sleep(PROXY_TIMEOUT) => {
                tracing::warn!(
                    session_id = %self.session_id,
                    instruction_id = %instruction_id,
                    timeout_secs = PROXY_TIMEOUT.as_secs(),
                    "ProxyToolExecutor: 30-minute timeout elapsed"
                );
                ProxyOutcome::TimedOut
            }
        };

        // The pending entry may already be gone (`deliver_proxy_result` removes
        // it before sending), but call again to be safe — `remove_pending` is
        // idempotent and we'd rather double-clean than leak.
        self.remove_pending(&instruction_id).await;

        match outcome {
            ProxyOutcome::Result(payload) => {
                tracing::debug!(
                    session_id = %self.session_id,
                    instruction_id = %instruction_id,
                    is_error = payload.is_error,
                    output_len = payload.output.len(),
                    "ProxyToolExecutor: result delivered"
                );
                if payload.is_error {
                    Err(ToolError::new(payload.output))
                } else {
                    Ok(payload.output)
                }
            }
            ProxyOutcome::Cancelled => Err(ToolError::new("proxy tool cancelled by user")),
            ProxyOutcome::Disconnected => {
                Err(ToolError::new("proxy result channel disconnected"))
            }
            ProxyOutcome::TimedOut => {
                let _ = self
                    .timeout_tx
                    .send(ProxyTimeoutEvent {
                        instruction_id: instruction_id.clone(),
                        tool_use_id,
                        reason: "timeout".to_string(),
                    })
                    .await;
                Err(ToolError::new(format!(
                    "proxy tool timed out after {} seconds",
                    PROXY_TIMEOUT.as_secs()
                )))
            }
        }
    }
}

enum ProxyOutcome {
    Result(ProxyResultPayload),
    Cancelled,
    Disconnected,
    TimedOut,
}

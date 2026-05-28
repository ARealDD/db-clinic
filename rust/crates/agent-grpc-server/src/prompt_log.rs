//! Per-turn JSONL prompt log.
//!
//! Every `ApiClient::stream` invocation produces one record capturing the
//! *true* prompt the model will see — post system-prompt merge, post
//! skill-context augmentation, post fork-report prepending. The file is
//! rotated daily by `tracing_appender::rolling::daily`; we share the
//! non-blocking writer here via a process-wide `OnceLock`.
//!
//! See `python/server.py` for the gateway-side counterpart and
//! `C:\Users\zym\.claude\plans\woolly-dancing-riddle.md` for the
//! cross-side correlation contract (both rows carry `session_id`).

use std::io::Write;
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use runtime::{ApiRequest, ContentBlock, ConversationMessage, MessageRole, TurnObserver};
use serde_json::{json, Value};
use tracing_appender::non_blocking::NonBlocking;

static WRITER: OnceLock<Mutex<NonBlocking>> = OnceLock::new();

/// Install the JSONL writer. Must be called exactly once at startup; calling
/// twice silently keeps the first writer (the second `set` is dropped).
pub fn init(writer: NonBlocking) {
    let _ = WRITER.set(Mutex::new(writer));
}

/// Best-effort: serialize one prompt record and append it as a JSON line.
/// Silently no-ops if `init` was never called (e.g. unit tests) or if the
/// JSON serialization fails — a logging error must never break a turn.
fn log_request(
    request: &ApiRequest,
    model: &str,
    provider: &str,
    session_id: &str,
    turn_iteration: usize,
) {
    let Some(writer) = WRITER.get() else {
        return;
    };

    let ts_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);

    let system_text = request.system_prompt.join("\n\n");
    let messages: Vec<Value> = request.messages.iter().map(message_to_json).collect();

    let record = json!({
        "ts_ms": ts_ms,
        "side": "grpc",
        "session_id": session_id,
        "turn_iteration": turn_iteration,
        "model": model,
        "provider": provider,
        "system_segments_count": request.system_prompt.len(),
        "system_text": system_text,
        "messages": messages,
    });

    // Serialize first, then take the mutex for the shortest possible window.
    let Ok(mut line) = serde_json::to_vec(&record) else {
        return;
    };
    line.push(b'\n');

    if let Ok(mut guard) = writer.lock() {
        let _ = guard.write_all(&line);
    }
}

fn message_to_json(msg: &ConversationMessage) -> Value {
    json!({
        "role": role_to_str(msg.role),
        "blocks": msg.blocks.iter().map(block_to_json).collect::<Vec<_>>(),
    })
}

fn role_to_str(role: MessageRole) -> &'static str {
    match role {
        MessageRole::System => "system",
        MessageRole::User => "user",
        MessageRole::Assistant => "assistant",
        MessageRole::Tool => "tool",
    }
}

fn block_to_json(block: &ContentBlock) -> Value {
    match block {
        ContentBlock::Text { text } => json!({ "type": "text", "text": text }),
        ContentBlock::Thinking {
            thinking,
            signature,
        } => json!({
            "type": "thinking",
            "thinking": thinking,
            "signature": signature,
        }),
        ContentBlock::ToolUse { id, name, input } => json!({
            "type": "tool_use",
            "id": id,
            "name": name,
            "input": input,
        }),
        ContentBlock::ToolResult {
            tool_use_id,
            tool_name,
            output,
            is_error,
        } => json!({
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "output": output,
            "is_error": is_error,
        }),
    }
}

/// Zero-sized observer that delegates to [`log_request`]. One instance is
/// shared across every session via `Arc` (see `session_store.rs`).
pub struct PromptLogObserver;

impl TurnObserver for PromptLogObserver {
    fn before_stream(
        &self,
        request: &ApiRequest,
        model: &str,
        provider: &str,
        session_id: &str,
        turn_iteration: usize,
    ) {
        log_request(request, model, provider, session_id, turn_iteration);
    }
}

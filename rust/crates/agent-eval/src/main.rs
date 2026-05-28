//! agent-eval — a lightweight stdin/stdout JSON-lines binary that wraps
//! `runtime::ConversationRuntime` for evaluation workflows.
//!
//! Protocol (one JSON object per line on stdin/stdout):
//!
//! ```json
//! {"message": "...", "session_id": null | "sess-xxx"}
//! {"session_id": "sess-xxx", "text": "...", "stop_reason": "end_turn", "tool_calls": [...], "error": null}
//! {"type": "close", "session_id": "sess-xxx"}
//! ```
//!
//! Env vars: `DB_CLINIC_MODEL`, `DB_CLINIC_API_KEY`, `DB_CLINIC_PROVIDER`, `DB_CLINIC_BASE_URL`

use std::collections::HashMap;
use std::io::{self, BufRead, Write};

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

// Re-exports from the `api` crate — submodules are private; use crate-root re-exports.
use api::ProviderClient;
use api::{
    ContentBlockDelta, InputContentBlock, InputMessage, MessageRequest, OutputContentBlock,
    StreamEvent, ToolResultContentBlock,
};

// Re-exports from the `runtime` crate — accessed via crate-root re-exports.
use runtime::{
    ApiClient, ApiRequest, AssistantEvent, ContentBlock, ConversationMessage, ConversationRuntime,
    MessageRole, PermissionMode, PermissionPolicy, RuntimeError, Session, ToolError, ToolExecutor,
    TurnSummary,
};

// ---------------------------------------------------------------------------
// JSON wire protocol
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct EvalRequest {
    /// The user message for the agent.
    #[serde(default)]
    message: Option<String>,
    /// Optional existing session id. Omit/null for a new session.
    #[serde(default)]
    session_id: Option<String>,
    /// Control commands: "close"
    #[serde(rename = "type")]
    #[serde(default)]
    msg_type: Option<String>,
}

#[derive(Debug, Serialize)]
struct EvalResponse {
    session_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    tool_calls: Vec<ToolCallInfo>,
    #[serde(skip_serializing_if = "Option::is_none")]
    stop_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Debug, Serialize)]
struct ToolCallInfo {
    tool_use_id: String,
    tool_name: String,
    input: String,
}

// ---------------------------------------------------------------------------
// API client that wraps ProviderClient and implements runtime::ApiClient
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct EvalApiClient {
    client: ProviderClient,
    model: String,
}

impl EvalApiClient {
    fn from_env(model: String) -> Result<Self, String> {
        let client =
            ProviderClient::from_model_with_anthropic_auth(&model, None).map_err(|e| e.to_string())?;
        Ok(Self { client, model })
    }
}

#[async_trait]
impl ApiClient for EvalApiClient {
    async fn stream(&mut self, request: ApiRequest) -> Result<Vec<AssistantEvent>, RuntimeError> {
        let message_request = MessageRequest {
            model: self.model.clone(),
            max_tokens: api::max_tokens_for_model(&self.model),
            messages: convert_messages(&request.messages),
            system: Some(request.system_prompt.join("\n\n")),
            stream: true,
            ..Default::default()
        };

        consume_stream(&self.client, &message_request).await
    }
}

async fn consume_stream(
    client: &ProviderClient,
    message_request: &MessageRequest,
) -> Result<Vec<AssistantEvent>, RuntimeError> {
    let mut stream = client
        .stream_message(message_request)
        .await
        .map_err(|e| RuntimeError::new(format!("API error: {e}")))?;

    let mut events = Vec::new();
    let mut pending_tool: Option<(String, String, String)> = None;
    let mut saw_stop = false;

    loop {
        let next = stream
            .next_event()
            .await
            .map_err(|e| RuntimeError::new(format!("stream error: {e}")))?;

        let Some(event) = next else {
            break;
        };

        match event {
            StreamEvent::ContentBlockStart(start) => {
                match start.content_block {
                    OutputContentBlock::Text { text } => {
                        if !text.is_empty() {
                            events.push(AssistantEvent::TextDelta(text));
                        }
                    }
                    OutputContentBlock::ToolUse { id, name, input } => {
                        let initial_input = if input.is_object()
                            && input.as_object().is_some_and(serde_json::Map::is_empty)
                        {
                            String::new()
                        } else {
                            input.to_string()
                        };
                        pending_tool = Some((id, name, initial_input));
                    }
                    OutputContentBlock::Thinking { .. } | OutputContentBlock::RedactedThinking { .. } => {}
                }
            }
            StreamEvent::ContentBlockDelta(delta) => match delta.delta {
                ContentBlockDelta::TextDelta { text } => {
                    if !text.is_empty() {
                        events.push(AssistantEvent::TextDelta(text));
                    }
                }
                ContentBlockDelta::InputJsonDelta { partial_json } => {
                    if let Some((_, _, input)) = &mut pending_tool {
                        input.push_str(&partial_json);
                    }
                }
                ContentBlockDelta::ThinkingDelta { .. } | ContentBlockDelta::SignatureDelta { .. } => {}
            },
            StreamEvent::ContentBlockStop(_) => {
                if let Some((id, name, input)) = pending_tool.take() {
                    events.push(AssistantEvent::ToolUse { id, name, input });
                }
            }
            StreamEvent::MessageDelta(delta) => {
                events.push(AssistantEvent::Usage(delta.usage.token_usage()));
            }
            StreamEvent::MessageStart(_) => {}
            StreamEvent::MessageStop(_) => {
                saw_stop = true;
                events.push(AssistantEvent::MessageStop);
            }
        }
    }

    if !saw_stop {
        events.push(AssistantEvent::MessageStop);
    }

    Ok(events)
}

// ---------------------------------------------------------------------------
// Tool executor that stubs all tools for eval mode
// ---------------------------------------------------------------------------

struct EvalToolExecutor;

#[async_trait]
impl ToolExecutor for EvalToolExecutor {
    async fn execute(&mut self, tool_name: &str, input: &str) -> Result<String, ToolError> {
        Ok(format!(
            "[eval mode] tool `{tool_name}` not executed (input: {input})"
        ))
    }
}

// ---------------------------------------------------------------------------
// Message conversion (same pattern as rusty-claude-cli)
// ---------------------------------------------------------------------------

fn convert_messages(messages: &[ConversationMessage]) -> Vec<InputMessage> {
    messages
        .iter()
        .filter_map(|message| {
            let role = match message.role {
                MessageRole::System | MessageRole::User | MessageRole::Tool => "user",
                MessageRole::Assistant => "assistant",
            };
            let content = message
                .blocks
                .iter()
                .filter_map(|block| match block {
                    ContentBlock::Text { text } => {
                        Some(InputContentBlock::Text { text: text.clone() })
                    }
                    ContentBlock::Thinking { .. } => None,
                    ContentBlock::ToolUse { id, name, input } => {
                        Some(InputContentBlock::ToolUse {
                            id: id.clone(),
                            name: name.clone(),
                            input: serde_json::from_str(input)
                                .unwrap_or_else(|_| serde_json::json!({ "raw": input })),
                        })
                    }
                    ContentBlock::ToolResult {
                        tool_use_id,
                        output,
                        is_error,
                        ..
                    } => Some(InputContentBlock::ToolResult {
                        tool_use_id: tool_use_id.clone(),
                        content: vec![ToolResultContentBlock::Text {
                            text: output.clone(),
                        }],
                        is_error: *is_error,
                    }),
                })
                .collect::<Vec<_>>();
            (!content.is_empty()).then(|| InputMessage {
                role: role.to_string(),
                content,
            })
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Response builders
// ---------------------------------------------------------------------------

fn extract_text(messages: &[ConversationMessage]) -> String {
    let mut parts: Vec<&str> = Vec::new();
    for msg in messages {
        for block in &msg.blocks {
            if let ContentBlock::Text { text } = block {
                if !text.is_empty() {
                    parts.push(text);
                }
            }
        }
    }
    parts.join("\n")
}

fn extract_tool_calls(messages: &[ConversationMessage]) -> Vec<ToolCallInfo> {
    let mut calls = Vec::new();
    for msg in messages {
        for block in &msg.blocks {
            if let ContentBlock::ToolUse { id, name, input } = block {
                calls.push(ToolCallInfo {
                    tool_use_id: id.clone(),
                    tool_name: name.clone(),
                    input: input.clone(),
                });
            }
        }
    }
    calls
}

fn build_response(
    session_id: &str,
    result: Result<TurnSummary, RuntimeError>,
) -> EvalResponse {
    match result {
        Ok(summary) => {
            let text = extract_text(&summary.assistant_messages);
            let tool_calls = extract_tool_calls(&summary.assistant_messages);
            EvalResponse {
                session_id: session_id.to_string(),
                text: Some(text),
                tool_calls,
                stop_reason: Some("end_turn".to_string()),
                error: None,
            }
        }
        Err(err) => EvalResponse {
            session_id: session_id.to_string(),
            text: None,
            tool_calls: Vec::new(),
            stop_reason: None,
            error: Some(err.to_string()),
        },
    }
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let model = std::env::var("DB_CLINIC_MODEL")
        .or_else(|_| std::env::var("ANTHROPIC_MODEL"))
        .unwrap_or_else(|_| "claude-sonnet-4-6".to_string());

    // Build the API client once — it's Clone and can be shared across sessions.
    let api_client = EvalApiClient::from_env(model).unwrap_or_else(|e| {
        eprintln!("FATAL: failed to create API client: {e}");
        std::process::exit(1);
    });

    let mut sessions: HashMap<String, SessionEntry> = HashMap::new();

    let stdin = io::stdin();
    let reader = stdin.lock();
    let stdout = io::stdout();
    let mut writer = stdout.lock();

    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(e) => {
                eprintln!("stdin read error: {e}");
                break;
            }
        };

        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        let req: EvalRequest = match serde_json::from_str(trimmed) {
            Ok(r) => r,
            Err(e) => {
                let resp = EvalResponse {
                    session_id: String::new(),
                    text: None,
                    tool_calls: Vec::new(),
                    stop_reason: None,
                    error: Some(format!("invalid JSON: {e}")),
                };
                writeln!(writer, "{}", serde_json::to_string(&resp).unwrap()).ok();
                let _ = writer.flush();
                continue;
            }
        };

        // Handle close commands
        if req.msg_type.as_deref() == Some("close") {
            if let Some(sid) = &req.session_id {
                sessions.remove(sid);
            }
            continue;
        }

        let Some(message) = req.message.as_ref().filter(|m| !m.is_empty()) else {
            let resp = EvalResponse {
                session_id: req.session_id.clone().unwrap_or_default(),
                text: None,
                tool_calls: Vec::new(),
                stop_reason: None,
                error: Some("empty message".to_string()),
            };
            writeln!(writer, "{}", serde_json::to_string(&resp).unwrap()).ok();
            let _ = writer.flush();
            continue;
        };

        let session_id = req.session_id.clone().unwrap_or_else(|| Session::new().session_id);

        // Look up or create the session entry
        let entry = sessions.entry(session_id.clone()).or_insert_with(|| {
            let session = Session::new();
            let sid = session.session_id.clone();
            let tool_executor = EvalToolExecutor;
            let permission_policy = PermissionPolicy::new(PermissionMode::Allow);
            let runtime = ConversationRuntime::new(
                session,
                api_client.clone(),
                tool_executor,
                permission_policy,
                Vec::new(), // system_prompts — empty for eval
            )
            .with_max_iterations(20);
            SessionEntry {
                runtime,
                display_id: sid,
            }
        });

        let result = entry.runtime.run_turn(message, None).await;
        let resp = build_response(&entry.display_id, result);

        writeln!(writer, "{}", serde_json::to_string(&resp).unwrap()).ok();
        let _ = writer.flush();
    }
}

struct SessionEntry {
    runtime: ConversationRuntime<EvalApiClient, EvalToolExecutor>,
    display_id: String,
}

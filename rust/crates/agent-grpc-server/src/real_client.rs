use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use api::{
    AnthropicClient, ApiError, AuthSource, ContentBlockDelta, InputContentBlock, InputMessage,
    MessageRequest, OpenAiCompatClient, OpenAiCompatConfig, OutputContentBlock, ProviderClient,
    StreamEvent, ToolDefinition, ToolResultContentBlock,
};
use runtime::{
    ApiClient, ApiFailure, ApiRequest, AssistantEvent, ContentBlock, ConversationMessage,
    MessageRole, RuntimeError,
};

pub type EventSink = Arc<Mutex<Option<std::sync::mpsc::Sender<AssistantEvent>>>>;

pub struct RealApiClient {
    runtime: tokio::runtime::Runtime,
    client: ProviderClient,
    model: String,
    provider: String,
    event_sink: EventSink,
    tool_definitions: Vec<ToolDefinition>,
}

impl RealApiClient {
    pub fn new(
        provider: &str,
        api_key: &str,
        base_url: &str,
        model: &str,
        tool_definitions: Vec<ToolDefinition>,
    ) -> Result<(Self, EventSink), String> {
        let client = match provider {
            "anthropic" => {
                let auth = AuthSource::ApiKey(api_key.to_string());
                let mut c = AnthropicClient::from_auth(auth);
                if !base_url.is_empty() {
                    c = c.with_base_url(base_url);
                }
                ProviderClient::Anthropic(c)
            }
            "openai" | "custom" => {
                let config = OpenAiCompatConfig::openai();
                let mut c = OpenAiCompatClient::new(api_key, config);
                if !base_url.is_empty() {
                    c = c.with_base_url(base_url);
                }
                ProviderClient::OpenAi(c)
            }
            "xai" => {
                let config = OpenAiCompatConfig::xai();
                let mut c = OpenAiCompatClient::new(api_key, config);
                if !base_url.is_empty() {
                    c = c.with_base_url(base_url);
                }
                ProviderClient::Xai(c)
            }
            "dashscope" => {
                let config = OpenAiCompatConfig::dashscope();
                let mut c = OpenAiCompatClient::new(api_key, config);
                if !base_url.is_empty() {
                    c = c.with_base_url(base_url);
                }
                ProviderClient::OpenAi(c)
            }
            _ => return Err(format!("unknown provider: {provider}")),
        };
        let rt = tokio::runtime::Runtime::new().map_err(|e| e.to_string())?;
        let event_sink: EventSink = Arc::new(Mutex::new(None));
        let sink_clone = event_sink.clone();
        Ok((
            Self {
                runtime: rt,
                client,
                model: model.to_string(),
                provider: provider.to_string(),
                event_sink,
                tool_definitions,
            },
            sink_clone,
        ))
    }
}

/// Distill an [`api::ApiError`] into a [`runtime::ApiFailure`] so the gateway
/// can pick the right gRPC `ErrorCode` and surface the classification to the
/// UI without re-parsing the error message.
fn api_error_into_runtime(err: &ApiError, provider: &str) -> RuntimeError {
    let class = err.safe_failure_class().to_string();
    let retryable = err.is_retryable();
    let request_id = err.request_id().map(str::to_string);
    let status = match err {
        ApiError::Api { status, .. } => Some(status.as_u16()),
        ApiError::RetriesExhausted { last_error, .. } => {
            if let ApiError::Api { status, .. } = last_error.as_ref() {
                Some(status.as_u16())
            } else {
                None
            }
        }
        _ => None,
    };
    RuntimeError::ApiFailure(ApiFailure {
        class,
        retryable,
        provider: Some(provider.to_string()),
        status,
        request_id,
        message: err.to_string(),
    })
}

fn emit(sink: &EventSink, event: &AssistantEvent) {
    if let Ok(guard) = sink.lock() {
        if let Some(tx) = guard.as_ref() {
            let _ = tx.send(event.clone());
        }
    }
}

impl ApiClient for RealApiClient {
    fn model(&self) -> &str {
        &self.model
    }

    fn provider(&self) -> &str {
        &self.provider
    }

    fn stream(&mut self, request: ApiRequest) -> Result<Vec<AssistantEvent>, RuntimeError> {
        let messages = convert_messages(&request.messages);
        let system =
            (!request.system_prompt.is_empty()).then(|| request.system_prompt.join("\n\n"));
        let max_tokens = api::max_tokens_for_model(&self.model);

        let tools = (!self.tool_definitions.is_empty()).then(|| self.tool_definitions.clone());
        let message_request = MessageRequest {
            model: self.model.clone(),
            max_tokens,
            messages,
            system,
            tools,
            tool_choice: None,
            stream: true,
            ..Default::default()
        };

        self.runtime.block_on(stream_and_collect(
            &self.client,
            &message_request,
            &self.event_sink,
            &self.provider,
        ))
    }
}

#[allow(clippy::too_many_lines)]
async fn stream_and_collect(
    client: &ProviderClient,
    message_request: &MessageRequest,
    sink: &EventSink,
    provider: &str,
) -> Result<Vec<AssistantEvent>, RuntimeError> {
    let mut stream = client
        .stream_message(message_request)
        .await
        .map_err(|e| api_error_into_runtime(&e, provider))?;

    let mut events = Vec::new();
    let mut pending_tools: BTreeMap<u32, (String, String, String)> = BTreeMap::new();
    let mut pending_thinking: BTreeMap<u32, (String, Option<String>)> = BTreeMap::new();
    let mut saw_stop = false;

    while let Some(event) = stream
        .next_event()
        .await
        .map_err(|e| api_error_into_runtime(&e, provider))?
    {
        match event {
            StreamEvent::MessageStart(start) => {
                for block in start.message.content {
                    push_output_block(
                        block,
                        0,
                        &mut events,
                        &mut pending_tools,
                        &mut pending_thinking,
                        sink,
                    );
                }
            }
            StreamEvent::ContentBlockStart(start) => {
                push_output_block(
                    start.content_block,
                    start.index,
                    &mut events,
                    &mut pending_tools,
                    &mut pending_thinking,
                    sink,
                );
            }
            StreamEvent::ContentBlockDelta(delta) => match delta.delta {
                ContentBlockDelta::TextDelta { text } => {
                    if !text.is_empty() {
                        let ev = AssistantEvent::TextDelta(text);
                        emit(sink, &ev);
                        events.push(ev);
                    }
                }
                ContentBlockDelta::InputJsonDelta { partial_json } => {
                    if let Some((_, _, input)) = pending_tools.get_mut(&delta.index) {
                        input.push_str(&partial_json);
                    }
                }
                ContentBlockDelta::ThinkingDelta { thinking } => {
                    if let Some((pending, _)) = pending_thinking.get_mut(&delta.index) {
                        pending.push_str(&thinking);
                    }
                    emit(
                        sink,
                        &AssistantEvent::Thinking {
                            thinking,
                            signature: None,
                        },
                    );
                }
                ContentBlockDelta::SignatureDelta { signature } => {
                    if let Some((_, sig)) = pending_thinking.get_mut(&delta.index) {
                        sig.get_or_insert_with(String::new).push_str(&signature);
                    }
                }
            },
            StreamEvent::ContentBlockStop(stop) => {
                if let Some((thinking, signature)) = pending_thinking.remove(&stop.index) {
                    events.push(AssistantEvent::Thinking {
                        thinking,
                        signature,
                    });
                }
                if let Some((id, name, input)) = pending_tools.remove(&stop.index) {
                    events.push(AssistantEvent::ToolUse { id, name, input });
                }
            }
            StreamEvent::MessageDelta(delta) => {
                let ev = AssistantEvent::Usage(delta.usage.token_usage());
                emit(sink, &ev);
                events.push(ev);
            }
            StreamEvent::MessageStop(_) => {
                saw_stop = true;
                let ev = AssistantEvent::MessageStop;
                emit(sink, &ev);
                events.push(ev);
            }
        }
    }

    if !saw_stop
        && events.iter().any(|e| {
            matches!(e, AssistantEvent::TextDelta(text) if !text.is_empty())
                || matches!(e, AssistantEvent::ToolUse { .. })
        })
    {
        events.push(AssistantEvent::MessageStop);
    }

    Ok(events)
}

#[allow(clippy::too_many_arguments)]
fn push_output_block(
    block: OutputContentBlock,
    block_index: u32,
    events: &mut Vec<AssistantEvent>,
    pending_tools: &mut BTreeMap<u32, (String, String, String)>,
    pending_thinking: &mut BTreeMap<u32, (String, Option<String>)>,
    sink: &EventSink,
) {
    match block {
        OutputContentBlock::Text { text } => {
            if !text.is_empty() {
                let ev = AssistantEvent::TextDelta(text);
                emit(sink, &ev);
                events.push(ev);
            }
        }
        OutputContentBlock::ToolUse { id, name, input } => {
            let initial_input =
                if input.is_object() && input.as_object().is_some_and(serde_json::Map::is_empty) {
                    String::new()
                } else {
                    input.to_string()
                };
            pending_tools.insert(block_index, (id, name, initial_input));
        }
        OutputContentBlock::Thinking {
            thinking,
            signature,
        } => {
            pending_thinking.insert(block_index, (thinking, signature));
        }
        OutputContentBlock::RedactedThinking { .. } => {}
    }
}

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
                .map(|block| match block {
                    ContentBlock::Text { text } => InputContentBlock::Text { text: text.clone() },
                    ContentBlock::Thinking {
                        thinking,
                        signature,
                    } => InputContentBlock::Thinking {
                        thinking: thinking.clone(),
                        signature: signature.clone(),
                    },
                    ContentBlock::ToolUse { id, name, input } => InputContentBlock::ToolUse {
                        id: id.clone(),
                        name: name.clone(),
                        input: serde_json::from_str(input)
                            .unwrap_or_else(|_| serde_json::json!({ "raw": input })),
                    },
                    ContentBlock::ToolResult {
                        tool_use_id,
                        output,
                        is_error,
                        ..
                    } => InputContentBlock::ToolResult {
                        tool_use_id: tool_use_id.clone(),
                        content: vec![ToolResultContentBlock::Text {
                            text: output.clone(),
                        }],
                        is_error: *is_error,
                    },
                })
                .filter(
                    |block| !matches!(block, InputContentBlock::Text { text } if text.is_empty()),
                )
                .collect::<Vec<_>>();
            (!content.is_empty()).then(|| InputMessage {
                role: role.to_string(),
                content,
            })
        })
        .collect()
}

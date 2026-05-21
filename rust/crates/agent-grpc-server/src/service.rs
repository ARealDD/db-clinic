use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tonic::{Request, Response, Status, Streaming};

use crate::convert;
use crate::proto;
use crate::proto::agent_service_server::AgentService;
use crate::session_store::{ApiConfig, SessionManager};

pub struct AgentServiceImpl {
    manager: SessionManager,
    mock_mode: bool,
}

impl AgentServiceImpl {
    pub fn new(mock_mode: bool) -> Self {
        Self {
            manager: SessionManager::new(),
            mock_mode,
        }
    }
}

#[tonic::async_trait]
impl AgentService for AgentServiceImpl {
    async fn create_session(
        &self,
        request: Request<proto::CreateSessionRequest>,
    ) -> Result<Response<proto::CreateSessionResponse>, Status> {
        let req = request.into_inner();
        let model = if req.model.is_empty() {
            "mock".to_string()
        } else {
            req.model
        };

        let max_iterations = req.config.as_ref().and_then(|c| {
            let n = c.max_iterations;
            if n > 0 {
                #[allow(clippy::cast_sign_loss)]
                Some(n as usize)
            } else {
                None
            }
        });

        let api_config = req.api_config.and_then(|c| {
            if c.api_key.is_empty() {
                None
            } else {
                Some(ApiConfig {
                    provider: c.provider,
                    api_key: c.api_key,
                    base_url: c.base_url,
                })
            }
        });

        let manager = self.manager.clone();
        let mock_mode = self.mock_mode;
        let model_clone = model.clone();
        let (session_id, created_at_ms) = tokio::task::spawn_blocking(move || {
            manager.create_session(model_clone, req.system_prompts, max_iterations, api_config)
        })
        .await
        .map_err(|e| Status::internal(e.to_string()))?;

        eprintln!(
            "created session {session_id} (model={model}, mock={mock_mode})"
        );

        Ok(Response::new(proto::CreateSessionResponse {
            session_id,
            created_at_ms,
        }))
    }

    type ChatStream = ReceiverStream<Result<proto::ChatOutput, Status>>;

    async fn chat(
        &self,
        request: Request<Streaming<proto::ChatInput>>,
    ) -> Result<Response<Self::ChatStream>, Status> {
        let mut inbound = request.into_inner();
        let manager = self.manager.clone();
        let (tx, rx) = mpsc::channel(64);

        tokio::spawn(async move {
            while let Ok(Some(input)) = inbound.message().await {
                let session_id = input.session_id.clone();

                let Some(payload) = input.payload else {
                    let _ = tx
                        .send(Ok(proto::ChatOutput {
                            session_id: session_id.clone(),
                            payload: Some(proto::chat_output::Payload::Error(
                                proto::ErrorEvent {
                                    code: proto::ErrorCode::ErrorInternal.into(),
                                    message: "empty payload in ChatInput".to_string(),
                                    recoverable: true,
                                },
                            )),
                        }))
                        .await;
                    continue;
                };

                match payload {
                    proto::chat_input::Payload::UserMessage(user_msg) => {
                        handle_user_message(&manager, &tx, &session_id, &user_msg).await;
                    }
                    proto::chat_input::Payload::ProxyResult(_proxy_result) => {
                        let _ = tx
                            .send(Ok(proto::ChatOutput {
                                session_id,
                                payload: Some(proto::chat_output::Payload::Error(
                                    proto::ErrorEvent {
                                        code: proto::ErrorCode::Unspecified.into(),
                                        message: "proxy result handling not yet implemented"
                                            .to_string(),
                                        recoverable: true,
                                    },
                                )),
                            }))
                            .await;
                    }
                    proto::chat_input::Payload::Cancel(_) => {
                        let _ = tx
                            .send(Ok(proto::ChatOutput {
                                session_id,
                                payload: Some(proto::chat_output::Payload::TurnComplete(
                                    proto::TurnComplete {
                                        messages: vec![],
                                        turn_usage: None,
                                        stop_reason: proto::TurnStopReason::TurnStopCancelled
                                            .into(),
                                    },
                                )),
                            }))
                            .await;
                        break;
                    }
                }
            }
        });

        Ok(Response::new(ReceiverStream::new(rx)))
    }

    async fn close_session(
        &self,
        request: Request<proto::CloseSessionRequest>,
    ) -> Result<Response<proto::CloseSessionResponse>, Status> {
        let session_id = request.into_inner().session_id;
        let manager = self.manager.clone();
        let sid = session_id.clone();
        let usage = tokio::task::spawn_blocking(move || {
            let u = manager.get_usage(&sid);
            manager.remove_session(&sid);
            u
        })
        .await
        .map_err(|e| Status::internal(e.to_string()))?;

        eprintln!("closed session {session_id}");

        Ok(Response::new(proto::CloseSessionResponse {
            total_usage: usage.map(|u| convert::runtime_usage_to_proto(&u)),
        }))
    }

    async fn health_check(
        &self,
        _request: Request<proto::HealthCheckRequest>,
    ) -> Result<Response<proto::HealthCheckResponse>, Status> {
        Ok(Response::new(proto::HealthCheckResponse {
            status: proto::ServiceStatus::Serving.into(),
            active_sessions: u32::try_from(self.manager.active_count()).unwrap_or(u32::MAX),
            uptime_seconds: self.manager.uptime_seconds(),
            version: env!("CARGO_PKG_VERSION").to_string(),
        }))
    }
}

#[allow(clippy::too_many_lines)]
async fn handle_user_message(
    manager: &SessionManager,
    tx: &mpsc::Sender<Result<proto::ChatOutput, Status>>,
    session_id: &str,
    user_msg: &proto::UserMessage,
) {
    let user_text = user_msg.content.clone();
    let mgr = manager.clone();
    let sid = session_id.to_string();
    let sid_for_stream = session_id.to_string();
    let tx_for_stream = tx.clone();

    let (event_tx, event_rx) = std::sync::mpsc::channel::<runtime::AssistantEvent>();

    let stream_forwarder = tokio::spawn(async move {
        let loop_handle = tokio::runtime::Handle::current();
        loop_handle
            .spawn_blocking(move || {
                while let Ok(event) = event_rx.recv() {
                    let payload = match &event {
                        runtime::AssistantEvent::TextDelta(text) => {
                            Some(proto::chat_output::Payload::TextDelta(proto::TextDelta {
                                content: text.clone(),
                            }))
                        }
                        runtime::AssistantEvent::Thinking {
                            thinking,
                            signature: _,
                        } => Some(proto::chat_output::Payload::ThinkingDelta(
                            proto::ThinkingDelta {
                                content: thinking.clone(),
                            },
                        )),
                        runtime::AssistantEvent::Usage(usage) => {
                            Some(proto::chat_output::Payload::UsageUpdate(
                                proto::TokenUsage {
                                    input_tokens: usage.input_tokens,
                                    output_tokens: usage.output_tokens,
                                    cache_creation_input_tokens: usage
                                        .cache_creation_input_tokens,
                                    cache_read_input_tokens: usage.cache_read_input_tokens,
                                },
                            ))
                        }
                        runtime::AssistantEvent::MessageStop
                        | runtime::AssistantEvent::ToolUse { .. }
                        | runtime::AssistantEvent::PromptCache(_) => None,
                    };
                    if let Some(p) = payload {
                        let msg = Ok(proto::ChatOutput {
                            session_id: sid_for_stream.clone(),
                            payload: Some(p),
                        });
                        let _ = tx_for_stream.blocking_send(msg);
                    }
                }
            })
            .await
    });

    let turn_result =
        tokio::task::spawn_blocking(move || mgr.run_turn_streaming(&sid, &user_text, event_tx))
            .await
            .unwrap_or(None);

    let _ = stream_forwarder.await;

    let Some(turn_result) = turn_result else {
        let _ = tx
            .send(Ok(proto::ChatOutput {
                session_id: session_id.to_string(),
                payload: Some(proto::chat_output::Payload::Error(proto::ErrorEvent {
                    code: proto::ErrorCode::ErrorSessionNotFound.into(),
                    message: format!("session {session_id} not found"),
                    recoverable: false,
                })),
            }))
            .await;
        return;
    };

    match turn_result {
        Ok(summary) => {
            for msg in &summary.tool_results {
                for block in &msg.blocks {
                    if let runtime::ContentBlock::ToolResult {
                        tool_use_id,
                        tool_name,
                        output,
                        is_error,
                    } = block
                    {
                        let _ = tx
                            .send(Ok(proto::ChatOutput {
                                session_id: session_id.to_string(),
                                payload: Some(proto::chat_output::Payload::ToolExecution(
                                    proto::ToolExecution {
                                        tool_use_id: tool_use_id.clone(),
                                        tool_name: tool_name.clone(),
                                        input_json: String::new(),
                                        status: if *is_error {
                                            proto::ToolExecutionStatus::ToolExecutionFailed.into()
                                        } else {
                                            proto::ToolExecutionStatus::ToolExecutionCompleted
                                                .into()
                                        },
                                        output: output.clone(),
                                        is_error: *is_error,
                                        duration_ms: 0,
                                    },
                                )),
                            }))
                            .await;
                    }
                }
            }

            let turn_complete = convert::turn_summary_to_turn_complete(&summary);
            let _ = tx
                .send(Ok(proto::ChatOutput {
                    session_id: session_id.to_string(),
                    payload: Some(proto::chat_output::Payload::TurnComplete(turn_complete)),
                }))
                .await;
        }
        Err(err) => {
            let _ = tx
                .send(Ok(proto::ChatOutput {
                    session_id: session_id.to_string(),
                    payload: Some(proto::chat_output::Payload::Error(proto::ErrorEvent {
                        code: proto::ErrorCode::ErrorLlmApiFailure.into(),
                        message: err.to_string(),
                        recoverable: true,
                    })),
                }))
                .await;
        }
    }
}

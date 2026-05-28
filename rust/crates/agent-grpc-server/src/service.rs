use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tonic::{Request, Response, Status, Streaming};

use crate::convert;
use crate::proto;
use crate::proto::agent_service_server::AgentService;
use crate::proxy_executor::{ProxyInstructionEvent, ProxyTimeoutEvent};
use crate::session_store::{ApiConfig, ProxyChannels, SessionManager};
use crate::skill_engine::SkillEngine;
use session_persistence::SessionBackend;

type ProxyReceivers = (
    mpsc::Receiver<ProxyInstructionEvent>,
    mpsc::Receiver<ProxyTimeoutEvent>,
);

/// Sentinel `ContextAttachment.source` value set by the frontend whenever the
/// user has reviewed the skill picker — even if they unchecked everything.
/// Its presence (regardless of whether any `SELECTED_SKILL_SOURCE` rows
/// follow) tells the kernel "do NOT run auto-top-3, the operator already
/// decided."
const SKILL_SELECTION_MARKER: &str = "skill_selection";
/// `ContextAttachment.source` for each individual selected skill row. The
/// attachment's `content` is the skill id.
const SELECTED_SKILL_SOURCE: &str = "selected_skill";
/// `ContextAttachment.source` set by the frontend when an operator absorbs a
/// fork pane's final answer back into its parent session. `content` holds the
/// fork's last assistant text; `metadata["task"]` carries the fork's original
/// one-line task description so the parent LLM can frame the report. We
/// prepend a synthesized `[Fork report — task: ...]` block to the `user_text`
/// before running the parent's next turn — see `handle_user_message`.
const FORK_SUMMARY_SOURCE: &str = "fork_summary";

pub struct AgentServiceImpl {
    manager: SessionManager,
    mock_mode: bool,
    pending_proxy_receivers: Arc<Mutex<HashMap<String, ProxyReceivers>>>,
}

impl AgentServiceImpl {
    pub fn new(
        mock_mode: bool,
        skills_root: Option<PathBuf>,
        backend: Arc<dyn SessionBackend>,
    ) -> Self {
        let skill_engine = if let Some(root) = skills_root {
            SkillEngine::load(&root)
        } else {
            SkillEngine::load(&PathBuf::from("skills"))
        };
        Self {
            manager: SessionManager::new(skill_engine, backend),
            mock_mode,
            pending_proxy_receivers: Arc::new(Mutex::new(HashMap::new())),
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

        let (instruction_tx, instruction_rx) = mpsc::channel::<ProxyInstructionEvent>(32);
        let (timeout_tx, timeout_rx) = mpsc::channel::<ProxyTimeoutEvent>(8);
        let proxy_channels = ProxyChannels {
            instruction_tx,
            timeout_tx,
        };

        let manager = self.manager.clone();
        let mock_mode = self.mock_mode;
        let model_clone = model.clone();
        let (session_id, created_at_ms) = manager
            .create_session(
                model_clone,
                req.system_prompts,
                max_iterations,
                api_config,
                proxy_channels,
                if req.data_dir.is_empty() {
                    None
                } else {
                    Some(req.data_dir)
                },
                if req.user_id.is_empty() {
                    None
                } else {
                    Some(req.user_id)
                },
            )
            .await;

        if let Ok(mut guard) = self.pending_proxy_receivers.lock() {
            guard.insert(session_id.clone(), (instruction_rx, timeout_rx));
        }

        tracing::info!(%session_id, %model, mock_mode, "created session");

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
        let pending_receivers = self.pending_proxy_receivers.clone();
        let (tx, rx) = mpsc::channel(64);

        tokio::spawn(async move {
            let mut forwarders_started: HashMap<String, ()> = HashMap::new();

            while let Ok(Some(input)) = inbound.message().await {
                let session_id = input.session_id.clone();

                if !forwarders_started.contains_key(&session_id) {
                    let receivers = pending_receivers
                        .lock()
                        .ok()
                        .and_then(|mut g| g.remove(&session_id));
                    if let Some((instr_rx, timeout_rx)) = receivers {
                        spawn_forwarders(session_id.clone(), tx.clone(), instr_rx, timeout_rx);
                        forwarders_started.insert(session_id.clone(), ());
                    }
                }

                let Some(payload) = input.payload else {
                    let _ = tx
                        .send(Ok(proto::ChatOutput {
                            session_id: session_id.clone(),
                            payload: Some(proto::chat_output::Payload::Error(proto::ErrorEvent {
                                code: proto::ErrorCode::ErrorInternal.into(),
                                message: "empty payload in ChatInput".to_string(),
                                recoverable: true,
                                failure_class: String::new(),
                                request_id: String::new(),
                                provider_status: 0,
                            })),
                        }))
                        .await;
                    continue;
                };

                match payload {
                    proto::chat_input::Payload::UserMessage(user_msg) => {
                        // Spawn the turn so the inbound loop keeps reading. If
                        // we awaited here, a `ProxyResult` message sent by the
                        // client *during* the turn would never be polled —
                        // deadlocking the proxy-tool wait inside `run_turn`.
                        let manager = manager.clone();
                        let tx = tx.clone();
                        let session_id = session_id.clone();
                        tokio::spawn(async move {
                            handle_user_message(&manager, &tx, &session_id, &user_msg).await;
                        });
                    }
                    proto::chat_input::Payload::ProxyResult(proxy_result) => {
                        let manager_clone = manager.clone();
                        let sid = session_id.clone();
                        let instruction_id = proxy_result.instruction_id.clone();
                        let output = proxy_result.output.clone();
                        let is_error = proxy_result.is_error;
                        let delivered = manager_clone
                            .deliver_proxy_result(&sid, &instruction_id, output, is_error)
                            .await;
                        if !delivered {
                            let _ = tx
                                .send(Ok(proto::ChatOutput {
                                    session_id: session_id.clone(),
                                    payload: Some(proto::chat_output::Payload::Error(
                                        proto::ErrorEvent {
                                            code: proto::ErrorCode::ErrorInternal.into(),
                                            message: format!(
                                                "no pending proxy instruction `{}` for session `{}`",
                                                proxy_result.instruction_id, session_id,
                                            ),
                                            recoverable: true,
                                            failure_class: String::new(),
                                            request_id: String::new(),
                                            provider_status: 0,
                                        },
                                    )),
                                }))
                                .await;
                        }
                    }
                    proto::chat_input::Payload::Cancel(_) => {
                        manager.cancel_turn(&session_id);
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
        if let Ok(mut guard) = self.pending_proxy_receivers.lock() {
            guard.remove(&session_id);
        }
        // Cancel any in-flight turn *before* queueing commands. The session-
        // manager thread may be blocked inside a proxy wait (up to 30 min); the
        // cancel path flips an atomic via the shared map and bypasses
        // `cmd_tx`, letting the runtime abort within one poll tick (~500 ms).
        // Without this, the queued `GetUsage`/`Remove` below would back up
        // behind the stuck turn — and any concurrent `Create` from a refreshed
        // browser tab would queue behind those, freezing new sessions too.
        self.manager.cancel_turn(&session_id);
        let manager = self.manager.clone();
        let sid = session_id.clone();
        let usage = manager.get_usage(&sid).await;
        manager.remove_session(&sid).await;

        tracing::info!(%session_id, "closed session");

        Ok(Response::new(proto::CloseSessionResponse {
            total_usage: usage.map(|u| convert::runtime_usage_to_proto(&u)),
        }))
    }

    async fn resume_session(
        &self,
        request: Request<proto::ResumeSessionRequest>,
    ) -> Result<Response<proto::ResumeSessionResponse>, Status> {
        let req = request.into_inner();
        let session_id = req.session_id;
        let data_dir = req.data_dir;
        let user_id = req.user_id;
        if data_dir.is_empty() {
            return Err(Status::invalid_argument("data_dir is required"));
        }

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

        let (instruction_tx, instruction_rx) = mpsc::channel::<ProxyInstructionEvent>(32);
        let (timeout_tx, timeout_rx) = mpsc::channel::<ProxyTimeoutEvent>(8);
        let proxy_channels = ProxyChannels {
            instruction_tx,
            timeout_tx,
        };

        let manager = self.manager.clone();
        let result = manager
            .resume_session(
                &session_id,
                &data_dir,
                &user_id,
                model,
                req.system_prompts,
                max_iterations,
                api_config,
                proxy_channels,
            )
            .await;

        let (sid, created_at_ms, loaded_messages) = result.map_err(Status::internal)?;

        if let Ok(mut guard) = self.pending_proxy_receivers.lock() {
            guard.insert(sid.clone(), (instruction_rx, timeout_rx));
        }

        eprintln!("resumed session {sid} (loaded {loaded_messages} messages)");

        Ok(Response::new(proto::ResumeSessionResponse {
            session_id: sid,
            created_at_ms,
            loaded_messages: loaded_messages as u32,
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

    async fn preview_skills(
        &self,
        request: Request<proto::PreviewSkillsRequest>,
    ) -> Result<Response<proto::PreviewSkillsResponse>, Status> {
        let req = request.into_inner();
        let text = req.text;
        let top_k = if req.top_k == 0 {
            8
        } else {
            req.top_k as usize
        };
        let engine = Arc::clone(self.manager.skill_engine());
        let matched = tokio::task::spawn_blocking(move || engine.match_skills(&text, top_k))
            .await
            .map_err(|e| Status::internal(e.to_string()))?;
        Ok(Response::new(proto::PreviewSkillsResponse {
            matches: matched.iter().map(matched_skill_to_proto).collect(),
        }))
    }
}

fn matched_skill_to_proto(m: &crate::skill_engine::MatchedSkill) -> proto::MatchedSkillInfo {
    proto::MatchedSkillInfo {
        skill_id: m.id.clone(),
        skill_name: m.name.clone(),
        category: m.category.clone(),
        score: m.score,
        skill_type: m.skill_type.as_str().to_string(),
        description: m.description.clone(),
    }
}

fn spawn_forwarders(
    session_id: String,
    tx: mpsc::Sender<Result<proto::ChatOutput, Status>>,
    mut instr_rx: mpsc::Receiver<ProxyInstructionEvent>,
    mut timeout_rx: mpsc::Receiver<ProxyTimeoutEvent>,
) {
    let instr_tx = tx.clone();
    let instr_sid = session_id.clone();
    tokio::spawn(async move {
        while let Some(event) = instr_rx.recv().await {
            let msg = proto::ChatOutput {
                session_id: instr_sid.clone(),
                payload: Some(proto::chat_output::Payload::ProxyInstruction(
                    proto::ProxyToolInstruction {
                        instruction_id: event.instruction_id,
                        tool_use_id: event.tool_use_id,
                        tool_name: event.tool_name,
                        card: Some(event.card),
                    },
                )),
            };
            if instr_tx.send(Ok(msg)).await.is_err() {
                break;
            }
        }
    });

    let timeout_tx = tx;
    tokio::spawn(async move {
        while let Some(event) = timeout_rx.recv().await {
            let msg = proto::ChatOutput {
                session_id: session_id.clone(),
                payload: Some(proto::chat_output::Payload::ProxyInstructionExpired(
                    proto::ProxyInstructionExpired {
                        instruction_id: event.instruction_id,
                        tool_use_id: event.tool_use_id,
                        reason: event.reason,
                    },
                )),
            };
            if timeout_tx.send(Ok(msg)).await.is_err() {
                break;
            }
        }
    });
}

#[allow(clippy::too_many_lines)]
async fn handle_user_message(
    manager: &SessionManager,
    tx: &mpsc::Sender<Result<proto::ChatOutput, Status>>,
    session_id: &str,
    user_msg: &proto::UserMessage,
) {
    // Fork absorb path: when the operator clicks "Send result to parent" on a
    // fork pane, the frontend posts a new user_message carrying the fork's
    // final answer as a `fork_summary` ContextAttachment. We splice it in
    // front of whatever the operator also typed (usually empty) so the parent
    // LLM sees the fork report as fresh context for its next turn.
    let fork_notes: Vec<String> = user_msg
        .context
        .iter()
        .filter(|a| a.source == FORK_SUMMARY_SOURCE)
        .map(|a| {
            let task = a
                .metadata
                .get("task")
                .map_or("(no task description)", String::as_str);
            format!(
                "[Fork report — task: {task}]\n{}\n[End fork report]",
                a.content
            )
        })
        .collect();

    let user_text = if fork_notes.is_empty() {
        user_msg.content.clone()
    } else if user_msg.content.trim().is_empty() {
        fork_notes.join("\n\n")
    } else {
        format!(
            "{}\n\n---\n\nOperator note:\n{}",
            fork_notes.join("\n\n"),
            user_msg.content
        )
    };
    let skill_engine = Arc::clone(manager.skill_engine());

    // UI path: the frontend has already shown a picker and recorded the
    // operator's choice as `ContextAttachment`s on the user_message. CLI path:
    // no marker → fall back to legacy auto-top-3 so rusty-claude-cli stays
    // unchanged.
    let has_selection_marker = user_msg
        .context
        .iter()
        .any(|a| a.source == SKILL_SELECTION_MARKER);

    let matched = if has_selection_marker {
        let ids: Vec<String> = user_msg
            .context
            .iter()
            .filter(|a| a.source == SELECTED_SKILL_SOURCE)
            .map(|a| a.content.clone())
            .collect();
        if ids.is_empty() {
            // Explicit "zero skills" — user reviewed and dismissed all matches.
            Vec::new()
        } else {
            tokio::task::spawn_blocking(move || {
                let refs: Vec<&str> = ids.iter().map(String::as_str).collect();
                skill_engine.select_by_ids(&refs)
            })
            .await
            .unwrap_or_default()
        }
    } else {
        let user_text_for_match = user_text.clone();
        tokio::task::spawn_blocking(move || skill_engine.match_skills(&user_text_for_match, 3))
            .await
            .unwrap_or_default()
    };

    let skill_context = if matched.is_empty() {
        None
    } else {
        let proto_skills: Vec<proto::MatchedSkillInfo> =
            matched.iter().map(matched_skill_to_proto).collect();

        let _ = tx
            .send(Ok(proto::ChatOutput {
                session_id: session_id.to_string(),
                payload: Some(proto::chat_output::Payload::SkillMatch(proto::SkillMatch {
                    skills: proto_skills,
                })),
            }))
            .await;

        let engine = Arc::clone(manager.skill_engine());
        Some(engine.build_context(&matched))
    };

    let mgr = manager.clone();
    let sid = session_id.to_string();
    let sid_for_stream = session_id.to_string();
    let tx_for_stream = tx.clone();

    // Tokio mpsc instead of std mpsc — Plan ② (B2). The runtime now drives
    // event emission through `EventSink::try_send` directly into this channel,
    // and we forward each event to the gRPC client in an async task. No more
    // sync→async bridge via `spawn_blocking`.
    let (event_tx, mut event_rx) =
        tokio::sync::mpsc::channel::<runtime::AssistantEvent>(256);

    let stream_forwarder = tokio::spawn(async move {
        while let Some(event) = event_rx.recv().await {
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
                runtime::AssistantEvent::Usage(usage) => Some(
                    proto::chat_output::Payload::UsageUpdate(proto::TokenUsage {
                        input_tokens: usage.input_tokens,
                        output_tokens: usage.output_tokens,
                        cache_creation_input_tokens: usage.cache_creation_input_tokens,
                        cache_read_input_tokens: usage.cache_read_input_tokens,
                    }),
                ),
                // Emit a "started" tool_execution the moment the LLM
                // requests the call so the operator-facing sidebar
                // updates in real time. The corresponding "completed"
                // / "failed" event is fired after the turn finishes
                // (see post-turn loop below), and the React reducer
                // upgrades the same tool_use_id entry in place.
                runtime::AssistantEvent::ToolUse { id, name, input } => {
                    Some(proto::chat_output::Payload::ToolExecution(
                        proto::ToolExecution {
                            tool_use_id: id.clone(),
                            tool_name: name.clone(),
                            input_json: input.clone(),
                            status: proto::ToolExecutionStatus::ToolExecutionStarted
                                .into(),
                            output: String::new(),
                            is_error: false,
                            duration_ms: 0,
                        },
                    ))
                }
                runtime::AssistantEvent::MessageStop
                | runtime::AssistantEvent::PromptCache(_) => None,
            };
            if let Some(p) = payload {
                let msg = Ok(proto::ChatOutput {
                    session_id: sid_for_stream.clone(),
                    payload: Some(p),
                });
                if tx_for_stream.send(msg).await.is_err() {
                    // Outbound stream closed — stop forwarding to avoid
                    // building up a backlog if the client went away.
                    break;
                }
            }
        }
    });

    let turn_result = mgr
        .run_turn_streaming(&sid, &user_text, skill_context, event_tx)
        .await;

    let _ = stream_forwarder.await;

    let Some(turn_result) = turn_result else {
        let _ = tx
            .send(Ok(proto::ChatOutput {
                session_id: session_id.to_string(),
                payload: Some(proto::chat_output::Payload::Error(proto::ErrorEvent {
                    code: proto::ErrorCode::ErrorSessionNotFound.into(),
                    message: format!("session {session_id} not found"),
                    recoverable: false,
                    failure_class: String::new(),
                    request_id: String::new(),
                    provider_status: 0,
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
            let error_event = runtime_error_to_event(&err);
            let _ = tx
                .send(Ok(proto::ChatOutput {
                    session_id: session_id.to_string(),
                    payload: Some(proto::chat_output::Payload::Error(error_event)),
                }))
                .await;
        }
    }
}

fn runtime_error_to_event(err: &runtime::RuntimeError) -> proto::ErrorEvent {
    use runtime::RuntimeError;
    let (code, recoverable, failure_class, request_id, provider_status) = match err {
        RuntimeError::ApiFailure(api) => {
            let class = api.class.as_str();
            let code = match class {
                "context_window" => proto::ErrorCode::ErrorContextTooLong,
                "provider_rate_limit" => proto::ErrorCode::ErrorLlmRateLimited,
                _ => proto::ErrorCode::ErrorLlmApiFailure,
            };
            let recoverable = match class {
                "provider_auth" => false,
                _ => api.retryable || class == "context_window",
            };
            (
                code,
                recoverable,
                api.class.clone(),
                api.request_id.clone().unwrap_or_default(),
                u32::from(api.status.unwrap_or(0)),
            )
        }
        RuntimeError::ToolFailure { .. } => (
            proto::ErrorCode::ErrorToolExecutionFailed,
            true,
            String::new(),
            String::new(),
            0,
        ),
        RuntimeError::SessionState(_) | RuntimeError::Internal { .. } => (
            proto::ErrorCode::ErrorInternal,
            false,
            String::new(),
            String::new(),
            0,
        ),
        RuntimeError::MaxIterations | RuntimeError::Cancelled | RuntimeError::Other(_) => (
            proto::ErrorCode::ErrorInternal,
            true,
            String::new(),
            String::new(),
            0,
        ),
        RuntimeError::StreamInvalid(_) => (
            proto::ErrorCode::ErrorLlmApiFailure,
            true,
            String::new(),
            String::new(),
            0,
        ),
    };
    proto::ErrorEvent {
        code: code.into(),
        message: err.to_string(),
        recoverable,
        failure_class,
        request_id,
        provider_status,
    }
}

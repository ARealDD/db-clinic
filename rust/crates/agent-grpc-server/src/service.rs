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
    /// Holds the receiver ends of per-session proxy channels until the
    /// matching `Chat` RPC connects and starts forwarding events to the
    /// client.
    pending_proxy_receivers: Arc<Mutex<HashMap<String, ProxyReceivers>>>,
}

impl AgentServiceImpl {
    pub fn new(mock_mode: bool, skills_root: Option<PathBuf>) -> Self {
        let skill_engine = if let Some(root) = skills_root {
            SkillEngine::load(&root)
        } else {
            SkillEngine::load(&PathBuf::from("skills"))
        };
        Self {
            manager: SessionManager::new(skill_engine),
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
        let (session_id, created_at_ms) = tokio::task::spawn_blocking(move || {
            manager.create_session(
                model_clone,
                req.system_prompts,
                max_iterations,
                api_config,
                proxy_channels,
            )
        })
        .await
        .map_err(|e| Status::internal(e.to_string()))?;

        if let Ok(mut guard) = self.pending_proxy_receivers.lock() {
            guard.insert(session_id.clone(), (instruction_rx, timeout_rx));
        }

        eprintln!("created session {session_id} (model={model}, mock={mock_mode})");

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
                        let delivered = tokio::task::spawn_blocking(move || {
                            manager_clone.deliver_proxy_result(
                                &sid,
                                &instruction_id,
                                output,
                                is_error,
                            )
                        })
                        .await
                        .unwrap_or(false);
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
                                        },
                                    )),
                                }))
                                .await;
                        }
                    }
                    proto::chat_input::Payload::Cancel(_) => {
                        let manager_clone = manager.clone();
                        let sid = session_id.clone();
                        let _ =
                            tokio::task::spawn_blocking(move || manager_clone.cancel_turn(&sid))
                                .await;
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
                        runtime::AssistantEvent::Usage(usage) => Some(
                            proto::chat_output::Payload::UsageUpdate(proto::TokenUsage {
                                input_tokens: usage.input_tokens,
                                output_tokens: usage.output_tokens,
                                cache_creation_input_tokens: usage.cache_creation_input_tokens,
                                cache_read_input_tokens: usage.cache_read_input_tokens,
                            }),
                        ),
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

    let turn_result = tokio::task::spawn_blocking(move || {
        mgr.run_turn_streaming(&sid, &user_text, skill_context, event_tx)
    })
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

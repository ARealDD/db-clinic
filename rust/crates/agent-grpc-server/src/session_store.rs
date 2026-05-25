use std::collections::{BTreeSet, HashMap};
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};

use runtime::{ApiClient, ApiRequest, AssistantEvent, RuntimeError};

use crate::composite_executor::CompositeToolExecutor;
use crate::local_executor::LocalToolExecutor;
use crate::mock::MockApiClient;
use crate::proxy_executor::{
    PendingMap, ProxyInstructionEvent, ProxyResultPayload, ProxyTimeoutEvent, ProxyToolExecutor,
};
use crate::real_client::{EventSink, RealApiClient};
use crate::skill_engine::SkillEngine;

/// Tools advertised to the LLM. `bash` is included so the model knows it can
/// request shell execution, but the call is intercepted by `ProxyToolExecutor`
/// (see `composite_executor::proxy_tool_names`) before reaching the local
/// executor — so `bash` runs in the operator's environment, never on the
/// server. The remaining entries route to `LocalToolExecutor`.
const ALLOWED_TOOLS: &[&str] = &[
    "bash",
    "read_file",
    "write_file",
    "edit_file",
    "glob_search",
    "grep_search",
];

/// Appended to every session's `system_prompts` so the LLM picks the right tool
/// and gives the operator enough context to approve or reject `bash` calls.
const TOOL_USAGE_GUIDANCE: &str = r"# Tool usage

- For reading file contents, prefer `read_file` over `bash cat ...` — `read_file` runs locally and returns content immediately; `bash` is dispatched to a human operator and may be rejected.
- For globbing or grepping the workspace, prefer `glob_search` / `grep_search` over `bash find` / `bash grep`.
- `bash` is for shell actions that have no local-tool equivalent (querying databases, inspecting running processes, calling external services, etc.). Every `bash` call is shown to a human operator as an instruction card before it runs.
- When you call `bash`, you MUST set the `description` input field to a short, plain-language statement of WHAT YOU ARE TRYING TO LEARN or ACCOMPLISH (not just a paraphrase of the command). The operator reads this to decide whether to run the command, suggest a different command, or reject the request entirely. A weak or missing description makes rejection more likely.
- If the operator returns `is_error=true` with a text message instead of command output, treat the message as feedback or guidance and adjust your approach — do not retry the same command.";

fn build_tool_definitions() -> Vec<api::ToolDefinition> {
    let registry = tools::GlobalToolRegistry::builtin();
    let allow_set: BTreeSet<String> = ALLOWED_TOOLS.iter().map(|s| (*s).to_string()).collect();
    registry.definitions(Some(&allow_set))
}

pub enum AnyApiClient {
    Mock(MockApiClient),
    Real(Box<RealApiClient>),
}

impl ApiClient for AnyApiClient {
    fn stream(&mut self, request: ApiRequest) -> Result<Vec<AssistantEvent>, RuntimeError> {
        match self {
            Self::Mock(c) => c.stream(request),
            Self::Real(c) => c.stream(request),
        }
    }
}

type AnyRuntime = runtime::ConversationRuntime<AnyApiClient, CompositeToolExecutor>;

pub struct SessionEntry {
    pub runtime: AnyRuntime,
    pub event_sink: Option<EventSink>,
    /// Held so a turn can reset the flag before starting. External callers
    /// (e.g. `cancel_turn`) reach the same `Arc` through `SharedSessionState`.
    pub cancel_flag: Arc<AtomicBool>,
}

struct InnerStore {
    sessions: HashMap<String, SessionEntry>,
}

/// Per-session state that must remain reachable from threads other than the
/// session-manager actor — namely the gRPC handler delivering proxy results or
/// asking for a cancel while the manager is mid-turn (and therefore unable to
/// drain its own command channel).
#[derive(Clone)]
struct SharedSessionState {
    pending_proxy: PendingMap,
    cancel_flag: Arc<AtomicBool>,
}

type SharedSessions = Arc<Mutex<HashMap<String, SharedSessionState>>>;

#[derive(Clone)]
pub struct SessionManager {
    cmd_tx: std::sync::mpsc::Sender<SessionCmd>,
    active_count: Arc<std::sync::atomic::AtomicUsize>,
    start_time: std::time::Instant,
    skill_engine: Arc<SkillEngine>,
    shared: SharedSessions,
}

type CmdResult<T> = std::sync::mpsc::Sender<T>;

#[derive(Clone)]
pub struct ApiConfig {
    pub provider: String,
    pub api_key: String,
    pub base_url: String,
}

pub struct ProxyChannels {
    pub instruction_tx: tokio::sync::mpsc::Sender<ProxyInstructionEvent>,
    pub timeout_tx: tokio::sync::mpsc::Sender<ProxyTimeoutEvent>,
}

enum SessionCmd {
    Create {
        model: String,
        system_prompts: Vec<String>,
        max_iterations: Option<usize>,
        api_config: Option<ApiConfig>,
        proxy_channels: ProxyChannels,
        reply: CmdResult<(String, u64)>,
    },
    RunTurnStreaming {
        session_id: String,
        user_text: String,
        skill_context: Option<String>,
        event_tx: std::sync::mpsc::Sender<AssistantEvent>,
        reply: CmdResult<Option<Result<runtime::TurnSummary, runtime::RuntimeError>>>,
    },
    GetUsage {
        session_id: String,
        reply: CmdResult<Option<runtime::TokenUsage>>,
    },
    Remove {
        session_id: String,
        reply: CmdResult<bool>,
    },
}

impl SessionManager {
    #[allow(clippy::too_many_lines)]
    pub fn new(skill_engine: Arc<SkillEngine>) -> Self {
        let (cmd_tx, cmd_rx) = std::sync::mpsc::channel::<SessionCmd>();
        let active_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let count_clone = active_count.clone();
        let start_time = std::time::Instant::now();
        let shared: SharedSessions = Arc::new(Mutex::new(HashMap::new()));
        let shared_for_thread = shared.clone();

        std::thread::Builder::new()
            .name("session-manager".into())
            .spawn(move || {
                let shared = shared_for_thread;
                let mut store = InnerStore {
                    sessions: HashMap::new(),
                };
                while let Ok(cmd) = cmd_rx.recv() {
                    match cmd {
                        SessionCmd::Create {
                            model,
                            mut system_prompts,
                            max_iterations,
                            api_config,
                            proxy_channels,
                            reply,
                        } => {
                            let session = runtime::Session::new();
                            let session_id = session.session_id.clone();
                            let created_at_ms = session.created_at_ms;
                            system_prompts.push(TOOL_USAGE_GUIDANCE.to_string());

                            let tool_definitions = build_tool_definitions();
                            let (api_client, event_sink) = if let Some(cfg) = api_config {
                                match RealApiClient::new(
                                    &cfg.provider,
                                    &cfg.api_key,
                                    &cfg.base_url,
                                    &model,
                                    tool_definitions,
                                ) {
                                    Ok((c, sink)) => (AnyApiClient::Real(Box::new(c)), Some(sink)),
                                    Err(e) => {
                                        eprintln!("failed to create real API client: {e}, falling back to mock");
                                        (AnyApiClient::Mock(MockApiClient::new()), None)
                                    }
                                }
                            } else {
                                (AnyApiClient::Mock(MockApiClient::new()), None)
                            };

                            let cancel_flag = Arc::new(AtomicBool::new(false));
                            let pending: PendingMap =
                                Arc::new(std::sync::Mutex::new(HashMap::new()));
                            let proxy = ProxyToolExecutor::new(
                                session_id.clone(),
                                proxy_channels.instruction_tx,
                                proxy_channels.timeout_tx,
                                pending.clone(),
                                cancel_flag.clone(),
                            );
                            let executor =
                                CompositeToolExecutor::new(LocalToolExecutor::new(), proxy);

                            let policy =
                                runtime::PermissionPolicy::new(runtime::PermissionMode::Allow);
                            let mut rt = runtime::ConversationRuntime::new(
                                session,
                                api_client,
                                executor,
                                policy,
                                system_prompts,
                            )
                            .with_cancel_signal(cancel_flag.clone());
                            if let Some(max) = max_iterations {
                                rt = rt.with_max_iterations(max);
                            }

                            if let Ok(mut g) = shared.lock() {
                                g.insert(
                                    session_id.clone(),
                                    SharedSessionState {
                                        pending_proxy: pending.clone(),
                                        cancel_flag: cancel_flag.clone(),
                                    },
                                );
                            }

                            store.sessions.insert(
                                session_id.clone(),
                                SessionEntry {
                                    runtime: rt,
                                    event_sink,
                                    cancel_flag,
                                },
                            );
                            count_clone.store(
                                store.sessions.len(),
                                std::sync::atomic::Ordering::Relaxed,
                            );
                            let _ = reply.send((session_id, created_at_ms));
                        }
                        SessionCmd::RunTurnStreaming {
                            session_id,
                            user_text,
                            skill_context,
                            event_tx,
                            reply,
                        } => {
                            let result = store.sessions.get_mut(&session_id).map(|entry| {
                                entry
                                    .cancel_flag
                                    .store(false, std::sync::atomic::Ordering::Relaxed);
                                if let Some(sink) = &entry.event_sink {
                                    *sink.lock().unwrap() = Some(event_tx);
                                }
                                let augmented = if let Some(ctx) = skill_context {
                                    format!("{ctx}\n\n---\n\n{user_text}")
                                } else {
                                    user_text
                                };
                                let result = entry.runtime.run_turn(&augmented, None);
                                if let Some(sink) = &entry.event_sink {
                                    *sink.lock().unwrap() = None;
                                }
                                result
                            });
                            let _ = reply.send(result);
                        }
                        SessionCmd::GetUsage {
                            session_id,
                            reply,
                        } => {
                            let usage = store
                                .sessions
                                .get(&session_id)
                                .map(|entry| entry.runtime.usage().cumulative_usage());
                            let _ = reply.send(usage);
                        }
                        SessionCmd::Remove {
                            session_id,
                            reply,
                        } => {
                            let removed = store.sessions.remove(&session_id).is_some();
                            if let Ok(mut g) = shared.lock() {
                                g.remove(&session_id);
                            }
                            count_clone.store(
                                store.sessions.len(),
                                std::sync::atomic::Ordering::Relaxed,
                            );
                            let _ = reply.send(removed);
                        }
                    }
                }
            })
            .expect("failed to spawn session-manager thread");

        Self {
            cmd_tx,
            active_count,
            start_time,
            skill_engine,
            shared,
        }
    }

    pub fn skill_engine(&self) -> &Arc<SkillEngine> {
        &self.skill_engine
    }

    pub fn create_session(
        &self,
        model: String,
        system_prompts: Vec<String>,
        max_iterations: Option<usize>,
        api_config: Option<ApiConfig>,
        proxy_channels: ProxyChannels,
    ) -> (String, u64) {
        let (tx, rx) = std::sync::mpsc::channel();
        self.cmd_tx
            .send(SessionCmd::Create {
                model,
                system_prompts,
                max_iterations,
                api_config,
                proxy_channels,
                reply: tx,
            })
            .expect("session-manager thread gone");
        rx.recv().expect("session-manager thread gone")
    }

    pub fn run_turn_streaming(
        &self,
        session_id: &str,
        user_text: &str,
        skill_context: Option<String>,
        event_tx: std::sync::mpsc::Sender<AssistantEvent>,
    ) -> Option<Result<runtime::TurnSummary, runtime::RuntimeError>> {
        let (tx, rx) = std::sync::mpsc::channel();
        self.cmd_tx
            .send(SessionCmd::RunTurnStreaming {
                session_id: session_id.to_string(),
                user_text: user_text.to_string(),
                skill_context,
                event_tx,
                reply: tx,
            })
            .expect("session-manager thread gone");
        rx.recv().expect("session-manager thread gone")
    }

    /// Deliver a proxy tool result. Bypasses the session-manager command
    /// channel because the manager thread is blocked inside `run_turn` while a
    /// proxy call is pending; the pending-map is an `Arc<Mutex<...>>` and can
    /// safely be touched from any thread.
    pub fn deliver_proxy_result(
        &self,
        session_id: &str,
        instruction_id: &str,
        output: String,
        is_error: bool,
    ) -> bool {
        let pending = match self.shared.lock() {
            Ok(g) => g.get(session_id).map(|s| s.pending_proxy.clone()),
            Err(_) => None,
        };
        let Some(pending) = pending else {
            return false;
        };
        let Some(tx) = pending
            .lock()
            .ok()
            .and_then(|mut g| g.remove(instruction_id))
        else {
            return false;
        };
        tx.send(ProxyResultPayload { output, is_error }).is_ok()
    }

    /// Request cancellation. Bypasses the manager thread for the same reason
    /// as `deliver_proxy_result`: the runtime polls the atomic on every proxy
    /// wait tick, so flipping it from any thread is enough to unblock the turn.
    pub fn cancel_turn(&self, session_id: &str) -> bool {
        let Some(flag) = (match self.shared.lock() {
            Ok(g) => g.get(session_id).map(|s| s.cancel_flag.clone()),
            Err(_) => None,
        }) else {
            return false;
        };
        flag.store(true, std::sync::atomic::Ordering::Relaxed);
        true
    }

    pub fn get_usage(&self, session_id: &str) -> Option<runtime::TokenUsage> {
        let (tx, rx) = std::sync::mpsc::channel();
        self.cmd_tx
            .send(SessionCmd::GetUsage {
                session_id: session_id.to_string(),
                reply: tx,
            })
            .expect("session-manager thread gone");
        rx.recv().expect("session-manager thread gone")
    }

    pub fn remove_session(&self, session_id: &str) -> bool {
        let (tx, rx) = std::sync::mpsc::channel();
        self.cmd_tx
            .send(SessionCmd::Remove {
                session_id: session_id.to_string(),
                reply: tx,
            })
            .expect("session-manager thread gone");
        rx.recv().expect("session-manager thread gone")
    }

    pub fn active_count(&self) -> usize {
        self.active_count.load(std::sync::atomic::Ordering::Relaxed)
    }

    pub fn uptime_seconds(&self) -> u64 {
        self.start_time.elapsed().as_secs()
    }
}

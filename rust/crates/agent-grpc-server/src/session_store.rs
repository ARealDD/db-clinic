use std::collections::{BTreeSet, HashMap};
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use runtime::{ApiClient, ApiRequest, AssistantEvent, RuntimeError};
use session_persistence::{
    Checkpoint, SessionBackend, SessionRecord,
};

use crate::composite_executor::CompositeToolExecutor;
use crate::local_executor::LocalToolExecutor;
use crate::mock::MockApiClient;
use crate::proxy_executor::{
    PendingMap, ProxyInstructionEvent, ProxyResultPayload, ProxyTimeoutEvent, ProxyToolExecutor,
};
use crate::real_client::{EventSink, RealApiClient};
use crate::skill_engine::SkillEngine;

const ALLOWED_TOOLS: &[&str] = &[
    "bash",
    "read_file",
    "write_file",
    "edit_file",
    "glob_search",
    "grep_search",
];

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
    pub cancel_flag: Arc<AtomicBool>,
    pub step_index: usize,
}

struct InnerStore {
    sessions: HashMap<String, SessionEntry>,
}

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
    #[allow(dead_code)]
    backend: Arc<dyn SessionBackend>,
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

#[allow(dead_code)]
enum SessionCmd {
Create {
        model: String,
        system_prompts: Vec<String>,
        max_iterations: Option<usize>,
        api_config: Option<ApiConfig>,
        proxy_channels: ProxyChannels,
        data_dir: Option<String>,
        user_id: Option<String>,
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
    Resume {
        session_id: String,
        data_dir: String,
        user_id: String,
        model: String,
        system_prompts: Vec<String>,
        max_iterations: Option<usize>,
        api_config: Option<ApiConfig>,
        proxy_channels: ProxyChannels,
        reply: CmdResult<Result<(String, u64, usize), String>>,
    },
ListBackend {
        reply: CmdResult<Vec<SessionRecord>>,
    },
}

fn current_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn save_session_meta_to_backend(
    backend: &dyn SessionBackend,
    session_id: &str,
    created_at_ms: u64,
    model: Option<&str>,
    workspace_root: Option<&std::path::Path>,
    fork_parent_id: Option<&str>,
    fork_branch_name: Option<&str>,
    username: Option<&str>,
) {
    let record = SessionRecord {
        session_id: session_id.to_string(),
        created_at_ms,
        updated_at_ms: current_time_ms(),
        model: model.map(String::from),
        workspace_root: workspace_root.map(|p| p.to_string_lossy().to_string()),
        fork_parent_id: fork_parent_id.map(String::from),
        fork_branch_name: fork_branch_name.map(String::from),
        username: username.map(String::from),
    };
    if let Err(e) = backend.save_session_meta(&record) {
        eprintln!("warning: failed to save session meta to backend: {e}");
    }
}

fn save_checkpoint_to_backend(
    backend: &dyn SessionBackend,
    session_id: &str,
    step_index: usize,
    message_count: usize,
) {
    let checkpoint = Checkpoint {
        checkpoint_id: format!("cp-{session_id}-{step_index}"),
        session_id: session_id.to_string(),
        step_index,
        timestamp_ms: current_time_ms(),
        message_count,
        summary: None,
    };
    if let Err(e) = backend.save_checkpoint(&checkpoint) {
        eprintln!("warning: failed to save checkpoint to backend: {e}");
    }
}

impl SessionManager {
    #[allow(clippy::too_many_lines)]
    pub fn new(skill_engine: Arc<SkillEngine>, backend: Arc<dyn SessionBackend>) -> Self {
        let (cmd_tx, cmd_rx) = std::sync::mpsc::channel::<SessionCmd>();
        let active_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let count_clone = active_count.clone();
        let start_time = std::time::Instant::now();
        let shared: SharedSessions = Arc::new(Mutex::new(HashMap::new()));
        let shared_for_thread = shared.clone();
        let backend_for_thread = backend.clone();

        std::thread::Builder::new()
            .name("session-manager".into())
            .spawn(move || {
                let shared = shared_for_thread;
                let backend = backend_for_thread;
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
        data_dir,
        user_id,
        reply,
    } => {
    let mut session = runtime::Session::new();
    let workspace_root = if let Some(dir) = data_dir {
        let root = std::path::PathBuf::from(&dir);
        session = session.with_workspace_root(root.clone());
        let sessions_dir = if let Some(uid) = &user_id {
            root.join("sessions").join(uid)
        } else {
            root.join("sessions")
        };
        std::fs::create_dir_all(&sessions_dir).ok();
        let path = sessions_dir.join(format!("{}.jsonl", session.session_id));
        session = session.with_persistence_path(path);
        Some(root)
    } else {
        None
    };
    let session_id = session.session_id.clone();
    let created_at_ms = session.created_at_ms;
    system_prompts.push(TOOL_USAGE_GUIDANCE.to_string());

    save_session_meta_to_backend(
        &*backend,
        &session_id,
        created_at_ms,
        Some(&model),
        workspace_root.as_deref(),
        None,
        None,
        user_id.as_deref(),
    );

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
                                        tracing::warn!(
                                            error = %e,
                                            "failed to create real API client, falling back to mock"
                                        );
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
                                    step_index: 0,
                                },
                            );
                            if let Some(path) = store.sessions.get(&session_id)
                                .and_then(|e| e.runtime.session().persistence_path())
                            {
                                if !path.exists() {
if let Some(Err(e)) = store.sessions.get(&session_id)
                                .map(|e| e.runtime.session().save_to_path(path))
                            {
                                eprintln!("warning: failed to bootstrap session file: {e}");
                            }
                                }
                            }
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
                                    let mut guard =
                                        sink.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
                                    *guard = Some(event_tx);
                                }
                                let augmented = if let Some(ctx) = skill_context {
                                    format!("{ctx}\n\n---\n\n{user_text}")
                                } else {
                                    user_text
                                };
                                let step_before = entry.step_index;
                                let outcome = std::panic::catch_unwind(
                                    std::panic::AssertUnwindSafe(|| {
                                        entry.runtime.run_turn(&augmented, None)
                                    }),
                                );
                                entry.step_index += 1;
                                if let Some(sink) = &entry.event_sink {
                                    let mut guard =
                                        sink.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
                                    *guard = None;
                                }
                                let result = match outcome {
                                    Ok(result) => result,
                                    Err(payload) => {
                                        let msg = panic_message(&payload);
                                        tracing::error!(
                                            session_id = %session_id,
                                            panic_message = %msg,
                                            "runtime panicked inside run_turn"
                                        );
                                        Err(RuntimeError::Internal {
                                            source: "runtime panic",
                                            message: msg,
                                        })
                                    }
                                };

                                let msg_count = entry.runtime.session().messages.len();
                                save_checkpoint_to_backend(
                                    &*backend,
                                    &session_id,
                                    step_before,
                                    msg_count,
                                );

                                if let Ok(Some(mut meta)) = backend.load_session_meta(&session_id) {
                                    meta.updated_at_ms = current_time_ms();
                                    if let Err(e) = backend.save_session_meta(&meta) {
                                        eprintln!("warning: failed to update session meta: {e}");
                                    }
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
                            if let Err(e) = backend.delete_session(&session_id) {
                                eprintln!("warning: failed to delete session from backend: {e}");
                            }
                            count_clone.store(
                                store.sessions.len(),
                                std::sync::atomic::Ordering::Relaxed,
                            );
                            let _ = reply.send(removed);
                        }
SessionCmd::Resume {
        session_id,
        data_dir,
        user_id,
        model,
        mut system_prompts,
        max_iterations,
        api_config,
        proxy_channels,
        reply,
    } => {
    let workspace_root = std::path::PathBuf::from(&data_dir);
    let sessions_dir = if user_id.is_empty() {
        workspace_root.join("sessions")
    } else {
        workspace_root.join("sessions").join(&user_id)
    };
    std::fs::create_dir_all(&sessions_dir).ok();
    let path = sessions_dir.join(format!("{}.jsonl", session_id));

                            let session = match runtime::Session::load_from_path(&path) {
                                Ok(s) => s.with_workspace_root(&workspace_root),
                                Err(_) => {
                                    let mut fresh = runtime::Session::new();
                                    fresh.session_id = session_id.clone();
                                    fresh = fresh
                                        .with_workspace_root(&workspace_root)
                                        .with_persistence_path(&path);
                                    if let Err(e) = fresh.save_to_path(&path) {
                                        eprintln!("warning: failed to bootstrap fresh session file: {e}");
                                    }
                                    fresh
                                }
                            };
                            let msg_count = session.messages.len();
                            let created_at_ms = session.created_at_ms;
                            let sid = session.session_id.clone();
                            system_prompts.push(TOOL_USAGE_GUIDANCE.to_string());

save_session_meta_to_backend(
        &*backend,
        &sid,
        created_at_ms,
        Some(&model),
        Some(workspace_root.as_ref()),
        session.fork.as_ref().map(|f| f.parent_session_id.as_str()),
        session.fork.as_ref().and_then(|f| f.branch_name.as_deref()),
        if user_id.is_empty() { None } else { Some(&user_id) },
    );

                            let _step_index = msg_count;

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
                                sid.clone(),
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
                                    sid.clone(),
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
                                    step_index: 0,
                                },
                            );
                            if let Some(path) = store.sessions.get(&session_id)
                                .and_then(|e| e.runtime.session().persistence_path())
                            {
                                if !path.exists() {
                                    if let Some(Err(e)) = store.sessions.get(&session_id)
                                        .map(|e| e.runtime.session().save_to_path(path))
                                    {
                                        eprintln!("warning: failed to bootstrap session file: {e}");
                                    }
                                }
                            }
                            count_clone.store(
                                store.sessions.len(),
                                std::sync::atomic::Ordering::Relaxed,
                            );
                            let _ = reply.send(Ok((sid, created_at_ms, msg_count)));
                        }
                        SessionCmd::ListBackend { reply } => {
                            let result = backend
                                .list_sessions()
                                .unwrap_or_default();
                            let _ = reply.send(result);
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
            backend,
        }
    }

    pub fn skill_engine(&self) -> &Arc<SkillEngine> {
        &self.skill_engine
    }

    #[allow(dead_code)]
    pub fn backend(&self) -> &Arc<dyn SessionBackend> {
        &self.backend
    }

    pub fn create_session(
        &self,
        model: String,
        system_prompts: Vec<String>,
        max_iterations: Option<usize>,
        api_config: Option<ApiConfig>,
        proxy_channels: ProxyChannels,
        data_dir: Option<String>,
        user_id: Option<String>,
    ) -> (String, u64) {
        let (tx, rx) = std::sync::mpsc::channel();
        self.cmd_tx
            .send(SessionCmd::Create {
                model,
                system_prompts,
                max_iterations,
                api_config,
                proxy_channels,
                data_dir,
                user_id,
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

pub fn resume_session(
        &self,
        session_id: &str,
        data_dir: &str,
        user_id: &str,
        model: String,
        system_prompts: Vec<String>,
        max_iterations: Option<usize>,
        api_config: Option<ApiConfig>,
        proxy_channels: ProxyChannels,
    ) -> Result<(String, u64, usize), String> {
        let (tx, rx) = std::sync::mpsc::channel();
        self.cmd_tx
            .send(SessionCmd::Resume {
                session_id: session_id.to_string(),
                data_dir: data_dir.to_string(),
                user_id: user_id.to_string(),
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

    #[allow(dead_code)]
    pub fn list_sessions_backend(&self) -> Vec<SessionRecord> {
        let (tx, rx) = std::sync::mpsc::channel();
        self.cmd_tx
            .send(SessionCmd::ListBackend { reply: tx })
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

fn panic_message(payload: &Box<dyn std::any::Any + Send>) -> String {
    if let Some(s) = payload.downcast_ref::<&'static str>() {
        return (*s).to_string();
    }
    if let Some(s) = payload.downcast_ref::<String>() {
        return s.clone();
    }
    "<non-string panic payload>".to_string()
}

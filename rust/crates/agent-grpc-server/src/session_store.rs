use std::collections::{BTreeSet, HashMap};
use std::panic::AssertUnwindSafe;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use futures_util::FutureExt;
use runtime::{ApiClient, ApiRequest, AssistantEvent, RuntimeError};
use session_persistence::{Checkpoint, SessionBackend, SessionRecord};
use tokio::sync::Mutex as AsyncMutex;
use tokio_util::sync::CancellationToken;

use crate::composite_executor::CompositeToolExecutor;
use crate::local_executor::LocalToolExecutor;
use crate::mock::MockApiClient;
use crate::prompt_log::PromptLogObserver;
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
- When you call `bash`, every invocation is shown to a human operator as an instruction card. The operator reads the `description` field to decide whether to run the command, suggest a different approach, or reject it entirely. A weak or missing description makes rejection more likely. You MUST set the `description` input field to a short, plain-language statement of WHAT YOU ARE TRYING TO LEARN or ACCOMPLISH.
- If the operator returns `is_error=true` with a text message instead of command output, treat the message as feedback or guidance and adjust your approach — do not retry the same command.

## Diagnosis workflow

You are a database diagnosis assistant. Follow the diagnostic steps in the matched skills above — they tell you exactly what information to request at each stage.

- **Step 1 — Request diagnostic information**: When you need diagnostic data from the user (e.g., `EXPLAIN ANALYZE` output, table schema, wait events, metrics), call `bash` with the command you want the user to run. Set the `description` to a clear explanation of what you need and why. This will show the user an instruction card with the command, and they can paste the output back to you through that card.
  - IMPORTANT: For diagnostic steps, do NOT just ask in plain text — use `bash` so a proxy instruction card appears where the user can submit the result.
- **Step 2 — Analyze**: Interpret the data the user provides and determine the root cause.
- **Step 3 — Recommend**: Give the user a concrete fix (index DDL, config change, query rewrite).

Only use `bash` for requesting diagnostic information as described above, or when the user explicitly asks you to run a command on their behalf.";

fn build_tool_definitions() -> Vec<api::ToolDefinition> {
    let registry = tools::GlobalToolRegistry::builtin();
    let allow_set: BTreeSet<String> = ALLOWED_TOOLS.iter().map(|s| (*s).to_string()).collect();
    registry.definitions(Some(&allow_set))
}

pub enum AnyApiClient {
    Mock(MockApiClient),
    Real(Box<RealApiClient>),
}

#[async_trait]
impl ApiClient for AnyApiClient {
    async fn stream(
        &mut self,
        request: ApiRequest,
    ) -> Result<Vec<AssistantEvent>, RuntimeError> {
        match self {
            Self::Mock(c) => c.stream(request).await,
            Self::Real(c) => c.stream(request).await,
        }
    }

    fn model(&self) -> &str {
        match self {
            Self::Mock(c) => c.model(),
            Self::Real(c) => c.model(),
        }
    }

    fn provider(&self) -> &str {
        match self {
            Self::Mock(c) => c.provider(),
            Self::Real(c) => c.provider(),
        }
    }
}

type AnyRuntime = runtime::ConversationRuntime<AnyApiClient, CompositeToolExecutor>;

pub struct SessionEntry {
    pub runtime: AnyRuntime,
    pub event_sink: Option<EventSink>,
    pub step_index: usize,
}

/// Per-session handle stored in the actor's map.
///
/// `entry` is wrapped in an `Arc<tokio::sync::Mutex<_>>` so the actor task can
/// hand off ownership for the duration of a turn — the actor stays free to
/// handle other commands while one session is mid-LLM-call (or mid-30-min
/// proxy wait). Plan ② switched this from `std::sync::Mutex` to the tokio
/// flavour because the per-turn worker is now an async task: holding a
/// `std::sync::MutexGuard` across an `.await` is a future-Send hazard the
/// compiler can't always catch.
///
/// `created_at_ms`, `cancel_flag`, `cancel_token`, and `msg_count` are exposed
/// *without* taking the entry lock — this is critical for the live-Resume
/// idempotent reply and for cancel-during-running-turn. Holding the entry
/// lock would block on the in-flight turn, which is exactly what B1 set out
/// to fix.
#[derive(Clone)]
pub struct SessionHandle {
    pub entry: Arc<AsyncMutex<SessionEntry>>,
    pub created_at_ms: u64,
    pub cancel_flag: Arc<AtomicBool>,
    pub cancel_token: CancellationToken,
    pub msg_count: Arc<AtomicUsize>,
}

struct InnerStore {
    sessions: HashMap<String, SessionHandle>,
}

#[derive(Clone)]
struct SharedSessionState {
    pending_proxy: PendingMap,
    cancel_flag: Arc<AtomicBool>,
    cancel_token: CancellationToken,
}

type SharedSessions = Arc<std::sync::Mutex<HashMap<String, SharedSessionState>>>;

#[derive(Clone)]
pub struct SessionManager {
    cmd_tx: tokio::sync::mpsc::UnboundedSender<SessionCmd>,
    active_count: Arc<std::sync::atomic::AtomicUsize>,
    start_time: std::time::Instant,
    skill_engine: Arc<SkillEngine>,
    shared: SharedSessions,
    #[allow(dead_code)]
    backend: Arc<dyn SessionBackend>,
}

type CmdResult<T> = tokio::sync::oneshot::Sender<T>;

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
        event_tx: tokio::sync::mpsc::Sender<AssistantEvent>,
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
        let (cmd_tx, mut cmd_rx) =
            tokio::sync::mpsc::unbounded_channel::<SessionCmd>();
        let active_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let count_clone = active_count.clone();
        let start_time = std::time::Instant::now();
        let shared: SharedSessions = Arc::new(std::sync::Mutex::new(HashMap::new()));
        let shared_for_task = shared.clone();
        let backend_for_task = backend.clone();

        tokio::spawn(async move {
            let shared = shared_for_task;
            let backend = backend_for_task;
            let mut store = InnerStore {
                sessions: HashMap::new(),
            };
            while let Some(cmd) = cmd_rx.recv().await {
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
                        inject_diagnosis_context(&mut system_prompts);
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
                        let cancel_token = CancellationToken::new();
                        let pending: PendingMap =
                            Arc::new(AsyncMutex::new(HashMap::new()));
                        let proxy = ProxyToolExecutor::new(
                            session_id.clone(),
                            proxy_channels.instruction_tx,
                            proxy_channels.timeout_tx,
                            pending.clone(),
                            cancel_flag.clone(),
                            cancel_token.clone(),
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
                        .with_cancel_signal(cancel_flag.clone())
                        .with_cancel_token(cancel_token.clone())
                        .with_turn_observer(Arc::new(PromptLogObserver));
                        if let Some(max) = max_iterations {
                            rt = rt.with_max_iterations(max);
                        }

                        if let Ok(mut g) = shared.lock() {
                            g.insert(
                                session_id.clone(),
                                SharedSessionState {
                                    pending_proxy: pending.clone(),
                                    cancel_flag: cancel_flag.clone(),
                                    cancel_token: cancel_token.clone(),
                                },
                            );
                        }

                        // Bootstrap persistence file before wrapping in Mutex —
                        // cheaper and clearer than locking ourselves to call save.
                        if let Some(path) = rt.session().persistence_path() {
                            if !path.exists() {
                                if let Err(e) = rt.session().save_to_path(path) {
                                    eprintln!(
                                        "warning: failed to bootstrap session file: {e}"
                                    );
                                }
                            }
                        }

                        let handle = SessionHandle {
                            entry: Arc::new(AsyncMutex::new(SessionEntry {
                                runtime: rt,
                                event_sink,
                                step_index: 0,
                            })),
                            created_at_ms,
                            cancel_flag,
                            cancel_token,
                            msg_count: Arc::new(AtomicUsize::new(0)),
                        };
                        store.sessions.insert(session_id.clone(), handle);
                        count_clone.store(
                            store.sessions.len(),
                            Ordering::Relaxed,
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
                        let Some(handle) = store.sessions.get(&session_id) else {
                            let _ = reply.send(None);
                            continue;
                        };
                        // Reset the per-session cancel signal on the actor
                        // task (before spawning the per-turn task) so the
                        // runtime observes `false` from the first iteration.
                        // We swap the cancel_token for a fresh one too, so a
                        // previously-cancelled turn doesn't poison the new one.
                        handle.cancel_flag.store(false, Ordering::Relaxed);
                        let new_token = CancellationToken::new();
                        // Update both the SessionHandle's token and the shared
                        // map so cancel_turn() flips the *new* token, not the
                        // old (already-cancelled) one. We can only mutate the
                        // shared entry; the handle's `cancel_token` field is
                        // overwritten below by replacing the map entry.
                        if let Ok(mut g) = shared.lock() {
                            if let Some(state) = g.get_mut(&session_id) {
                                state.cancel_token = new_token.clone();
                            }
                        }
                        // Replace the handle's cancel_token in-place by
                        // re-inserting a clone of the handle with updated
                        // token. Cheap — `SessionHandle` is fully Arc-backed.
                        let mut new_handle = handle.clone();
                        new_handle.cancel_token = new_token.clone();
                        store.sessions.insert(session_id.clone(), new_handle);

                        let entry_arc = store
                            .sessions
                            .get(&session_id)
                            .expect("just inserted")
                            .entry
                            .clone();
                        let msg_count_atom = store
                            .sessions
                            .get(&session_id)
                            .expect("just inserted")
                            .msg_count
                            .clone();
                        let backend_for_worker = backend.clone();
                        let sid_for_worker = session_id.clone();
                        // Per-session worker task: serializes against other
                        // turns for the same session via entry.lock(), but
                        // other sessions and other commands proceed
                        // independently. Plan ② swapped the std::thread for
                        // tokio::spawn so the long proxy waits no longer pin
                        // an OS thread.
                        tokio::spawn(async move {
                            let mut guard = entry_arc.lock().await;
                            let entry: &mut SessionEntry = &mut guard;
                            if let Some(sink) = &entry.event_sink {
                                if let Ok(mut g) = sink.lock() {
                                    *g = Some(event_tx);
                                }
                            }
                            let augmented = if let Some(ctx) = skill_context {
                                format!("{ctx}\n\n---\n\n{user_text}")
                            } else {
                                user_text
                            };
                            // Isolate runtime panics: a single bad turn must
                            // not poison the entry mutex (we sink the panic
                            // into a RuntimeError and drop the guard normally).
                            // `FutureExt::catch_unwind` is the async analogue
                            // of `std::panic::catch_unwind`.
                            let step_before = entry.step_index;
                            let outcome =
                                AssertUnwindSafe(entry.runtime.run_turn(&augmented, None))
                                    .catch_unwind()
                                    .await;
                            entry.step_index += 1;
                            if let Some(sink) = &entry.event_sink {
                                if let Ok(mut g) = sink.lock() {
                                    *g = None;
                                }
                            }
                            let result = match outcome {
                                Ok(result) => result,
                                Err(payload) => {
                                    let msg = panic_message(&payload);
                                    tracing::error!(
                                        session_id = %sid_for_worker,
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
                                &*backend_for_worker,
                                &sid_for_worker,
                                step_before,
                                msg_count,
                            );

                            if let Ok(Some(mut meta)) =
                                backend_for_worker.load_session_meta(&sid_for_worker)
                            {
                                meta.updated_at_ms = current_time_ms();
                                if let Err(e) =
                                    backend_for_worker.save_session_meta(&meta)
                                {
                                    eprintln!(
                                        "warning: failed to update session meta: {e}"
                                    );
                                }
                            }
                            // Publish the new message count for the lock-free
                            // live-Resume path.
                            msg_count_atom.store(msg_count, Ordering::Relaxed);
                            // Release entry lock BEFORE the reply so any
                            // queued next-turn worker for this session can
                            // start immediately.
                            drop(guard);
                            let _ = reply.send(Some(result));
                        });
                    }
                    SessionCmd::GetUsage { session_id, reply } => {
                        let Some(handle) = store.sessions.get(&session_id) else {
                            let _ = reply.send(None);
                            continue;
                        };
                        let entry_arc = handle.entry.clone();
                        // Off-actor read: locking briefly is fine because it
                        // only contends against an in-flight turn for the same
                        // session — and even there it's a quick immutable read.
                        tokio::spawn(async move {
                            let guard = entry_arc.lock().await;
                            let usage = guard.runtime.usage().cumulative_usage();
                            let _ = reply.send(Some(usage));
                        });
                    }
                    SessionCmd::Remove { session_id, reply } => {
                        // Note: dropping the handle here drops its
                        // Arc<Mutex<SessionEntry>>. A worker task that still
                        // holds a strong Arc will keep the SessionEntry alive
                        // until it finishes — the in-flight turn completes
                        // safely, it just no longer affects this kernel's map.
                        let removed = store.sessions.remove(&session_id).is_some();
                        if let Ok(mut g) = shared.lock() {
                            g.remove(&session_id);
                        }
                        if let Err(e) = backend.delete_session(&session_id) {
                            eprintln!("warning: failed to delete session from backend: {e}");
                        }
                        count_clone.store(
                            store.sessions.len(),
                            Ordering::Relaxed,
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
                        // If the session is already live in this kernel
                        // process, don't tear it down and rebuild from disk —
                        // that would orphan any in-flight turn (e.g. one
                        // waiting on a proxy_result the operator hasn't sent
                        // yet). The React app may call /resume opportunistically
                        // when the user switches back to a tab; idempotency
                        // keeps the pending proxy alive.
                        //
                        // CRITICAL: read `created_at_ms` and `msg_count` from
                        // the lock-free `SessionHandle` fields. Locking
                        // `handle.entry` here would deadlock against the
                        // in-flight turn we're trying NOT to disturb.
                        if let Some(handle) = store.sessions.get(&session_id) {
                            let sid = session_id.clone();
                            let created_at_ms = handle.created_at_ms;
                            let msg_count = handle.msg_count.load(Ordering::Relaxed);
                            let _ = reply.send(Ok((sid, created_at_ms, msg_count)));
                            continue;
                        }
                        let workspace_root = std::path::PathBuf::from(&data_dir);
                        let sessions_dir = if user_id.is_empty() {
                            workspace_root.join("sessions")
                        } else {
                            workspace_root.join("sessions").join(&user_id)
                        };
                        std::fs::create_dir_all(&sessions_dir).ok();
                        let path = sessions_dir.join(format!("{session_id}.jsonl"));

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
                        inject_diagnosis_context(&mut system_prompts);
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
                        let cancel_token = CancellationToken::new();
                        let pending: PendingMap =
                            Arc::new(AsyncMutex::new(HashMap::new()));
                        let proxy = ProxyToolExecutor::new(
                            sid.clone(),
                            proxy_channels.instruction_tx,
                            proxy_channels.timeout_tx,
                            pending.clone(),
                            cancel_flag.clone(),
                            cancel_token.clone(),
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
                        .with_cancel_signal(cancel_flag.clone())
                        .with_cancel_token(cancel_token.clone())
                        .with_turn_observer(Arc::new(PromptLogObserver));
                        if let Some(max) = max_iterations {
                            rt = rt.with_max_iterations(max);
                        }

                        if let Ok(mut g) = shared.lock() {
                            g.insert(
                                sid.clone(),
                                SharedSessionState {
                                    pending_proxy: pending.clone(),
                                    cancel_flag: cancel_flag.clone(),
                                    cancel_token: cancel_token.clone(),
                                },
                            );
                        }

                        // Bootstrap persistence file BEFORE wrapping in Mutex,
                        // same pattern as Create — avoids needing to lock
                        // ourselves.
                        if let Some(persist_path) = rt.session().persistence_path() {
                            if !persist_path.exists() {
                                if let Err(e) = rt.session().save_to_path(persist_path) {
                                    eprintln!("warning: failed to bootstrap session file: {e}");
                                }
                            }
                        }

                        let handle = SessionHandle {
                            entry: Arc::new(AsyncMutex::new(SessionEntry {
                                runtime: rt,
                                event_sink,
                                step_index: 0,
                            })),
                            created_at_ms,
                            cancel_flag,
                            cancel_token,
                            // Seed msg_count with the count we just loaded
                            // from disk so live-Resume sees the right value
                            // even before the first new turn runs.
                            msg_count: Arc::new(AtomicUsize::new(msg_count)),
                        };
                        store.sessions.insert(session_id.clone(), handle);
                        count_clone.store(
                            store.sessions.len(),
                            Ordering::Relaxed,
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
        });

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

    pub async fn create_session(
        &self,
        model: String,
        system_prompts: Vec<String>,
        max_iterations: Option<usize>,
        api_config: Option<ApiConfig>,
        proxy_channels: ProxyChannels,
        data_dir: Option<String>,
        user_id: Option<String>,
    ) -> (String, u64) {
        let (tx, rx) = tokio::sync::oneshot::channel();
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
            .expect("session-manager actor gone");
        rx.await.expect("session-manager actor gone")
    }

    pub async fn run_turn_streaming(
        &self,
        session_id: &str,
        user_text: &str,
        skill_context: Option<String>,
        event_tx: tokio::sync::mpsc::Sender<AssistantEvent>,
    ) -> Option<Result<runtime::TurnSummary, runtime::RuntimeError>> {
        let (tx, rx) = tokio::sync::oneshot::channel();
        self.cmd_tx
            .send(SessionCmd::RunTurnStreaming {
                session_id: session_id.to_string(),
                user_text: user_text.to_string(),
                skill_context,
                event_tx,
                reply: tx,
            })
            .expect("session-manager actor gone");
        rx.await.expect("session-manager actor gone")
    }

    pub async fn deliver_proxy_result(
        &self,
        session_id: &str,
        instruction_id: &str,
        output: String,
        is_error: bool,
    ) -> bool {
        // Snapshot the pending map under the sync mutex, drop it before
        // touching the async one.
        let pending = match self.shared.lock() {
            Ok(g) => g.get(session_id).map(|s| s.pending_proxy.clone()),
            Err(_) => None,
        };
        let Some(pending) = pending else {
            tracing::debug!(
                session_id,
                instruction_id,
                "deliver_proxy_result: no pending map for session"
            );
            return false;
        };
        let mut guard = pending.lock().await;
        let Some(tx) = guard.remove(instruction_id) else {
            tracing::debug!(
                session_id,
                instruction_id,
                "deliver_proxy_result: no pending entry for instruction"
            );
            return false;
        };
        // Drop the lock before sending so the executor's `remove_pending`
        // doesn't deadlock against us.
        drop(guard);
        match tx.send(ProxyResultPayload { output, is_error }) {
            Ok(()) => {
                tracing::debug!(
                    session_id,
                    instruction_id,
                    "deliver_proxy_result: oneshot delivered"
                );
                true
            }
            Err(_) => {
                tracing::warn!(
                    session_id,
                    instruction_id,
                    "deliver_proxy_result: receiver gone (turn cancelled or timed out)"
                );
                false
            }
        }
    }

    pub fn cancel_turn(&self, session_id: &str) -> bool {
        let snapshot = match self.shared.lock() {
            Ok(g) => g
                .get(session_id)
                .map(|s| (s.cancel_flag.clone(), s.cancel_token.clone())),
            Err(_) => None,
        };
        let Some((flag, token)) = snapshot else {
            return false;
        };
        flag.store(true, std::sync::atomic::Ordering::Relaxed);
        token.cancel();
        tracing::info!(session_id, "cancel_turn: flipped both cancel paths");
        true
    }

    pub async fn get_usage(&self, session_id: &str) -> Option<runtime::TokenUsage> {
        let (tx, rx) = tokio::sync::oneshot::channel();
        self.cmd_tx
            .send(SessionCmd::GetUsage {
                session_id: session_id.to_string(),
                reply: tx,
            })
            .expect("session-manager actor gone");
        rx.await.expect("session-manager actor gone")
    }

    pub async fn remove_session(&self, session_id: &str) -> bool {
        let (tx, rx) = tokio::sync::oneshot::channel();
        self.cmd_tx
            .send(SessionCmd::Remove {
                session_id: session_id.to_string(),
                reply: tx,
            })
            .expect("session-manager actor gone");
        rx.await.expect("session-manager actor gone")
    }

    pub async fn resume_session(
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
        let (tx, rx) = tokio::sync::oneshot::channel();
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
            .expect("session-manager actor gone");
        rx.await.expect("session-manager actor gone")
    }

    #[allow(dead_code)]
    pub async fn list_sessions_backend(&self) -> Vec<SessionRecord> {
        let (tx, rx) = tokio::sync::oneshot::channel();
        self.cmd_tx
            .send(SessionCmd::ListBackend { reply: tx })
            .expect("session-manager actor gone");
        rx.await.expect("session-manager actor gone")
    }

    pub fn active_count(&self) -> usize {
        self.active_count.load(std::sync::atomic::Ordering::Relaxed)
    }

    pub fn uptime_seconds(&self) -> u64 {
        self.start_time.elapsed().as_secs()
    }
}

/// Pre-pends the diagnosis-mode environment / actions / instruction context to
/// `system_prompts` (after the user's role/background/rules segments, before
/// the kernel's `TOOL_USAGE_GUIDANCE`).
///
/// Best-effort: if any step fails (cwd unreadable, instruction-file IO error)
/// we log at `debug` and leave `system_prompts` untouched — the session still
/// runs with just the user's segments and `TOOL_USAGE_GUIDANCE`. A logging
/// failure must never prevent a session from being created.
fn inject_diagnosis_context(system_prompts: &mut Vec<String>) {
    let cwd = match std::env::current_dir() {
        Ok(p) => p,
        Err(e) => {
            tracing::debug!(error = %e, "diagnosis prompt: cwd unavailable, skipping injection");
            return;
        }
    };
    let date = runtime::today_utc_ymd();
    let sections = match runtime::load_diagnosis_system_prompt(
        &cwd,
        date,
        std::env::consts::OS,
        std::env::consts::ARCH,
    ) {
        Ok(s) => s,
        Err(e) => {
            tracing::debug!(error = %e, "diagnosis prompt: section build failed, skipping injection");
            return;
        }
    };
    for section in sections {
        if !section.trim().is_empty() {
            system_prompts.push(section);
        }
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

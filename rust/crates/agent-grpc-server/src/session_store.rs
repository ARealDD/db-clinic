use std::collections::HashMap;
use std::sync::Arc;

use runtime::{ApiClient, ApiRequest, AssistantEvent, RuntimeError};

use crate::mock::{MockApiClient, MockToolExecutor};
use crate::real_client::{EventSink, RealApiClient};

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

type AnyRuntime = runtime::ConversationRuntime<AnyApiClient, MockToolExecutor>;

pub struct SessionEntry {
    pub runtime: AnyRuntime,
    pub event_sink: Option<EventSink>,
}

struct InnerStore {
    sessions: HashMap<String, SessionEntry>,
}

#[derive(Clone)]
pub struct SessionManager {
    cmd_tx: std::sync::mpsc::Sender<SessionCmd>,
    active_count: Arc<std::sync::atomic::AtomicUsize>,
    start_time: std::time::Instant,
}

type CmdResult<T> = std::sync::mpsc::Sender<T>;

#[derive(Clone)]
pub struct ApiConfig {
    pub provider: String,
    pub api_key: String,
    pub base_url: String,
}

enum SessionCmd {
    Create {
        model: String,
        system_prompts: Vec<String>,
        max_iterations: Option<usize>,
        api_config: Option<ApiConfig>,
        reply: CmdResult<(String, u64)>,
    },
    RunTurnStreaming {
        session_id: String,
        user_text: String,
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
    pub fn new() -> Self {
        let (cmd_tx, cmd_rx) = std::sync::mpsc::channel::<SessionCmd>();
        let active_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let count_clone = active_count.clone();
        let start_time = std::time::Instant::now();

        std::thread::Builder::new()
            .name("session-manager".into())
            .spawn(move || {
                let mut store = InnerStore {
                    sessions: HashMap::new(),
                };
                while let Ok(cmd) = cmd_rx.recv() {
                    match cmd {
                        SessionCmd::Create {
                            model,
                            system_prompts,
                            max_iterations,
                            api_config,
                            reply,
                        } => {
                            let session = runtime::Session::new();
                            let session_id = session.session_id.clone();
                            let created_at_ms = session.created_at_ms;

                            let (api_client, event_sink) = if let Some(cfg) = api_config {
                                match RealApiClient::new(
                                    &cfg.provider,
                                    &cfg.api_key,
                                    &cfg.base_url,
                                    &model,
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

                            let policy =
                                runtime::PermissionPolicy::new(runtime::PermissionMode::Allow);
                            let mut rt = runtime::ConversationRuntime::new(
                                session,
                                api_client,
                                MockToolExecutor,
                                policy,
                                system_prompts,
                            );
                            if let Some(max) = max_iterations {
                                rt = rt.with_max_iterations(max);
                            }

                            store.sessions.insert(
                                session_id.clone(),
                                SessionEntry {
                                    runtime: rt,
                                    event_sink,
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
                            event_tx,
                            reply,
                        } => {
                            let result = store.sessions.get_mut(&session_id).map(|entry| {
                                if let Some(sink) = &entry.event_sink {
                                    *sink.lock().unwrap() = Some(event_tx);
                                }
                                let result = entry.runtime.run_turn(&user_text, None);
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
        }
    }

    pub fn create_session(
        &self,
        model: String,
        system_prompts: Vec<String>,
        max_iterations: Option<usize>,
        api_config: Option<ApiConfig>,
    ) -> (String, u64) {
        let (tx, rx) = std::sync::mpsc::channel();
        self.cmd_tx
            .send(SessionCmd::Create {
                model,
                system_prompts,
                max_iterations,
                api_config,
                reply: tx,
            })
            .expect("session-manager thread gone");
        rx.recv().expect("session-manager thread gone")
    }

    pub fn run_turn_streaming(
        &self,
        session_id: &str,
        user_text: &str,
        event_tx: std::sync::mpsc::Sender<AssistantEvent>,
    ) -> Option<Result<runtime::TurnSummary, runtime::RuntimeError>> {
        let (tx, rx) = std::sync::mpsc::channel();
        self.cmd_tx
            .send(SessionCmd::RunTurnStreaming {
                session_id: session_id.to_string(),
                user_text: user_text.to_string(),
                event_tx,
                reply: tx,
            })
            .expect("session-manager thread gone");
        rx.recv().expect("session-manager thread gone")
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
        self.active_count
            .load(std::sync::atomic::Ordering::Relaxed)
    }

    pub fn uptime_seconds(&self) -> u64 {
        self.start_time.elapsed().as_secs()
    }
}

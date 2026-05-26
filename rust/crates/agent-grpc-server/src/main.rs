mod composite_executor;
mod config;
mod convert;
mod instruction_card;
mod local_executor;
mod mock;
mod proxy_executor;
mod real_client;
mod service;
mod session_store;
mod skill_engine;

pub mod proto {
    #![allow(
        clippy::doc_markdown,
        clippy::must_use_candidate,
        clippy::default_trait_access,
        clippy::too_many_lines
    )]
    tonic::include_proto!("agent");
}

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use tonic::transport::Server;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;
use tracing_subscriber::EnvFilter;

use crate::proto::agent_service_server::AgentServiceServer;
use crate::service::AgentServiceImpl;
use session_persistence::open_backend;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mock_mode = args.iter().any(|a| a == "--mock");
    let addr: SocketAddr = args
        .iter()
        .position(|a| a == "--addr")
        .and_then(|i| args.get(i + 1))
        .and_then(|s| s.parse().ok())
        .unwrap_or_else(|| "0.0.0.0:50051".parse().unwrap());
    let skills_root: Option<PathBuf> = args
        .iter()
        .position(|a| a == "--skills-dir")
        .and_then(|i| args.get(i + 1))
        .map(PathBuf::from);
    let config_path: PathBuf = args
        .iter()
        .position(|a| a == "--config")
        .and_then(|i| args.get(i + 1))
        .map_or_else(|| PathBuf::from("config.toml"), PathBuf::from);

    let cfg = config::load(&config_path);
    let log_dir = if cfg.logging.dir.is_absolute() {
        cfg.logging.dir.clone()
    } else {
        config_path
            .parent()
            .map(|p| p.join(&cfg.logging.dir))
            .unwrap_or_else(|| cfg.logging.dir.clone())
    };
    if let Err(e) = std::fs::create_dir_all(&log_dir) {
        eprintln!(
            "failed to create log dir {}: {e}; file logging disabled",
            log_dir.display()
        );
    }

    let file_appender = tracing_appender::rolling::daily(&log_dir, &cfg.logging.grpc_prefix);
    let (file_writer, _file_guard) = tracing_appender::non_blocking(file_appender);

    let env_filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let stdout_layer = tracing_subscriber::fmt::layer()
        .with_writer(std::io::stdout)
        .with_target(true);
    let file_layer = tracing_subscriber::fmt::layer()
        .with_writer(file_writer)
        .with_target(true)
        .with_ansi(false);

    tracing_subscriber::registry()
        .with(env_filter)
        .with(stdout_layer)
        .with(file_layer)
        .init();

    tracing::info!(
        log_dir = %log_dir.display(),
        prefix = %cfg.logging.grpc_prefix,
        config = %config_path.display(),
        "logging initialised (stdout + daily rolling file)"
    );

    let data_dir: PathBuf = args
        .iter()
        .position(|a| a == "--data-dir")
        .and_then(|i| args.get(i + 1))
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            let exe_dir = std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|p| p.to_path_buf()))
                .unwrap_or_else(|| PathBuf::from("."));
            exe_dir.join("data")
        });
    let backend_kind = args
        .iter()
        .position(|a| a == "--backend")
        .and_then(|i| args.get(i + 1))
        .map(String::from)
        .unwrap_or_else(|| "jsonl".to_string());

    let backend = match open_backend(&backend_kind, &data_dir) {
        Ok(b) => b,
        Err(e) => {
            eprintln!("failed to open session backend ({backend_kind}): {e}");
            std::process::exit(1);
        }
    };

    tracing::info!(
        backend_kind = %backend_kind,
        data_dir = %data_dir.display(),
        "session backend opened"
    );

    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("failed to build tokio runtime");

    rt.block_on(async move {
        let service = AgentServiceImpl::new(mock_mode, skills_root, Arc::from(backend));
        tracing::info!(%addr, mock_mode, "agent-grpc-server listening");
        Server::builder()
            .add_service(AgentServiceServer::new(service))
            .serve(addr)
            .await
            .expect("gRPC server failed");
    });
}
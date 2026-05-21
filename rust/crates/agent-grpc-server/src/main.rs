mod convert;
mod mock;
mod real_client;
mod service;
mod session_store;

pub mod proto {
    #![allow(clippy::doc_markdown, clippy::must_use_candidate, clippy::default_trait_access, clippy::too_many_lines)]
    tonic::include_proto!("agent");
}

use std::net::SocketAddr;
use tonic::transport::Server;

use crate::proto::agent_service_server::AgentServiceServer;
use crate::service::AgentServiceImpl;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mock_mode = args.iter().any(|a| a == "--mock");
    let addr: SocketAddr = args
        .iter()
        .position(|a| a == "--addr")
        .and_then(|i| args.get(i + 1))
        .and_then(|s| s.parse().ok())
        .unwrap_or_else(|| "0.0.0.0:50051".parse().unwrap());

    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("failed to build tokio runtime");

    rt.block_on(async move {
        let service = AgentServiceImpl::new(mock_mode);
        eprintln!(
            "agent-grpc-server listening on {addr} (mock_mode={mock_mode})"
        );
        Server::builder()
            .add_service(AgentServiceServer::new(service))
            .serve(addr)
            .await
            .expect("gRPC server failed");
    });
}

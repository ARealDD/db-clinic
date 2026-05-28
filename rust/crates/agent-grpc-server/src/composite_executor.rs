use std::collections::HashSet;

use async_trait::async_trait;
use runtime::{ToolError, ToolExecutor};

use crate::local_executor::LocalToolExecutor;
use crate::proxy_executor::ProxyToolExecutor;

/// Routes tool calls to one of three back-ends:
/// - **Local**: executed by the kernel via `LocalToolExecutor`, which wraps
///   `tools::GlobalToolRegistry` (real `read_file`, `write_file`, `edit_file`,
///   `glob_search`, `grep_search`, ...).
/// - **Proxy**: serialized as an `InstructionCard` and shipped to the operator
///   via the bidirectional gRPC stream; the operator returns the textual result.
/// - **Disabled**: tools that are intentionally blocked at runtime (network,
///   MCP) to preserve the air-gapped property of the kernel.
pub struct CompositeToolExecutor {
    local: LocalToolExecutor,
    proxy: ProxyToolExecutor,
    proxy_tools: HashSet<&'static str>,
    disabled_tools: HashSet<&'static str>,
}

impl CompositeToolExecutor {
    pub fn new(local: LocalToolExecutor, proxy: ProxyToolExecutor) -> Self {
        Self {
            local,
            proxy,
            proxy_tools: proxy_tool_names(),
            disabled_tools: disabled_tool_names(),
        }
    }
}

#[async_trait]
impl ToolExecutor for CompositeToolExecutor {
    async fn execute(&mut self, tool_name: &str, input: &str) -> Result<String, ToolError> {
        if self.disabled_tools.contains(tool_name) {
            tracing::debug!(tool_name, "CompositeToolExecutor: rejecting disabled tool");
            return Err(ToolError::new(format!(
                "tool `{tool_name}` is disabled in this deployment (network/MCP tools are off)",
            )));
        }
        if self.proxy_tools.contains(tool_name) {
            tracing::debug!(tool_name, "CompositeToolExecutor: routing to proxy");
            return self.proxy.execute(tool_name, input).await;
        }
        tracing::debug!(tool_name, "CompositeToolExecutor: routing to local");
        self.local.execute(tool_name, input).await
    }
}

/// Tools that produce side effects in the user's production environment.
/// These are routed through the operator instead of executed locally.
fn proxy_tool_names() -> HashSet<&'static str> {
    [
        "bash",
        "Bash",
        "powershell",
        "PowerShell",
        "PowershellTool",
        "repl",
        "REPL",
        "ReplTool",
    ]
    .into_iter()
    .collect()
}

/// Tools that are intentionally disabled at runtime. The code paths still
/// exist in upstream crates but every invocation here is rejected so the
/// kernel cannot reach the internet or external services.
fn disabled_tool_names() -> HashSet<&'static str> {
    [
        "WebFetch",
        "WebSearch",
        "RemoteTrigger",
        "MCP",
        "ListMcpResources",
        "ReadMcpResource",
        "McpAuth",
    ]
    .into_iter()
    .collect()
}

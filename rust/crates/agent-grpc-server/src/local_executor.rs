use async_trait::async_trait;
use runtime::{ToolError, ToolExecutor};
use serde_json::Value;
use tools::GlobalToolRegistry;

/// Executes built-in tools (`read_file`, `write_file`, `edit_file`,
/// `glob_search`, `grep_search`, ...) by delegating to
/// `tools::GlobalToolRegistry`. Tools that must run in the operator's
/// environment (bash/PowerShell/REPL) are routed through `ProxyToolExecutor`
/// *before* a call reaches this executor, so the inputs we see here are always
/// safe to dispatch locally against the kernel's own filesystem.
pub struct LocalToolExecutor {
    registry: GlobalToolRegistry,
}

impl LocalToolExecutor {
    pub fn new() -> Self {
        Self {
            registry: GlobalToolRegistry::builtin(),
        }
    }
}

impl Default for LocalToolExecutor {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl ToolExecutor for LocalToolExecutor {
    async fn execute(&mut self, tool_name: &str, input: &str) -> Result<String, ToolError> {
        let value: Value = if input.trim().is_empty() {
            Value::Object(serde_json::Map::new())
        } else {
            serde_json::from_str(input)
                .map_err(|e| ToolError::new(format!("invalid tool input json: {e}")))?
        };
        self.registry
            .execute(tool_name, &value)
            .map_err(ToolError::new)
    }
}

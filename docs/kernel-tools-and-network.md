# Agent 内核：工具体系与联网机制

本文档总结 db-clinic 中 Rust agent 内核（`rust/crates/`）当前提供的工具能力、并发模型，以及所有可能产生外部网络通信的入口与禁用方法。

---

## 一、工具发现机制

**位置**: [tools/src/lib.rs](../rust/crates/tools/src/lib.rs)

内核采用 **静态注册 + 动态扩展** 的三层结构：

```
GlobalToolRegistry (统一聚合层)
    ├── Built-in Tools     ← mvp_tool_specs() 静态返回 58 个工具
    ├── Plugin Tools       ← PluginToolDefinition，启动时加载
    ├── Runtime Tools      ← RuntimeToolDefinition，运行时注册
    └── MCP Tools          ← McpServerManager.discover_tools() 动态发现
                             命名带前缀：mcp__{server}__{tool}
```

### 关键流程

1. **注册阶段**：每个工具定义 `ToolSpec { name, description, input_schema, required_permission }`
2. **聚合阶段**：`GlobalToolRegistry::definitions()` 按 `allowed_tools` allowlist 过滤
3. **调度阶段**：LLM 返回 `tool_use` → `ToolExecutor::execute()` → 权限检查 → Hook 拦截 → 真实执行

### 执行器与权限

- **执行器**：`MockToolExecutor`（当前 gRPC server 用，返回 stub） vs 真实执行（`GlobalToolRegistry::execute()` 按 tool_name 分派）
- **权限模式**：`ReadOnly` / `WorkspaceWrite` / `DangerFullAccess` / `Prompt` / `Allow` 五级

---

## 二、全局工具清单（共 58 个）

| 分类 | 工具 | 权限 |
|------|------|------|
| **文件操作** | `read_file`, `write_file`, `edit_file`, `NotebookEdit` | ReadOnly / WorkspaceWrite |
| **搜索查询** | `glob_search`, `grep_search`, `LSP` | ReadOnly |
| **Web** | `WebFetch`, `WebSearch` | ReadOnly |
| **执行环境** | `bash`, `PowerShell`, `REPL` | DangerFullAccess |
| **任务管理** | `TaskCreate`, `TaskGet`, `TaskList`, `TaskStop`, `TaskUpdate`, `TaskOutput`, `RunTaskPacket` | 读 ReadOnly / 写 DangerFullAccess |
| **Worker 管理** | `WorkerCreate/Get/Observe/ResolveTrust/AwaitReady/SendPrompt/Restart/Terminate/ObserveCompletion` | 多数 DangerFullAccess |
| **团队协作** | `TeamCreate`, `TeamDelete` | DangerFullAccess |
| **定时调度** | `CronCreate`, `CronDelete`, `CronList` | 写 DangerFullAccess |
| **Agent/Skill** | `Agent`, `Skill`, `ToolSearch` | 多为 ReadOnly |
| **规划配置** | `TodoWrite`, `Config`, `EnterPlanMode`, `ExitPlanMode` | WorkspaceWrite |
| **用户交互** | `AskUserQuestion`, `SendUserMessage` (alias `Brief`) | ReadOnly |
| **MCP 集成** | `ListMcpResources`, `ReadMcpResource`, `McpAuth`, `MCP` | 读 ReadOnly / 调用 DangerFullAccess |
| **网络** | `RemoteTrigger` | DangerFullAccess |
| **工具** | `Sleep`, `StructuredOutput`, `TestingPermission` | ReadOnly |

完整定义参考 [tools/src/lib.rs:393 `mvp_tool_specs()`](../rust/crates/tools/src/lib.rs)。

---

## 三、Multi-Agent 框架

### Agent 类型与白名单

**位置**: [tools/src/lib.rs:3793-3872](../rust/crates/tools/src/lib.rs)

| Agent 类型 | 工具白名单 | 定位 |
|-----------|-----------|------|
| **Explore** | `read_file`, `glob_search`, `grep_search`, `WebFetch`, `WebSearch`, `ToolSearch`, `Skill`, `StructuredOutput` | 纯只读探索 |
| **Plan** | Explore 全集 + `TodoWrite`, `SendUserMessage` | 只读 + 规划 + 汇报 |
| **Verification** | `bash`, `PowerShell` + 读类工具 + `TodoWrite`, `SendUserMessage` | 可执行验证 |
| **claw-guide** | 读类工具 + `Skill`, `SendUserMessage` | 引导咨询 |
| **statusline-setup** | `bash`, `read_file`, `write_file`, `edit_file`, `glob_search`, `grep_search`, `ToolSearch` | 配置专用 |
| **general-purpose** | 17 个工具（含写、执行、Skill、REPL、NotebookEdit、Config 等） | 全能默认 |

**关键观察**:
- Explore/Plan **无 `Agent` 工具** → 不能嵌套派生
- 所有 sub-agent **都没有 `Agent` 工具** → 仅根 agent 可派生，避免无限递归
- 只有 Verification/statusline-setup/general-purpose 能执行 shell

### 隔离机制

每个 sub-agent 拥有独立：

1. **会话上下文** — 独立 `Session::new()`，不共享对话历史
2. **工具注册表** — `SubagentToolExecutor` 通过 `allowed_tools: BTreeSet<String>` 强制过滤
3. **权限策略** — `agent_permission_policy()` 单独配置
4. **API 客户端** — 独立 `ProviderRuntimeClient` + 独立 tokio runtime
5. **系统提示** — 自动注入 `"You are a background sub-agent of type '{type}'..."`

**生命周期**: 派生在独立 OS 线程，唯一 ID `agent-{nanos}`，最大迭代 32 次，状态持久化到 `.clawd-agents/`。

### 通信机制

`SendUserMessage`（alias `Brief`）：子 agent → 父 agent 的唯一通信通道，可附带文件附件。

### 管理命令

`/agents`、`/agent`、`/subagent` 三个 slash 命令（CLI 模式可用，gRPC 模式未暴露）。

---

## 四、工具并发模型

### 核心结论

内核**没有显式的并发声明**或 lock manager，并发完全由**调度层位置**决定。

### 调度层强制约束

**位置**: [conversation.rs:404](../rust/crates/runtime/src/conversation.rs)

```rust
for (tool_use_id, tool_name, input) in pending_tool_uses {
    let result = self.tool_executor.execute(&tool_name, &effective_input);
    ...
}
```

LLM 同一轮返回的所有 `tool_use` 块由内核 **串行** 执行（for 循环，非 join_all）。`ToolSpec` **没有** `parallel_safe` / `exclusive` 字段。

### 并发安全矩阵

| 场景 | 是否并发 | 是否安全 |
|------|---------|---------|
| 同 turn、同工具、同文件 | ❌ 串行 | ✅ 安全 |
| 同 turn、同工具、不同文件 | ❌ 串行 | ✅ 安全（失去并行收益）|
| 同 turn、不同工具、同文件 | ❌ 串行 | ✅ 安全 |
| 同 turn 派生多个 `Agent` / `TaskCreate` | ✅ 真并发 | ⚠️ 不安全：可能同文件竞写 |
| 多个 `bash --background` 操作同文件 | ✅ 真并发 | ⚠️ 不安全 |
| 多 session 同时操作同文件 | ✅ 真并发 | ⚠️ 不安全 |

### 同步执行类（被 for 循环序列化）

`read_file` / `write_file` / `edit_file` / `NotebookEdit` / `glob_search` / `grep_search` / `LSP` / `bash`(前台) / `PowerShell` / `REPL` / `WebFetch` / `WebSearch` / `MCP` / `Skill` / `ToolSearch` / `TodoWrite` 等。

**关键观察**：文件操作 **没有任何同文件锁**（[file_ops.rs](../rust/crates/runtime/src/file_ops.rs) 直接用 `std::fs`），但因为同步调度串行化，单 turn 内不会真正并发。**跨 turn 或多 session 同时写同一文件，没有保护。**

### 异步派生类（脱离 turn 调度）

[lib.rs:3711](../rust/crates/tools/src/lib.rs) 用 `std::thread::Builder::spawn()` 派生独立 OS 线程：

| 工具 | 派生后 | 同资源并发风险 |
|------|--------|--------------|
| `bash`（`run_in_background: true`） | 独立子进程 | 多个后台命令可同时写同一文件 |
| `TaskCreate` / `WorkerCreate` | 独立线程 + 子会话 | 完全并发，无协调 |
| `Agent` | 独立 OS 线程 + 独立 ConversationRuntime | 子 agent 可与父 turn 并发写文件 |
| `CronCreate` | 调度器线程 | 触发时与主流程并发 |

调用本身被串行执行，但 spawn 出去的后台单元彼此和主 turn 都是真并发，**没有任何资源协调机制**。

---

## 五、联网机制与禁用方案

### 联网点总览

| 类别 | 入口 | 默认目的地 | 当前 db-clinic 状态 |
|------|------|----------|------------------|
| **LLM API** | `RealApiClient` | api.anthropic.com / api.openai.com 等 | ✅ 已用，必需 |
| **Web 工具** | `WebFetch`, `WebSearch`, `RemoteTrigger` | 任意 URL / DuckDuckGo | ❌ 未启用（MockToolExecutor 屏蔽） |
| **MCP 第三方 server** | `MCP`, `ListMcpResources`, `ReadMcpResource`, `McpAuth` | settings.json 中配置的 SSE/HTTP/WS server | ❌ 未配置任何 server |
| **遥测/日志上传** | `telemetry` crate | **无外部 sink，仅本地 JSONL** | ✅ 本地，无外发 |
| **插件市场** | `plugins` crate | **无网络下载** | ✅ 仅本地文件 |
| **更新检查 / 心跳** | — | **不存在** | ✅ 无 |

### 1. LLM API（唯一必需的联网通道）

**位置**:
- [api/src/providers/anthropic.rs:25](../rust/crates/api/src/providers/anthropic.rs) — 默认 `https://api.anthropic.com`
- [api/src/providers/openai_compat.rs:19-21](../rust/crates/api/src/providers/openai_compat.rs) — OpenAI / xAI / DashScope
- [api/src/http_client.rs](../rust/crates/api/src/http_client.rs) — `reqwest` 客户端，遵循 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`

**当前 db-clinic 接入方式**: `session_store.rs` 通过 `ApiConfig { provider, api_key, base_url }` 配置，**base_url 可指向内网代理或本地推理服务**。

**禁用/重定向**:
- 改用 `--mock` 启动 → 全程不发任何外网请求
- 或在 settings 设 `base_url: http://内网LLM:port`

### 2. Web 工具（当前都不可用）

| 工具 | 行为 | 禁用方式 |
|------|------|---------|
| `WebFetch` | 抓取任意 URL（[tools/src/lib.rs:2891](../rust/crates/tools/src/lib.rs)） | ① MockToolExecutor 已屏蔽；② 切真实执行器后从 `mvp_tool_specs()` 移除；③ 用 session allowlist 排除 |
| `WebSearch` | 默认调用 DuckDuckGo HTML 接口（[tools/src/lib.rs:3010](../rust/crates/tools/src/lib.rs)） | 同上；或 `CLAWD_WEB_SEARCH_BASE_URL=http://localhost:1` 强制失败 |
| `RemoteTrigger` | 触发任意 webhook（[tools/src/lib.rs:1752](../rust/crates/tools/src/lib.rs)） | 同上 |

**当前现状**: gRPC server 用 `MockToolExecutor`，这 3 个工具的真实代码路径根本走不到。即使未来换真实执行器，也只需要在 db-clinic 的工具白名单里**不放它们**。

### 3. MCP 第三方 server（未启用，但代码已就绪）

**位置**:
- [runtime/src/mcp_client.rs](../rust/crates/runtime/src/mcp_client.rs) — 支持 SSE / HTTP / WebSocket / 托管代理 4 种远程连接方式
- [runtime/src/mcp_stdio.rs](../rust/crates/runtime/src/mcp_stdio.rs) — 本地 stdio 子进程（不联网）
- [runtime/src/oauth.rs](../rust/crates/runtime/src/oauth.rs) — MCP server 可能要求 OAuth token 刷新
- 配置源：settings.json 的 `mcp.servers.*`

**当前现状**: agent-grpc-server 启动时**没有任何 MCP server 注册**，整个 MCP 子系统处于休眠态。

**永久禁用**:
- 不在 settings.json 添加任何 `mcp.servers.*` 条目（等于已禁用）
- 如要强保险：在 `AgentServiceImpl::new()` 里不调用 MCP 初始化路径

### 4. 遥测（本地落盘，无网络）

[telemetry/src/lib.rs](../rust/crates/telemetry/src/lib.rs) 只提供两种 sink：
- `MemoryTelemetrySink` — 进程内 ring buffer
- `JsonlTelemetrySink` — 本地 JSONL 文件

**没有任何**外部 sink，无 Sentry / DataDog / OTLP 等 SDK 依赖。

### 5. 插件系统（纯本地文件）

[plugins/src/lib.rs](../rust/crates/plugins/src/lib.rs) 的 "marketplace" 命名只是分类标签（builtin/bundled/external），**没有任何下载机制** —— 插件来自本地 `~/.claw/plugins/` 等目录。

---

## 六、db-clinic 全面断网清单

### 现状已具备的"断网保护"

| 项 | 现状 |
|----|------|
| Web 工具（WebFetch/WebSearch/RemoteTrigger） | 被 MockToolExecutor 屏蔽，**调用不到** |
| MCP 远程 server | settings.json 无配置，**未启用** |
| 遥测上传 | 设计上就**只落本地文件** |
| 插件市场 | **不存在** |
| 自动更新 / 心跳 | **不存在** |

### LLM 通道控制（需主动选择）

| 方案 | 适用场景 |
|------|---------|
| `--mock` 启动 | 完全离线演示/开发 |
| `base_url` 指向内网 LLM | 私有模型推理 |
| 默认 `api.anthropic.com` | 标准联网 |

### 防御性加固（如果担心后续接入真实工具时漏挡）

1. **代码层移除**: 在 [tools/src/lib.rs](../rust/crates/tools/src/lib.rs) 的 `mvp_tool_specs()` 把 `WebFetch` / `WebSearch` / `RemoteTrigger` 三项删除或 `#[cfg(feature = "web_tools")]` 包裹
2. **运行时白名单**: 切换到真实 `ToolExecutor` 后，给 session 设置 `allowed_tools`，排除联网类工具
3. **MCP 完全禁用**: `AgentServiceImpl::new()` 里不传任何 MCP server 配置，并可在代码中直接 `#[cfg(not(feature = "mcp"))]` 屏蔽 `mcp_tool_bridge`
4. **环境变量兜底**:
   ```bash
   export NO_PROXY="*"             # reqwest 不走代理
   export HTTP_PROXY=""            # 清空代理
   export CLAWD_WEB_SEARCH_BASE_URL="http://127.0.0.1:1"  # 强制失败
   ```
5. **进程级网络隔离**: 容器化部署时用 `--network=none` 加 gRPC server 反向代理 LLM API

### 最小动作（当前阶段确保零外联）

1. 用 `scripts/start-dev.sh --mock` 启动（已是默认）→ 不调用任何 LLM API
2. 不动其他东西 → Web 工具、MCP、遥测、插件**本来就没启用**

将来切到真实 LLM 且接入真实工具执行器时，再回头做：
- 工具白名单排除 Web/RemoteTrigger
- 配置 LLM `base_url` 指向你信任的端点
- settings.json 保持 MCP 配置为空

---

## 七、能力与现状对比

| 能力 | 内核支持 | 当前 gRPC server 接入度 |
|------|---------|----------------------|
| 工具发现 (58 内置 + 插件 + MCP) | ✅ 完整 | ❌ 用 MockToolExecutor，工具未真实执行 |
| 多 agent 派生与隔离 | ✅ 完整 | ❌ `Agent` 工具不可用（被 mock 拦截） |
| 权限策略 5 级 | ✅ 完整 | ⚠️ 当前 session 固定 `PermissionMode::Allow` |
| MCP server 集成 | ✅ 完整 | ❌ 未注册任何 MCP server |
| 工具 allowlist 按 session 过滤 | ✅ 支持 | ❌ 无 session-level 过滤 |
| 遥测上传 | — | ✅ 本地落盘，无外发 |
| 联网工具 (WebFetch/WebSearch/RemoteTrigger) | ✅ 完整 | ❌ Mock 屏蔽，零调用 |

**核心结论**: 内核的工具体系和 multi-agent 框架都很完整，但 db-clinic 的 gRPC server 当前只接入了 LLM 文本生成 + Skill 注入，**所有工具能力都被 MockToolExecutor 屏蔽**。从联网视角看，这反而是天然的"零外联"状态——只要不主动切换执行器、不配 MCP server、不改默认 LLM `base_url`，整个服务即可在离线环境运行。

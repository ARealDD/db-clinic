# db-clinic

## 1. Windows / Linux

### 环境准备 (Prerequisites)

```bash
# Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# Python 依赖
pip install grpcio grpcio-tools fastapi uvicorn websockets pydantic PyYAML
```

### 编译 (Build)

```bash
scripts/build.sh            # 完整构建：proto stubs + Rust workspace + Python 依赖检查
scripts/build.sh --rust     # 仅编译 Rust
scripts/build.sh --proto    # 仅重新生成 proto stubs
scripts/build.sh --check    # CI 模式：fmt check + clippy + tests
```

## 2. macOS

### 环境准备 (Prerequisites)

macOS 环境配置详见 [scripts/mac_setup/README.md](scripts/mac_setup/README.md)。

```bash
# conda 环境
conda create -n db-clinic python=3.10 -y
conda activate db-clinic
pip install -r scripts/mac_setup/requirements.txt

# Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# Protocol Buffers
brew install protobuf
```

### 编译 (Build)

```bash
scripts/mac_setup/build.sh              # 完整构建
scripts/mac_setup/build.sh --setup-only # 仅检查/安装依赖
scripts/mac_setup/build.sh --rust       # 仅编译 Rust
scripts/mac_setup/build.sh --proto      # 仅重新生成 proto stubs
scripts/mac_setup/build.sh --check      # CI 模式：fmt check + clippy + tests
scripts/mac_setup/build.sh --yes        # 自动安装缺失的 pip 包
```

## 3. 启动服务 (Usage)

Windows、Linux、macOS 使用相同的命令格式：

```bash
# 编译 + 启动
scripts/start-dev.sh                    # Windows / Linux
scripts/mac_setup/start_dev.sh          # macOS

# 可用参数
--skip-build    # 跳过编译，直接启动
--mock          # 强制 mock LLM 模式
--port 9000     # 自定义 FastAPI 端口（默认 8001）
```

**停止服务：** 按 `Ctrl+C`，gRPC server 和 FastAPI gateway 会同时关闭。

### 启动后的服务

| 服务 | 地址 |
|---|---|
| gRPC server | localhost:50051 |
| Web UI | http://localhost:8001 |

## 4. 前端 UI (React + TypeScript)

前端位于 `ui/` 目录，基于 Vite + React + TypeScript。构建产物输出到 `python/static/`，由 FastAPI 直接托管。

### 环境准备

需要 Node.js 18+。

### 安装 & 开发

```bash
cd ui
npm install
npm run dev              # 启动 Vite 开发服务器（端口 3000，API 代理到 8001）
```

开发服务器会自动代理 `/api` 和 `/ws` 请求到 Python 后端。

### 生产构建

```bash
cd ui
npm run build            # 构建产物输出到 python/static/assets/
```

> **注意**：`python/static/assets/` 已加入 `.gitignore`，新克隆/部署的机器**必须先跑一次 `npm run build`** 才能让 FastAPI（:8001）正常返回前端页面。Vite 开发模式（:3000）不受影响。

构建后重启 Python 服务器即可使用新的前端页面。页面路由由 React Router 处理：

| 路径 | 页面 |
|------|------|
| `/` | 登录页（输入用户名 → 跳转到设置） |
| `/chat` | 诊断聊天（WebSocket 流式对话） |
| `/settings` | LLM 模型配置 |
| `/skills-square` | 技能广场（浏览和克隆技能） |
| `/my-skills` | 我的仓库（管理个人技能） |

## 5. 配置文件 (config.toml)

仓库根目录的 [`config.toml`](config.toml) 是 Rust gRPC server 与 Python FastAPI gateway 共享的运行时配置。两端启动时分别读取，约定的字段如下：

### `[storage]` — 持久化目录

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `data_dir` | `"data"` | 持久化根目录（相对路径基于仓库根解析）。包含 SQLite 元数据库和每会话 JSONL 流水。启动时自动创建。 |
| `db_file` | `"metadatabase.db"` | SQLite 元数据库文件名，位于 `data_dir` 下。存放用户账户、LLM 配置等。 |
| `sessions_subdir` | `"sessions"` | 会话 JSONL 子目录名。文件按 `{username}/{session_id}.jsonl` 写入。 |

### `[logging]` — 日志与按天滚动

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `dir` | `"logs"` | 滚动日志输出目录，启动时自动创建。 |
| `grpc_prefix` | `"agent-grpc-server"` | Rust 端日志文件前缀，按天追加 `YYYY-MM-DD`。 |
| `gateway_prefix` | `"gateway"` | Python 端日志文件前缀。 |
| `retention_days` | `14` | 保留多少天的旧日志。**仅 Python 端生效**（`TimedRotatingFileHandler.backupCount`）；Rust 端 `tracing-appender` 不会自动清理，需要手工或定时清理。 |
| `prompt_log_dir` | `"logs/prompts"` | 全量 prompt JSONL 输出目录。 |
| `prompt_log_prefix_grpc` | `"prompt-grpc"` | Rust 端：每次 `run_turn` 调用追加一条记录（含系统提示词合并 + skill_context 增强后的最终内容）。 |
| `prompt_log_prefix_gateway` | `"prompt-gateway"` | Python 端：每个 WebSocket `user_message` 帧追加一条（含操作员原始输入 + context attachments）。两端记录都带 `session_id`，可用 `jq -s 'group_by(.session_id)'` 关联。 |

### 关键环境变量

`config.toml` 不覆盖以下环境变量；它们由启动脚本或调用方注入：

| 变量 | 用途 | 设置位置 |
|------|------|----------|
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 启动时自动创建/更新的管理员账户。**留空则跳过**。 | [`scripts/start-dev.sh`](scripts/start-dev.sh) 默认设置为 `admin` / `Gauss_234`。生产部署应通过 systemd unit / docker env 注入。 |
| `GRPC_ADDR` | Python gateway 连接 Rust gRPC server 的地址。 | `start-dev.sh` 默认 `localhost:50051`。 |

LLM API key、模型选择由前端「设置」页存入数据库，每用户独立，**不通过 config.toml 配置**。

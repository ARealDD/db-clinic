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
npm run build            # 构建产物输出到 python/static/
```

构建后重启 Python 服务器即可使用新的前端页面。页面路由由 React Router 处理：

| 路径 | 页面 |
|------|------|
| `/` | 登录页（输入用户名 → 跳转到设置） |
| `/chat` | 诊断聊天（WebSocket 流式对话） |
| `/settings` | LLM 模型配置 |
| `/skills-square` | 技能广场（浏览和克隆技能） |
| `/my-skills` | 我的仓库（管理个人技能） |

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
| Settings（模型配置） | http://localhost:8001/static/settings.html |
| Skills API | http://localhost:8001/api/skills |

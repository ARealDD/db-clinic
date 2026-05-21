# scripts/build.sh — 编译脚本

- scripts/build.sh            # 完整构建：proto stubs + Rust workspace + Python 依赖检查
- scripts/build.sh --rust     # 仅编译 Rust
- scripts/build.sh --proto    # 仅重新生成 proto stubs
- scripts/build.sh --check    # CI 模式：fmt check + clippy + tests

# scripts/start-dev.sh — 启动脚本

- scripts/start-dev.sh              # 编译 + 启动两个服务
- scripts/start-dev.sh --skip-build # 跳过编译，直接启动
- scripts/start-dev.sh --mock       # 强制 mock LLM 模式
- scripts/start-dev.sh --port 9000  # 自定义 FastAPI 端口（默认 8001）

# 启动后的服务：

- gRPC server: localhost:50051
- Web UI: http://localhost:8001
- Settings: http://localhost:8001/static/settings.html
- Skills API: http://localhost:8001/api/skills
- Ctrl+C 会同时关闭两个服务。

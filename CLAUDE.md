# CLAUDE.md

# 项目现状

## 技术栈：
- 编程语言：Rust。

## 验证：
- Rust 验证：在仓库根目录运行 scripts/fmt.sh --check 进行检查；如需格式化代码请使用 scripts/fmt.sh。
- Clippy 与测试：在 rust/ 目录下运行 Rust clippy 和测试：cargo clippy --workspace --all-targets -- -D warnings，cargo test --workspace
- 代码与测试同步：src/ 和 tests/ 目录均已存在；当业务逻辑或行为发生变更时，需要同步更新这两部分。

## 仓库结构
- rust/：包含 Rust 工作区以及活跃的 CLI（命令行工具）/运行时实现。
- src/：包含源代码文件，这些文件应与生成的指引和测试保持一致。
- tests/：包含验证层面的文件，在进行代码变更时应一并审查。
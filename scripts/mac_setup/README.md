# macOS Setup Scripts

Scripts for building and running db-clinic on macOS.

## Prerequisites

### 1. Conda Environment

```bash
# Create and activate a conda environment (Python 3.10+)
conda create -n db-clinic python=3.10 -y
conda activate db-clinic

# Install Python packages (or let build.sh prompt to install them)
pip install -r scripts/mac_setup/requirements.txt
```

### 2. Rust Toolchain

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

### 3. Protocol Buffers Compiler

```bash
brew install protobuf
```

## Usage

Make sure your conda environment is activated before running the scripts:

```bash
conda activate db-clinic

# Build + start both services (gRPC on :50051, Web UI on :8001)
scripts/mac_setup/start_dev.sh

# Or step by step:
scripts/mac_setup/build.sh --setup-only   # verify dependencies
scripts/mac_setup/build.sh                # full build
scripts/mac_setup/start_dev.sh --skip-build  # run without rebuilding
```

**To stop:** press `Ctrl+C` — both the gRPC server and FastAPI gateway shut down cleanly.

Once running, open:
- **Web UI:** http://localhost:8001
- **Settings** (model config): http://localhost:8001/static/settings.html
```

The build script auto-detects the conda `python` and will offer to install any missing packages.

## Scripts

| Script | Purpose |
|---|---|
| `build.sh` | Dependency check + proto stubs + Rust build |
| `start_dev.sh` | Build (optional) + launch gRPC server and FastAPI gateway |

### build.sh flags

| Flag | Effect |
|---|---|
| (none) | Full build: setup + proto + Rust + dep check |
| `--rust` | Rust workspace only |
| `--proto` | Regenerate proto stubs only |
| `--check` | CI mode: fmt check + clippy + tests |
| `--setup-only` | Verify/install dependencies, then exit |
| `--yes` / `-y` | Auto-install missing pip packages without prompting |

### start_dev.sh flags

| Flag | Effect |
|---|---|
| (none) | Build + start both services |
| `--skip-build` | Skip build, start existing binaries |
| `--mock` | Force mock LLM mode |
| `--port 9000` | Custom FastAPI port (default 8001) |
| `--grpc-addr 0.0.0.0:50052` | Custom gRPC address |

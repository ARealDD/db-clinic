FROM rust:bookworm

# Install system packages + protoc 28.3 (needed for proto3 optional)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        libssl-dev \
        pkg-config \
        python3 \
        python3-pip \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/* \
    && PROTOC_VER="28.3" \
    && curl -fsSL "https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOC_VER}/protoc-${PROTOC_VER}-linux-x86_64.zip" \
       -o /tmp/protoc.zip \
    && unzip -o /tmp/protoc.zip -d /usr/local bin/protoc \
    && unzip -o /tmp/protoc.zip -d /usr/local 'include/*' \
    && rm -f /tmp/protoc.zip \
    && protoc --version

# Install Python dependencies for FastAPI gateway
RUN pip3 install --no-cache-dir \
      grpcio grpcio-tools fastapi uvicorn pydantic PyYAML \
      aiosqlite python-jose passlib bcrypt cryptography

ENV CARGO_TERM_COLOR=always
WORKDIR /workspace
CMD ["bash"]

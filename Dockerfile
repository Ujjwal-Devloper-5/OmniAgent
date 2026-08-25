# ============================================================
# Multi-stage optimised Docker build for OmniAgent
# Stage 1: dependency builder
# Stage 2: minimal production image
# ============================================================

# ---- Stage 1: Builder ----
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set uv link mode to copy — prevents "Failed to hardlink files" warning.
# In Docker, the uv cache and the .venv are always on different overlay
# filesystem layers, so hardlinks are impossible. copy mode is the correct
# approach and performs identically at image-build time.
ENV UV_LINK_MODE=copy

WORKDIR /app

# Copy only dependency files first (Docker layer cache)
COPY pyproject.toml uv.lock ./

# Install deps into a virtual environment, no project itself.
# --mount=type=cache keeps downloaded wheels in BuildKit cache → faster rebuilds.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Now copy the rest of the project
COPY . .

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen


# ---- Stage 2: Production runner ----
FROM python:3.12-slim AS production

# Install Docker CLI (needed for sandbox tool to exec docker commands via mounted socket)
# We only need the CLI binary, not the daemon
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        ca-certificates curl gnupg lsb-release && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends docker-ce-cli && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Security: run as non-root, but in docker group for sandbox socket access
RUN groupadd --gid 1001 omniagent && \
    useradd --uid 1001 --gid omniagent --shell /bin/bash --create-home omniagent && \
    groupadd --gid 999 docker 2>/dev/null || true && \
    usermod -aG docker omniagent

# Copy uv binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy the fully installed project from builder
COPY --from=builder /app /app

# Create data and logs dirs with correct ownership
RUN mkdir -p /app/data /app/logs && chown -R omniagent:omniagent /app

USER omniagent

# Health check: verify Python can import our main module
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import main" || exit 1

CMD ["uv", "run", "python", "main.py"]

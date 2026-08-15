# ============================================================
# Multi-stage optimised Docker build for OmniAgent
# Stage 1: dependency builder
# Stage 2: minimal production image
# ============================================================

# ---- Stage 1: Builder ----
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy only dependency files first (Docker layer cache)
COPY pyproject.toml uv.lock ./

# Install deps into a virtual environment, no project itself
RUN uv sync --frozen --no-install-project

# Now copy the rest of the project
COPY . .

# Install the project itself
RUN uv sync --frozen


# ---- Stage 2: Production runner ----
FROM python:3.12-slim AS production

# Security: run as non-root
RUN groupadd --gid 1001 omniagent && \
    useradd --uid 1001 --gid omniagent --shell /bin/bash --create-home omniagent

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

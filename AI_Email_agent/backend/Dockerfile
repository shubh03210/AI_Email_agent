# ══════════════════════════════════════════════════════════════════════════════
# Stage 1 — dependency builder
# Install all Python packages into an isolated virtual environment.
# This stage includes build tools (gcc, libpq-dev) that are NOT needed at
# runtime and would otherwise bloat the final image.
# ══════════════════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Upgrade pip, then install into /opt/venv so it can be cleanly copied.
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 — runtime
# Minimal image: only the installed packages and application code.
# No build tools, no compiler, smaller attack surface.
# ══════════════════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS runtime

# Runtime-only native libraries (no -dev headers, no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for the running process
RUN useradd --uid 1000 --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy the pre-built virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Put venv bin on PATH so `python`, `uvicorn`, `alembic`, `celery` all resolve
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy application source (credentials/ excluded via .dockerignore — mounted at runtime)
COPY . .

# Directories that need to exist at container start
# credentials/ is expected to be a read-only volume mount (see docker-compose.yml)
RUN mkdir -p logs credentials \
    && chown -R appuser:appuser /app

# Make entrypoint script executable
RUN chmod +x entrypoint.sh

USER appuser

EXPOSE 8000

# Liveness probe — confirmed reachable by the orchestrator before traffic is routed
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health/live || exit 1

ENTRYPOINT ["./entrypoint.sh"]

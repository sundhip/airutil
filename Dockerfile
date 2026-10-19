# ==============================================================================
# Airutil Multi-Stage Dockerfile
#
# OS Concepts Demonstrated:
# 1. OverlayFS & Layer Optimization: Multi-stage builds keep compiler toolchains
#    out of production image layers, minimizing image footprint and attack surface.
# 2. Linux UID/GID Namespace Isolation: Running as non-root user (airutil:10001)
#    enforces the principle of least privilege against host container breakouts.
# 3. Process Execution & Signals: Gunicorn acts as the master process managing
#    worker forks, handling SIGTERM/SIGINT signals cleanly for container lifecycle.
# 4. Kernel Health Signaling: Docker HEALTHCHECK leverages process exit codes
#    to communicate container readiness to the container engine.
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Builder
# Compiles dependencies and wheels inside an isolated build layer
# ------------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Set build environment variables (OS concept: Environment Variables & Subprocesses)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build dependencies for C-extensions (e.g., psycopg2, cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment to isolate Python package binaries
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# ------------------------------------------------------------------------------
# Stage 2: Runtime
# Minimal hardened production runtime layer
# ------------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Set runtime environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=5000 \
    STORAGE_PATH=/vault_storage

# Install minimal runtime dependencies (curl for healthcheck, libpq for Postgres)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    procps \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# OS Concept: User Namespaces & Least Privilege
# Create a dedicated non-root user with explicit UID/GID (10001)
RUN groupadd -g 10001 airutil && \
    useradd -u 10001 -g airutil -s /bin/false -m airutil

# Copy pre-built virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Set up application workspace
WORKDIR /workspace

# Create storage mount directory and set permissions for non-root user
RUN mkdir -p /vault_storage /tmp && \
    chown -R airutil:airutil /vault_storage /tmp /workspace

# Copy application code with non-root ownership
COPY --chown=airutil:airutil app/ ./app/
COPY --chown=airutil:airutil worker/ ./worker/
COPY --chown=airutil:airutil tests/ ./tests/

# Switch execution context to non-root user
USER airutil

# Expose internal application port
EXPOSE 5000

# OS Concept: Healthchecks & Exit Codes
# Signal container health to Docker daemon via HTTP /healthz probe
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:5000/healthz || exit 1

# Launch application via Gunicorn with 2 pre-fork worker processes
CMD ["gunicorn", "--workers=2", "--bind=0.0.0.0:5000", "--access-logfile=-", "--error-logfile=-", "app.main:app"]

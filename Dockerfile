# =============================================================================
# PACCA Dockerfile
# Multi-stage build for optimized production image
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Build dependencies
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install pip and build tools
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy package metadata, README (referenced by pyproject's readme field), and
# source. Hatchling's metadata generation requires all three to produce a valid
# wheel — the deps-only / source-second caching split that works with
# poetry-no-root does not apply under hatchling.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the package and its declared dependencies
RUN pip install --no-cache-dir .

# -----------------------------------------------------------------------------
# Stage 2: Production image
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS production

WORKDIR /app

# Create non-root user for security
RUN groupadd -r pacca && useradd -r -g pacca pacca

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code (README.md is needed by hatchling for editable install metadata)
COPY src/ ./src/
COPY pyproject.toml README.md ./

# Copy Alembic config (script_location = src/pacca/db/migrations, read from
# CWD) and the entrypoint that applies migrations before the app starts (C5 —
# Alembic is the single schema source; the app no longer runs create_all).
COPY alembic.ini ./
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# Install the package itself
RUN pip install --no-cache-dir -e .

# Set ownership
RUN chown -R pacca:pacca /app

# Switch to non-root user
USER pacca

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    LOG_LEVEL=INFO

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# Entrypoint runs `alembic upgrade head`, then execs the CMD below.
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Run the application
CMD ["uvicorn", "pacca.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# -----------------------------------------------------------------------------
# Stage 3: Development image (optional target)
# -----------------------------------------------------------------------------
FROM production AS development

USER root

# Install development dependencies
RUN pip install --no-cache-dir ".[dev]"

# Switch back to non-root user
USER pacca

# Development overrides
ENV APP_ENV=development \
    DEBUG=true \
    LOG_LEVEL=DEBUG

# Run with reload for development
CMD ["uvicorn", "pacca.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

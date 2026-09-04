# ============================================
# Stage 1: Builder
# ============================================
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install exact locked dependencies in a source-independent layer.
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --prefix=/install \
    --no-warn-script-location -r requirements.lock

# Build and install the application without duplicating dependency resolution.
COPY pyproject.toml setup.py MANIFEST.in README.md ./
COPY app/ ./app/
COPY translations/ ./translations/
COPY CHANGELOG.md LICENSE ./
RUN pip install --no-cache-dir --no-deps --prefix=/install --no-warn-script-location .

# ============================================
# Stage 2: Runtime
# ============================================
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS runtime

WORKDIR /app

# Apply available Debian security updates and install runtime dependencies only.
# Package versions intentionally follow the current Debian security repository.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    zlib1g \
    fonts-symbola \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Package installation is builder-only; remove pip and its vendored libraries.
RUN PIP_ROOT_USER_ACTION=ignore python -m pip uninstall --yes pip

# Copy and install reset-db script
COPY scripts/reset_db.sh /usr/local/bin/reset-db
RUN chmod +x /usr/local/bin/reset-db

# Copy and install backup CLI script
COPY scripts/backup_cli.py /usr/local/bin/backup
RUN chmod +x /usr/local/bin/backup

# Create non-root user for security
RUN useradd -m -u 1000 -s /bin/bash appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Healthcheck using Python (no extra dependencies needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5m --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.getenv(\"APP_PORT\", \"8000\")}/health/ready').read()" || exit 1

# Expose port
EXPOSE 8000

# Environment variables for better container behavior
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    FORWARDED_ALLOW_IPS=127.0.0.1

# Run application
CMD ["sh", "-c", "exec uvicorn app.main:app --host \"${APP_HOST}\" --port \"${APP_PORT}\" --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS}\""]

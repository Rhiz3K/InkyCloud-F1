# ============================================
# Stage 1: Builder
# ============================================
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml setup.py MANIFEST.in README.md ./
COPY app/ ./app/
COPY translations/ ./translations/

# Upgrade pip and install setuptools/wheel first, then install package
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --prefix=/install --no-warn-script-location .

# ============================================
# Stage 2: Runtime
# ============================================
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies only (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    zlib1g \
    fonts-symbola \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application code
COPY app/ ./app/
COPY translations/ ./translations/
COPY pyproject.toml setup.py MANIFEST.in ./

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
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

# Expose port
EXPOSE 8000

# Environment variables for better container behavior
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Run application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

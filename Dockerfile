# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src"

WORKDIR /app

# Install system utilities needed for production operations
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root system user for secure container execution
RUN groupadd -g 1001 settlegraph && \
    useradd -u 1001 -g settlegraph -m -s /bin/bash settlegraph

# Copy package metadata and source before installation.  Setuptools discovers
# packages under ``src/``, so installing before this copy leaves it with no
# package to build and breaks the release image.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install application and production runtime dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy application source code and assets
COPY datagen/ ./datagen/
COPY scripts/ ./scripts/

# Create data and results directories with appropriate permissions
RUN mkdir -p data/generated results && \
    chown -R settlegraph:settlegraph /app

# Switch to non-root user
USER settlegraph

# Pre-generate financial data and run initial reconciliation
RUN python -m settlegraph.cli generate --total-records 1000 --seed 42 && \
    python -m settlegraph.cli run

# Expose port for Web UI & REST API
EXPOSE 8080

# Configure production health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/healthz || exit 1

# Default command: Start interactive dashboard server
CMD ["python", "-m", "settlegraph.cli", "serve", "--host", "0.0.0.0", "--port", "8080"]

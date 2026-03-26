# Stage 1: Build with uv
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

WORKDIR /app

# Install git (required for git-based dependencies like toon-format)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (without dev dependencies)
# WITH_EXTENSION=0 tells mwparserfromhell to use pure-Python tokenizer (avoids gcc requirement)
RUN WITH_EXTENSION=0 uv sync --frozen --no-dev --no-install-project

# Copy source code
COPY packages/ ./packages/

# Stage 2: Minimal runtime
FROM python:3.14-slim-bookworm

WORKDIR /app

# Install curl for health check
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy source code
COPY --from=builder /app/packages ./packages/

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Expose the API port
EXPOSE 8080

# Run the API server (10 minute timeout for long-running AI requests)
CMD ["uvicorn", "packages.api.app:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "600"]

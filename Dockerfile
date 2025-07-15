FROM python:3.13-alpine

# Install system dependencies needed for rustup and building native extensions.
# 'curl' is for downloading rustup, and 'build-base' provides C compilers, etc.
RUN apk add --no-cache curl build-base

# Install the Rust toolchain using rustup
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

# Add the Rust compiler (cargo) to the system's PATH.
# This makes 'rustc' available to subsequent RUN commands.
ENV PATH="/root/.cargo/bin:${PATH}"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Enable uv optimizations:
# UV_COMPILE_BYTECODE=1 compiles Python bytecode for faster startup
# UV_LINK_MODE=copy ensures dependencies are copied (isolated env)
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Change the working directory to the `app` directory
WORKDIR /app

# Install dependencies
# The "cache" mount allows efficient reuse of a uv cache on rebuilds
RUN --mount=type=cache,target=/root/.cache/uv \
  --mount=type=bind,source=uv.lock,target=uv.lock \
  --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
  uv sync --frozen --no-install-project --no-dev

# Copy the project into the image
COPY . .

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --frozen --no-dev

CMD ["uv", "run", "--no-dev", "uvicorn", "ai_assistant.api:app", "--host", "0.0.0.0", "--port", "8080"]

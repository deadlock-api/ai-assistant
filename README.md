# Deadlock AI Assistant

Intelligent assistant with ClickHouse, Wiki, and API integrations.

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker (optional, for container deployment)

## Setup

### Install Dependencies

```bash
uv sync
```

### Run the API Server

```bash
uv run uvicorn packages.api.app:app --reload
```

The API is available at http://localhost:8000

### Verify Health

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "healthy", "version": "0.1.0"}
```

## Development Commands

### Code Quality

```bash
uv run ruff check .        # Lint
uv run ruff format .       # Format
uv run ty check            # Type check
```

### Testing

```bash
uv run pytest
```

### All Checks (mimics CI)

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```

## CLI/TUI

```bash
uv run deadlock-assistant
```

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+Q   | Quit   |
| Ctrl+L   | Clear  |
| Enter    | Submit |

## Docker

### Build

```bash
docker build -t deadlock-ai-assistant .
```

### Run

```bash
docker run -p 8000:8000 deadlock-ai-assistant
```

## Environment Variables

Copy `.env.example` to `.env` and configure the required variables:

```bash
cp .env.example .env
```

| Variable | Description | Required | Default | Example |
|----------|-------------|----------|---------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude AI | Yes | - | `sk-ant-api03-...` |
| `API_KEYS` | Comma-separated list of valid API keys for authentication | No | - | `key1,key2,key3` |
| `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile secret for browser auth | No | - | `0x4AAA...` |
| `REDIS_URL` | Redis connection URL for conversation storage | No | - | `redis://localhost:6379/0` |
| `LOG_LEVEL` | Application log level | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CONVERSATION_TTL` | Conversation expiration in seconds | No | `86400` (24h) | `3600` |
| `AGENT_TIMEOUT_SECONDS` | AI response timeout in seconds | No | `60` | `120` |

### Required Variables

- **ANTHROPIC_API_KEY**: Required for both API and CLI. Get your key from [Anthropic Console](https://console.anthropic.com/).

### Optional Variables

- **API_KEYS**: Enable API key authentication. If not set, authentication is disabled (not recommended for production).
- **TURNSTILE_SECRET_KEY**: Enable Cloudflare Turnstile verification as an alternative to API keys.
- **REDIS_URL**: Enable persistent conversation history. Without this, conversations are not persisted.
- **LOG_LEVEL**: Control log verbosity. Use `DEBUG` for development.
- **CONVERSATION_TTL**: Control how long conversations are kept in Redis.
- **AGENT_TIMEOUT_SECONDS**: Increase for longer responses; decrease for faster failures.

## API Documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

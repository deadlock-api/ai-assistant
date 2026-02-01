# Deadlock AI Assistant Development Guidelines

## Quick Reference

```bash
uv sync                          # Install dependencies
uv run uvicorn api.app:app       # Run API server
uv run deadlock-assistant        # Run CLI/TUI
uv run ruff check .              # Lint
uv run ruff format .             # Format
uv run ty check                  # Type check
uv run pytest                    # Test
docker build -t deadlock-ai-assistant .  # Build Docker image
```

## Project Structure

```text
packages/
  api/           # FastAPI application, routes, middleware, auth
  cli/           # Terminal user interface (Textual)
  ai_assistant/  # Claude Agent SDK integration, conversation handling, tools
  integrations/  # ClickHouse, Wiki, OpenAPI clients, Python sandbox
```

**Dependency flow**: `api/` and `cli/` → `ai_assistant/` → `integrations/`

## Technology Stack

| Category         | Technology                 |
|------------------|----------------------------|
| Language         | Python >= 3.14             |
| Web Framework    | FastAPI                    |
| LLM SDK          | Claude Agent SDK >= 0.1.27 |
| TUI Framework    | Textual                    |
| Type Checker     | ty                         |
| Linter/Formatter | Ruff                       |
| Package Manager  | uv                         |
| Testing          | pytest                     |
| Database         | ClickHouse                 |
| Cache            | Redis                      |
| Container        | Docker                     |

## Core Principles

### Stateless Architecture

- No in-memory state between requests
- Use Redis for caching and temporary state
- Conversation continuity via `conversation_id` query parameter
- No singleton patterns that accumulate state

### Error Handling

- All external integrations must have explicit error handling with typed exceptions
- Categorize errors: retriable (network timeouts), non-retriable (auth failures), user-facing (invalid queries)
- Log errors with context (request ID, timestamp, integration name, sanitized parameters)
- Configure timeout and retry policies for Claude Agent SDK calls

### Testing

- Test files co-located: `module.py` / `module_test.py` (same directory)
- All LLM/Claude Agent SDK interactions must be mocked in tests
- Integration tests use recorded fixtures or dedicated test instances
- Cover error paths, not just happy paths

### Type Safety

- Complete type annotations on all function signatures
- Avoid `Any` type except for untyped third-party libraries
- Use Pydantic models for API request/response validation
- Zero `ty check` errors required

### Security

- All API requests authenticated via API key or Cloudflare Turnstile token
- Never log or expose API keys in error messages
- Use parameterized queries for ClickHouse (prevent SQL injection)
- Load sensitive config from environment variables only

### Code Quality

- Zero `ruff check` and `ruff format --check` violations
- 120 character line length maximum
- No unused imports, variables, or dead code
- No star imports; use explicit imports only

### Simplicity (YAGNI)

- Concrete implementations before interfaces
- Minimal configuration with sensible defaults
- Features driven by user needs, not anticipated requirements
- Refactor when patterns emerge, not in anticipation

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>[optional scope]: <description>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `perf`

Examples:

```
feat(api): add health check endpoint
fix(integrations): handle ClickHouse connection timeout
refactor!: rename conversation_id to session_id
```

**Do not** include `Co-Authored-By:` footers.

## CI Requirements

All PRs must pass:

1. `ruff check .` - zero violations
2. `ruff format --check .` - zero differences
3. `ty check` - zero errors
4. `pytest` - all tests pass
5. `docker build .` - image builds successfully

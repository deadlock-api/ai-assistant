# Deadlock AI Assistant - FastAPI Application

import os
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from packages.ai_assistant.tracing import configure_tracing, flush_traces
from packages.api.auth import APIKeyAuthMiddleware
from packages.api.auth_patreon import router as patreon_auth_router
from packages.api.chat import router as chat_router
from packages.api.chat import shutdown_active_clients
from packages.api.errors import RequestIDMiddleware, register_exception_handlers
from packages.api.health import router as health_router
from packages.api.logging import RequestLoggingMiddleware, configure_logging
from packages.api.rate_limit import RateLimitMiddleware


def _get_cors_origins() -> list[str]:
    """Get allowed CORS origins based on environment."""
    if os.environ.get("DEV_MODE", "false").lower() == "true":
        return [
            "https://deadlock-api.com",
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
    return ["https://deadlock-api.com"]


# Initialize logging at module load
configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan handler for startup and shutdown events."""
    # Startup: Configure Langfuse tracing
    configure_tracing()
    yield
    # Shutdown: Disconnect any active agent clients (leaked subprocesses)
    await shutdown_active_clients()
    # Shutdown: Flush pending traces
    flush_traces()


app = FastAPI(
    title="Deadlock AI Assistant",
    description="Stateless AI assistant with REST API",
    version="1.0.0",
    lifespan=lifespan,
)

# Register exception handlers for structured error responses
register_exception_handlers(app)

# Middleware order matters (LIFO - last added runs first):
# 1. CORSMiddleware (added last) - handles preflight OPTIONS requests (must run before auth)
# 2. RequestIDMiddleware - generates request ID (runs early for context)
# 3. RequestLoggingMiddleware - logs requests (needs request ID from context)
# 4. RateLimitMiddleware - enforces rate limits (runs before auth, after logging)
# 5. APIKeyAuthMiddleware (added first) - validates authentication (runs last, after rate limit)
app.add_middleware(cast(Any, APIKeyAuthMiddleware))
app.add_middleware(cast(Any, RateLimitMiddleware))
app.add_middleware(cast(Any, RequestLoggingMiddleware))
app.add_middleware(cast(Any, RequestIDMiddleware))
app.add_middleware(
    cast(Any, CORSMiddleware),
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(chat_router)
app.include_router(patreon_auth_router)

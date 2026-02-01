# Deadlock AI Assistant - FastAPI Application

from typing import Any, cast

from fastapi import FastAPI

from packages.api.auth import APIKeyAuthMiddleware
from packages.api.chat import router as chat_router
from packages.api.errors import RequestIDMiddleware, register_exception_handlers
from packages.api.health import router as health_router
from packages.api.logging import RequestLoggingMiddleware, configure_logging

# Initialize logging at module load
configure_logging()

app = FastAPI(
    title="Deadlock AI Assistant",
    description="Stateless AI assistant with REST API",
    version="1.0.0",
)

# Register exception handlers for structured error responses
register_exception_handlers(app)

# Middleware order matters (LIFO - last added runs first):
# 1. RequestIDMiddleware - generates request ID (runs first)
# 2. RequestLoggingMiddleware - logs after response (needs request ID from context)
# 3. APIKeyAuthMiddleware - validates authentication
app.add_middleware(cast(Any, APIKeyAuthMiddleware))
app.add_middleware(cast(Any, RequestLoggingMiddleware))
app.add_middleware(cast(Any, RequestIDMiddleware))

app.include_router(health_router)
app.include_router(chat_router)

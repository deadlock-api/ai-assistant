# Deadlock AI Assistant - Structured Error Handling
#
# Provides consistent error responses with request IDs and error codes.

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from enum import StrEnum

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from packages.ai_assistant.agent import (
    AgentAuthError,
    AgentConfigurationError,
    AgentError,
    AgentRateLimitError,
    AgentRetryExhaustedError,
    AgentTimeoutError,
)
from packages.integrations.redis_client import RedisUnavailableError

logger = logging.getLogger("deadlock_assistant")

# Context variable to store request ID for the current request
request_id_context: ContextVar[str] = ContextVar("request_id", default="")


class ErrorCode(StrEnum):
    """Standard error codes for API responses."""

    AUTH_FAILED = "AUTH_FAILED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AGENT_ERROR = "AGENT_ERROR"
    REDIS_ERROR = "REDIS_ERROR"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorResponse(BaseModel):
    """Structured error response model.

    All API errors return this format for consistent client handling.
    """

    error: str
    code: str
    request_id: str


def get_request_id() -> str:
    """Get the current request ID from context.

    Returns:
        The request ID for the current request, or empty string if not set.
    """
    return request_id_context.get()


def generate_request_id() -> str:
    """Generate a new unique request ID.

    Returns:
        A UUID4 string.
    """
    return str(uuid.uuid4())


def create_error_response(
    error: str,
    code: ErrorCode | str,
    status_code: int,
    request_id: str | None = None,
) -> JSONResponse:
    """Create a structured JSON error response.

    Args:
        error: Human-readable error message.
        code: Error code for programmatic handling.
        status_code: HTTP status code.
        request_id: Optional request ID. Uses context if not provided.

    Returns:
        JSONResponse with structured error body.
    """
    if request_id is None:
        request_id = get_request_id()

    response_body = ErrorResponse(
        error=error,
        code=str(code),
        request_id=request_id,
    )

    return JSONResponse(
        content=response_body.model_dump(),
        status_code=status_code,
    )


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to generate and track request IDs.

    Sets a unique request ID in the context for each request, making it
    available for error responses and logging.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Generate unique request ID
        request_id = generate_request_id()

        # Store in context for access throughout request handling
        token = request_id_context.set(request_id)

        try:
            response = await call_next(request)
            # Add request ID to response headers for debugging
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            # Reset context
            request_id_context.reset(token)


async def validation_exception_handler(
    request: Request,  # noqa: ARG001
    exc: RequestValidationError,
) -> JSONResponse:
    """Handle Pydantic validation errors.

    Args:
        request: The FastAPI request (unused but required by signature).
        exc: The validation exception.

    Returns:
        Structured error response with validation details.
    """
    logger.warning("Validation error", extra={"request_id": get_request_id(), "errors": str(exc.errors())})

    # Extract validation error details without exposing internal structure
    error_messages = []
    for error in exc.errors():
        location = ".".join(str(loc) for loc in error.get("loc", []))
        message = error.get("msg", "Invalid value")
        if location:
            error_messages.append(f"{location}: {message}")
        else:
            error_messages.append(message)

    error_text = "; ".join(error_messages) if error_messages else "Invalid request data"

    return create_error_response(
        error=error_text,
        code=ErrorCode.VALIDATION_ERROR,
        status_code=status.HTTP_400_BAD_REQUEST,
    )


async def agent_error_handler(
    request: Request,  # noqa: ARG001
    exc: AgentError,
) -> JSONResponse:
    """Handle agent-related errors.

    Args:
        request: The FastAPI request (unused but required by signature).
        exc: The agent exception.

    Returns:
        Structured error response with appropriate status code.
    """
    logger.error(
        "Agent error: %s",
        exc,
        extra={"request_id": get_request_id(), "error_type": type(exc).__name__},
    )

    # Determine status code based on error type
    if isinstance(exc, AgentAuthError):
        return create_error_response(
            error="Agent authentication failed",
            code=ErrorCode.AGENT_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    elif isinstance(exc, AgentRateLimitError):
        return create_error_response(
            error="Service temporarily unavailable due to rate limiting",
            code=ErrorCode.AGENT_ERROR,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    elif isinstance(exc, AgentTimeoutError):
        return create_error_response(
            error="Request timed out",
            code=ErrorCode.AGENT_ERROR,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    elif isinstance(exc, AgentRetryExhaustedError):
        return create_error_response(
            error="Service temporarily unavailable",
            code=ErrorCode.AGENT_ERROR,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    elif isinstance(exc, AgentConfigurationError):
        return create_error_response(
            error="Service configuration error",
            code=ErrorCode.AGENT_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    else:
        return create_error_response(
            error="An error occurred while processing your request",
            code=ErrorCode.AGENT_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


async def redis_error_handler(
    request: Request,  # noqa: ARG001
    exc: RedisUnavailableError,
) -> JSONResponse:
    """Handle Redis unavailability errors.

    Args:
        request: The FastAPI request (unused but required by signature).
        exc: The Redis exception.

    Returns:
        Structured error response indicating service unavailability.
    """
    logger.error("Redis unavailable: %s", exc, extra={"request_id": get_request_id()})

    return create_error_response(
        error="Storage service temporarily unavailable",
        code=ErrorCode.REDIS_ERROR,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


async def generic_exception_handler(
    request: Request,  # noqa: ARG001
    exc: Exception,
) -> JSONResponse:
    """Handle all unhandled exceptions.

    Internal error details are never exposed to the client.

    Args:
        request: The FastAPI request (unused but required by signature).
        exc: The exception.

    Returns:
        Generic error response with 500 status code.
    """
    logger.exception("Unhandled exception", extra={"request_id": get_request_id(), "error_type": type(exc).__name__})

    return create_error_response(
        error="An internal error occurred",
        code=ErrorCode.INTERNAL_ERROR,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app.

    Args:
        app: The FastAPI application instance.
    """
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(AgentError, agent_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RedisUnavailableError, redis_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, generic_exception_handler)

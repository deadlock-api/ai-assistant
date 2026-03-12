# Deadlock AI Assistant - Request Logging
#
# Provides structured JSON logging for request tracking and monitoring.

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from packages.api.errors import get_request_id

# Default log level
DEFAULT_LOG_LEVEL = "INFO"

# Headers that should never be logged (case-insensitive)
SENSITIVE_HEADERS = frozenset(
    {
        "x-api-key",
        "authorization",
        "cf-turnstile-response",
        "cookie",
        "set-cookie",
    }
)


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs logs as JSON lines.

    Automatically includes any extra fields passed via logger calls,
    enabling structured logging without hardcoding field names.
    """

    # Fields that are part of the standard LogRecord and should not be included as extras
    _STANDARD_FIELDS = frozenset(
        {
            "name",
            "msg",
            "args",
            "created",
            "relativeCreated",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "pathname",
            "filename",
            "module",
            "levelno",
            "levelname",
            "msecs",
            "process",
            "processName",
            "thread",
            "threadName",
            "taskName",
            "message",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.

        All extra fields passed via ``logger.info("msg", extra={...})``
        are automatically included in the JSON output.

        Args:
            record: The log record to format.

        Returns:
            JSON-formatted string.
        """
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include all extra fields automatically
        for key, value in record.__dict__.items():
            if key not in self._STANDARD_FIELDS and key not in log_entry:
                log_entry[key] = value

        # Add exception info if present
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def configure_logging() -> logging.Logger:
    """Configure the application logger with JSON formatting.

    Reads LOG_LEVEL from environment variable (default: INFO).
    Valid levels: DEBUG, INFO, WARNING, ERROR, CRITICAL.

    Returns:
        Configured logger instance.
    """
    # Get log level from environment
    log_level_str = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()

    # Validate and convert log level
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if log_level_str not in valid_levels:
        log_level_str = DEFAULT_LOG_LEVEL

    log_level = getattr(logging, log_level_str)

    # Get or create logger
    logger = logging.getLogger("deadlock_assistant")

    # Only configure if not already configured
    if not logger.handlers:
        logger.setLevel(log_level)

        # Create console handler with JSON formatter
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(log_level)
        handler.setFormatter(JSONFormatter())

        logger.addHandler(handler)

        # Prevent propagation to root logger
        logger.propagate = False

    return logger


def get_logger() -> logging.Logger:
    """Get the application logger.

    Returns:
        The configured logger instance.
    """
    return logging.getLogger("deadlock_assistant")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all incoming requests with structured JSON output.

    Logs include:
    - request_id: Unique identifier for the request
    - method: HTTP method (GET, POST, etc.)
    - path: Request path
    - status_code: Response status code
    - duration_ms: Request duration in milliseconds
    - timestamp: ISO format timestamp

    Sensitive data (API keys, auth headers, message content) is never logged.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request and log details.

        Args:
            request: The incoming request.
            call_next: The next middleware/handler in the chain.

        Returns:
            The response from the handler.
        """
        logger = get_logger()
        start_time = time.perf_counter()

        # Process the request
        response = await call_next(request)

        # Calculate duration
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        # Get request ID from context (set by RequestIDMiddleware)
        request_id = get_request_id()

        # Log the request (sensitive data excluded by not logging body/headers)
        logger.info(
            "Request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        return response

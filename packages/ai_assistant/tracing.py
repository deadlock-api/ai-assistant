"""Langfuse tracing integration for Claude Agent SDK.

Provides distributed tracing and observability for LLM interactions using
Langfuse's OpenTelemetry instrumentation.

Configuration via environment variables:
    LANGFUSE_PUBLIC_KEY: Langfuse public key (from cloud.langfuse.com)
    LANGFUSE_SECRET_KEY: Langfuse secret key
    LANGFUSE_HOST: Langfuse host URL (default: https://cloud.langfuse.com)
    LANGFUSE_ENABLED: Set to "true" to enable tracing (default: "false")

Example:
    # Enable tracing at application startup
    from packages.ai_assistant.tracing import configure_tracing

    configure_tracing()
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_tracing_configured: bool = False


def is_tracing_enabled() -> bool:
    """Check if Langfuse tracing is enabled via environment variables.

    Returns:
        True if LANGFUSE_ENABLED is "true" and required credentials are set.
    """
    enabled = os.environ.get("LANGFUSE_ENABLED", "false").lower() == "true"
    has_public_key = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))
    has_secret_key = bool(os.environ.get("LANGFUSE_SECRET_KEY"))
    return enabled and has_public_key and has_secret_key


def configure_tracing() -> bool:
    """Configure Langfuse tracing for Claude Agent SDK.

    This function should be called once at application startup. It:
    1. Validates that required environment variables are set
    2. Enables OpenTelemetry tracing via langsmith
    3. Configures the Claude Agent SDK instrumentation

    Returns:
        True if tracing was configured successfully, False otherwise.

    Environment variables:
        LANGFUSE_PUBLIC_KEY: Required - Langfuse public key
        LANGFUSE_SECRET_KEY: Required - Langfuse secret key
        LANGFUSE_HOST: Optional - Langfuse host (default: https://cloud.langfuse.com)
        LANGFUSE_ENABLED: Required - Set to "true" to enable tracing
    """
    global _tracing_configured

    if _tracing_configured:
        logger.debug("Tracing already configured, skipping")
        return True

    if not is_tracing_enabled():
        logger.debug("Langfuse tracing is disabled (LANGFUSE_ENABLED != 'true' or missing credentials)")
        return False

    try:
        # Set environment variables required by langsmith for OpenTelemetry
        os.environ.setdefault("LANGSMITH_OTEL_ENABLED", "true")
        os.environ.setdefault("LANGSMITH_OTEL_ONLY", "true")
        os.environ.setdefault("LANGSMITH_TRACING", "true")

        # Apply pydantic v1 patch for Python 3.14+ compatibility (must be before langfuse import)
        import packages.ai_assistant.patch_pydantic  # noqa: F401, I001
        from langfuse import get_client  # noqa: I001

        langfuse = get_client()

        if not langfuse.auth_check():
            logger.warning("Langfuse authentication check failed - tracing will not be enabled")
            return False

        # Configure Claude Agent SDK instrumentation
        from langsmith.integrations.claude_agent_sdk import configure_claude_agent_sdk

        configure_claude_agent_sdk()

        _tracing_configured = True
        logger.info("Langfuse tracing configured successfully")
        return True

    except ImportError as e:
        logger.warning(f"Failed to import tracing dependencies: {e}")
        return False
    except Exception as e:
        logger.warning(f"Failed to configure Langfuse tracing: {e}")
        return False


def flush_traces() -> None:
    """Flush any pending traces to Langfuse.

    Call this before application shutdown to ensure all traces are sent.
    """
    if not _tracing_configured:
        return

    try:
        from langfuse import get_client

        langfuse = get_client()
        langfuse.flush()
        logger.debug("Flushed traces to Langfuse")
    except Exception as e:
        logger.warning(f"Failed to flush traces: {e}")

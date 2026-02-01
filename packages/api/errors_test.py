# Tests for structured error handling

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from packages.ai_assistant.agent import (
    AgentAuthError,
    AgentConfigurationError,
    AgentError,
    AgentRateLimitError,
    AgentRetryExhaustedError,
    AgentTimeoutError,
)
from packages.api.errors import (
    ErrorCode,
    ErrorResponse,
    RequestIDMiddleware,
    agent_error_handler,
    create_error_response,
    generate_request_id,
    generic_exception_handler,
    get_request_id,
    redis_error_handler,
    register_exception_handlers,
    request_id_context,
    validation_exception_handler,
)
from packages.integrations.redis_client import RedisUnavailableError


def parse_json_response(response: JSONResponse) -> dict[str, Any]:
    """Parse JSON response body for testing."""
    return json.loads(bytes(response.body))


class TestErrorCode:
    """Tests for ErrorCode enum."""

    def test_auth_failed_code(self) -> None:
        """AUTH_FAILED code has correct value."""
        assert ErrorCode.AUTH_FAILED == "AUTH_FAILED"

    def test_validation_error_code(self) -> None:
        """VALIDATION_ERROR code has correct value."""
        assert ErrorCode.VALIDATION_ERROR == "VALIDATION_ERROR"

    def test_agent_error_code(self) -> None:
        """AGENT_ERROR code has correct value."""
        assert ErrorCode.AGENT_ERROR == "AGENT_ERROR"

    def test_redis_error_code(self) -> None:
        """REDIS_ERROR code has correct value."""
        assert ErrorCode.REDIS_ERROR == "REDIS_ERROR"

    def test_internal_error_code(self) -> None:
        """INTERNAL_ERROR code has correct value."""
        assert ErrorCode.INTERNAL_ERROR == "INTERNAL_ERROR"


class TestErrorResponse:
    """Tests for ErrorResponse model."""

    def test_error_response_fields(self) -> None:
        """ErrorResponse contains all required fields."""
        response = ErrorResponse(
            error="Test error",
            code="TEST_CODE",
            request_id="abc-123",
        )
        assert response.error == "Test error"
        assert response.code == "TEST_CODE"
        assert response.request_id == "abc-123"

    def test_error_response_serialization(self) -> None:
        """ErrorResponse serializes to expected JSON structure."""
        response = ErrorResponse(
            error="Something went wrong",
            code=ErrorCode.INTERNAL_ERROR,
            request_id="req-456",
        )
        data = response.model_dump()
        assert data == {
            "error": "Something went wrong",
            "code": "INTERNAL_ERROR",
            "request_id": "req-456",
        }


class TestRequestIdGeneration:
    """Tests for request ID generation and context."""

    def test_generate_request_id_returns_uuid(self) -> None:
        """generate_request_id returns a valid UUID string."""
        request_id = generate_request_id()
        # Verify it's a valid UUID by parsing it
        parsed = uuid.UUID(request_id)
        assert str(parsed) == request_id

    def test_generate_request_id_unique(self) -> None:
        """generate_request_id returns unique values."""
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100

    def test_get_request_id_returns_context_value(self) -> None:
        """get_request_id returns the value from context."""
        token = request_id_context.set("test-id-123")
        try:
            assert get_request_id() == "test-id-123"
        finally:
            request_id_context.reset(token)

    def test_get_request_id_returns_empty_when_not_set(self) -> None:
        """get_request_id returns empty string when not in request context."""
        # Context should be empty at test start
        # Note: this might fail if another test left context dirty
        assert get_request_id() == ""


class TestCreateErrorResponse:
    """Tests for create_error_response function."""

    def test_creates_json_response(self) -> None:
        """create_error_response returns JSONResponse."""
        token = request_id_context.set("req-789")
        try:
            response = create_error_response(
                error="Test error",
                code=ErrorCode.VALIDATION_ERROR,
                status_code=400,
            )
            assert response.status_code == 400
            assert response.media_type == "application/json"
        finally:
            request_id_context.reset(token)

    def test_uses_request_id_from_context(self) -> None:
        """create_error_response uses request ID from context."""
        token = request_id_context.set("context-id")
        try:
            response = create_error_response(
                error="Error",
                code=ErrorCode.INTERNAL_ERROR,
                status_code=500,
            )
            body = parse_json_response(response)
            assert body["request_id"] == "context-id"
        finally:
            request_id_context.reset(token)

    def test_uses_provided_request_id(self) -> None:
        """create_error_response uses explicitly provided request ID."""
        response = create_error_response(
            error="Error",
            code=ErrorCode.INTERNAL_ERROR,
            status_code=500,
            request_id="explicit-id",
        )
        body = parse_json_response(response)
        assert body["request_id"] == "explicit-id"

    def test_response_body_structure(self) -> None:
        """create_error_response returns correct body structure."""
        response = create_error_response(
            error="Something failed",
            code=ErrorCode.AGENT_ERROR,
            status_code=503,
            request_id="test-req",
        )
        body = parse_json_response(response)
        assert body == {
            "error": "Something failed",
            "code": "AGENT_ERROR",
            "request_id": "test-req",
        }


class TestRequestIDMiddleware:
    """Tests for RequestIDMiddleware."""

    @pytest.fixture
    def app_with_middleware(self) -> FastAPI:
        """Create a test app with RequestIDMiddleware."""
        from typing import Any, cast

        app = FastAPI()
        app.add_middleware(cast(Any, RequestIDMiddleware))

        @app.get("/test")
        async def test_endpoint() -> dict[str, str]:
            return {"request_id": get_request_id()}

        return app

    def test_middleware_sets_request_id_in_context(self, app_with_middleware: FastAPI) -> None:
        """Middleware sets request ID accessible in endpoint."""
        client = TestClient(app_with_middleware)
        response = client.get("/test")
        assert response.status_code == 200
        data = response.json()
        # Verify it's a valid UUID
        uuid.UUID(data["request_id"])

    def test_middleware_adds_request_id_header(self, app_with_middleware: FastAPI) -> None:
        """Middleware adds X-Request-ID header to response."""
        client = TestClient(app_with_middleware)
        response = client.get("/test")
        assert "X-Request-ID" in response.headers
        # Verify it's a valid UUID
        uuid.UUID(response.headers["X-Request-ID"])

    def test_request_id_matches_header(self, app_with_middleware: FastAPI) -> None:
        """Request ID in context matches response header."""
        client = TestClient(app_with_middleware)
        response = client.get("/test")
        body_id = response.json()["request_id"]
        header_id = response.headers["X-Request-ID"]
        assert body_id == header_id


class TestValidationExceptionHandler:
    """Tests for validation_exception_handler."""

    @pytest.fixture
    def mock_request(self) -> MagicMock:
        """Create a mock request."""
        return MagicMock()

    @pytest.mark.anyio
    async def test_handles_validation_error(self, mock_request: MagicMock) -> None:
        """Handler returns structured error for validation failures."""

        # Create a real validation error
        class TestModel(BaseModel):
            name: str
            age: int

        try:
            TestModel(name=123, age="invalid")  # type: ignore[arg-type]
        except ValidationError as e:
            exc = RequestValidationError(e.errors())

        token = request_id_context.set("val-req")
        try:
            response = await validation_exception_handler(mock_request, exc)
            assert response.status_code == 400
            body = parse_json_response(response)
            assert body["code"] == "VALIDATION_ERROR"
            assert body["request_id"] == "val-req"
            assert "error" in body
        finally:
            request_id_context.reset(token)


class TestAgentErrorHandler:
    """Tests for agent_error_handler."""

    @pytest.fixture
    def mock_request(self) -> MagicMock:
        """Create a mock request."""
        return MagicMock()

    @pytest.mark.anyio
    async def test_handles_agent_auth_error(self, mock_request: MagicMock) -> None:
        """Handler returns 500 for auth errors."""
        exc = AgentAuthError("Auth failed")
        token = request_id_context.set("auth-req")
        try:
            response = await agent_error_handler(mock_request, exc)
            assert response.status_code == 500
            body = parse_json_response(response)
            assert body["code"] == "AGENT_ERROR"
            assert "authentication" in body["error"].lower()
        finally:
            request_id_context.reset(token)

    @pytest.mark.anyio
    async def test_handles_agent_rate_limit_error(self, mock_request: MagicMock) -> None:
        """Handler returns 503 for rate limit errors."""
        exc = AgentRateLimitError("Rate limited")
        token = request_id_context.set("rate-req")
        try:
            response = await agent_error_handler(mock_request, exc)
            assert response.status_code == 503
            body = parse_json_response(response)
            assert body["code"] == "AGENT_ERROR"
            assert "rate limit" in body["error"].lower()
        finally:
            request_id_context.reset(token)

    @pytest.mark.anyio
    async def test_handles_agent_timeout_error(self, mock_request: MagicMock) -> None:
        """Handler returns 503 for timeout errors."""
        exc = AgentTimeoutError("Timed out")
        token = request_id_context.set("timeout-req")
        try:
            response = await agent_error_handler(mock_request, exc)
            assert response.status_code == 503
            body = parse_json_response(response)
            assert body["code"] == "AGENT_ERROR"
            assert "timed out" in body["error"].lower()
        finally:
            request_id_context.reset(token)

    @pytest.mark.anyio
    async def test_handles_agent_retry_exhausted_error(self, mock_request: MagicMock) -> None:
        """Handler returns 503 for retry exhausted errors."""
        exc = AgentRetryExhaustedError("Retries exhausted")
        token = request_id_context.set("retry-req")
        try:
            response = await agent_error_handler(mock_request, exc)
            assert response.status_code == 503
            body = parse_json_response(response)
            assert body["code"] == "AGENT_ERROR"
            assert "unavailable" in body["error"].lower()
        finally:
            request_id_context.reset(token)

    @pytest.mark.anyio
    async def test_handles_agent_configuration_error(self, mock_request: MagicMock) -> None:
        """Handler returns 500 for configuration errors."""
        exc = AgentConfigurationError("Config missing")
        token = request_id_context.set("config-req")
        try:
            response = await agent_error_handler(mock_request, exc)
            assert response.status_code == 500
            body = parse_json_response(response)
            assert body["code"] == "AGENT_ERROR"
            assert "configuration" in body["error"].lower()
        finally:
            request_id_context.reset(token)

    @pytest.mark.anyio
    async def test_handles_generic_agent_error(self, mock_request: MagicMock) -> None:
        """Handler returns 500 for generic agent errors."""
        exc = AgentError("Something went wrong")
        token = request_id_context.set("generic-req")
        try:
            response = await agent_error_handler(mock_request, exc)
            assert response.status_code == 500
            body = parse_json_response(response)
            assert body["code"] == "AGENT_ERROR"
        finally:
            request_id_context.reset(token)


class TestRedisErrorHandler:
    """Tests for redis_error_handler."""

    @pytest.fixture
    def mock_request(self) -> MagicMock:
        """Create a mock request."""
        return MagicMock()

    @pytest.mark.anyio
    async def test_handles_redis_unavailable_error(self, mock_request: MagicMock) -> None:
        """Handler returns 503 for Redis errors."""
        exc = RedisUnavailableError("Redis down")
        token = request_id_context.set("redis-req")
        try:
            response = await redis_error_handler(mock_request, exc)
            assert response.status_code == 503
            body = parse_json_response(response)
            assert body["code"] == "REDIS_ERROR"
            assert body["request_id"] == "redis-req"
            assert "storage" in body["error"].lower()
        finally:
            request_id_context.reset(token)


class TestGenericExceptionHandler:
    """Tests for generic_exception_handler."""

    @pytest.fixture
    def mock_request(self) -> MagicMock:
        """Create a mock request."""
        return MagicMock()

    @pytest.mark.anyio
    async def test_handles_unhandled_exception(self, mock_request: MagicMock) -> None:
        """Handler returns 500 for unhandled exceptions."""
        exc = RuntimeError("Unexpected error with sensitive details")
        token = request_id_context.set("unhandled-req")
        try:
            response = await generic_exception_handler(mock_request, exc)
            assert response.status_code == 500
            body = parse_json_response(response)
            assert body["code"] == "INTERNAL_ERROR"
            assert body["request_id"] == "unhandled-req"
            # Verify internal details are NOT exposed
            assert "sensitive" not in body["error"].lower()
            assert "Unexpected" not in body["error"]
        finally:
            request_id_context.reset(token)


class TestRegisterExceptionHandlers:
    """Tests for register_exception_handlers function."""

    def test_registers_all_handlers(self) -> None:
        """Function registers handlers for all expected exception types."""
        app = FastAPI()
        register_exception_handlers(app)

        # Verify handlers are registered (FastAPI stores them in exception_handlers dict)
        # Note: FastAPI may normalize types, so we check for the presence of handlers
        assert len(app.exception_handlers) >= 4


class TestIntegration:
    """Integration tests for error handling with full app."""

    @pytest.fixture
    def test_app(self) -> FastAPI:
        """Create a test app with error handling configured."""
        from typing import Any, cast

        app = FastAPI()
        app.add_middleware(cast(Any, RequestIDMiddleware))
        register_exception_handlers(app)

        @app.get("/trigger-validation")
        async def trigger_validation(value: int) -> dict[str, int]:
            return {"value": value}

        @app.get("/trigger-agent-error")
        async def trigger_agent_error() -> None:
            raise AgentError("Agent failed")

        @app.get("/trigger-redis-error")
        async def trigger_redis_error() -> None:
            raise RedisUnavailableError("Redis unavailable")

        @app.get("/trigger-internal-error")
        async def trigger_internal_error() -> None:
            raise RuntimeError("Internal failure")

        return app

    def test_validation_error_response_format(self, test_app: FastAPI) -> None:
        """Validation errors return structured response."""
        client = TestClient(test_app, raise_server_exceptions=False)
        response = client.get("/trigger-validation?value=not-an-int")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert data["code"] == "VALIDATION_ERROR"
        assert "request_id" in data
        # Verify request_id is valid UUID
        uuid.UUID(data["request_id"])

    def test_agent_error_response_format(self, test_app: FastAPI) -> None:
        """Agent errors return structured response."""
        client = TestClient(test_app, raise_server_exceptions=False)
        response = client.get("/trigger-agent-error")
        assert response.status_code == 500
        data = response.json()
        assert "error" in data
        assert data["code"] == "AGENT_ERROR"
        assert "request_id" in data

    def test_redis_error_response_format(self, test_app: FastAPI) -> None:
        """Redis errors return structured response."""
        client = TestClient(test_app, raise_server_exceptions=False)
        response = client.get("/trigger-redis-error")
        assert response.status_code == 503
        data = response.json()
        assert "error" in data
        assert data["code"] == "REDIS_ERROR"
        assert "request_id" in data

    def test_internal_error_response_format(self, test_app: FastAPI) -> None:
        """Internal errors return structured response without details."""
        client = TestClient(test_app, raise_server_exceptions=False)
        response = client.get("/trigger-internal-error")
        assert response.status_code == 500
        data = response.json()
        assert "error" in data
        assert data["code"] == "INTERNAL_ERROR"
        assert "request_id" in data
        # Verify internal details are NOT in response
        assert "Internal failure" not in data["error"]

    def test_request_id_in_success_response_header(self, test_app: FastAPI) -> None:
        """Successful responses include X-Request-ID header."""

        # Add a success endpoint to the test app
        @test_app.get("/success")
        async def success_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(test_app)
        response = client.get("/success")
        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        # Verify it's a valid UUID
        uuid.UUID(response.headers["X-Request-ID"])

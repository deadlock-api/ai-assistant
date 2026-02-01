# Tests for request logging module

from __future__ import annotations

import json
import logging
import os
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.api.errors import RequestIDMiddleware
from packages.api.logging import (
    JSONFormatter,
    RequestLoggingMiddleware,
    configure_logging,
    get_logger,
)


class ListHandler(logging.Handler):
    """Handler that stores log records in a list for testing."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class TestJSONFormatter:
    """Tests for JSONFormatter class."""

    def test_formats_basic_log_record_as_json(self) -> None:
        """Test that basic log records are formatted as valid JSON."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test_logger"
        assert parsed["message"] == "Test message"
        assert "timestamp" in parsed

    def test_includes_extra_request_fields(self) -> None:
        """Test that extra request fields are included in output."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Request completed",
            args=(),
            exc_info=None,
        )
        record.request_id = "test-uuid-123"
        record.method = "GET"
        record.path = "/health"
        record.status_code = 200
        record.duration_ms = 12.5

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["request_id"] == "test-uuid-123"
        assert parsed["method"] == "GET"
        assert parsed["path"] == "/health"
        assert parsed["status_code"] == 200
        assert parsed["duration_ms"] == 12.5

    def test_timestamp_is_iso_format(self) -> None:
        """Test that timestamp is in ISO format."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)
        parsed = json.loads(result)

        # Should be parseable as ISO format (contains T and timezone)
        assert "T" in parsed["timestamp"]


class TestConfigureLogging:
    """Tests for configure_logging function."""

    @pytest.fixture(autouse=True)
    def reset_logger(self) -> Generator[None]:
        """Reset the logger before and after each test."""
        logger = logging.getLogger("deadlock_assistant")
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)
        yield
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)

    def test_creates_logger_with_default_level(self) -> None:
        """Test that logger is created with default INFO level."""
        # Ensure LOG_LEVEL is not set
        with patch.dict(os.environ, {}, clear=False):
            if "LOG_LEVEL" in os.environ:
                del os.environ["LOG_LEVEL"]
            logger = configure_logging()

        assert logger.name == "deadlock_assistant"
        assert logger.level == logging.INFO

    def test_respects_log_level_env_var(self) -> None:
        """Test that LOG_LEVEL environment variable is respected."""
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
            logger = configure_logging()

        assert logger.level == logging.DEBUG

    def test_handles_invalid_log_level(self) -> None:
        """Test that invalid log levels fall back to default."""
        with patch.dict(os.environ, {"LOG_LEVEL": "INVALID"}):
            logger = configure_logging()

        assert logger.level == logging.INFO

    def test_logger_has_json_handler(self) -> None:
        """Test that logger has a handler with JSON formatter."""
        logger = configure_logging()

        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, JSONFormatter)

    def test_does_not_duplicate_handlers(self) -> None:
        """Test that calling configure_logging twice doesn't add duplicate handlers."""
        configure_logging()
        configure_logging()
        logger = get_logger()

        assert len(logger.handlers) == 1

    @pytest.mark.parametrize(
        "level_str,expected_level",
        [
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
            ("CRITICAL", logging.CRITICAL),
            ("debug", logging.DEBUG),  # Test case insensitivity
            ("info", logging.INFO),
        ],
    )
    def test_all_valid_log_levels(self, level_str: str, expected_level: int) -> None:
        """Test that all valid log levels are accepted."""
        with patch.dict(os.environ, {"LOG_LEVEL": level_str}):
            logger = configure_logging()

        assert logger.level == expected_level


class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_named_logger(self) -> None:
        """Test that get_logger returns the correct logger."""
        logger = get_logger()
        assert logger.name == "deadlock_assistant"


class TestRequestLoggingMiddleware:
    """Tests for RequestLoggingMiddleware."""

    @pytest.fixture
    def app(self) -> FastAPI:
        """Create a test FastAPI app with logging middleware."""
        app = FastAPI()

        # Add middlewares in correct order (LIFO)
        app.add_middleware(RequestLoggingMiddleware)  # type: ignore[arg-type]
        app.add_middleware(RequestIDMiddleware)  # type: ignore[arg-type]

        @app.get("/test")
        def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        @app.post("/submit")
        def submit_endpoint() -> dict[str, str]:
            return {"status": "submitted"}

        return app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        """Create test client."""
        return TestClient(app)

    @pytest.fixture
    def log_handler(self) -> Generator[ListHandler]:
        """Add a list handler to capture log records."""
        logger = logging.getLogger("deadlock_assistant")
        # Clear existing handlers and configure logging
        logger.handlers.clear()
        configure_logging()

        # Add our custom handler
        handler = ListHandler()
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)

        yield handler

        # Clean up
        logger.removeHandler(handler)

    def test_logs_request_with_required_fields(self, client: TestClient, log_handler: ListHandler) -> None:
        """Test that requests are logged with all required fields."""
        client.get("/test")

        # Find the request log record
        log_record = None
        for record in log_handler.records:
            if record.getMessage() == "Request completed":
                log_record = record
                break

        assert log_record is not None
        assert hasattr(log_record, "request_id")
        assert hasattr(log_record, "method")
        assert hasattr(log_record, "path")
        assert hasattr(log_record, "status_code")
        assert hasattr(log_record, "duration_ms")

    def test_logs_correct_method(self, client: TestClient, log_handler: ListHandler) -> None:
        """Test that the correct HTTP method is logged."""
        client.post("/submit")

        log_record = None
        for record in log_handler.records:
            if hasattr(record, "method"):
                log_record = record
                break

        assert log_record is not None
        assert log_record.method == "POST"

    def test_logs_correct_path(self, client: TestClient, log_handler: ListHandler) -> None:
        """Test that the correct path is logged."""
        client.get("/test")

        log_record = None
        for record in log_handler.records:
            if hasattr(record, "path"):
                log_record = record
                break

        assert log_record is not None
        assert log_record.path == "/test"

    def test_logs_correct_status_code(self, client: TestClient, log_handler: ListHandler) -> None:
        """Test that the correct status code is logged."""
        client.get("/test")

        log_record = None
        for record in log_handler.records:
            if hasattr(record, "status_code"):
                log_record = record
                break

        assert log_record is not None
        assert log_record.status_code == 200

    def test_logs_duration_in_ms(self, client: TestClient, log_handler: ListHandler) -> None:
        """Test that duration is logged in milliseconds."""
        client.get("/test")

        log_record = None
        for record in log_handler.records:
            if hasattr(record, "duration_ms"):
                log_record = record
                break

        assert log_record is not None
        assert isinstance(log_record.duration_ms, float)
        assert log_record.duration_ms >= 0

    def test_logs_request_id_from_context(self, client: TestClient, log_handler: ListHandler) -> None:
        """Test that request_id is captured from context."""
        response = client.get("/test")
        request_id = response.headers.get("X-Request-ID")

        log_record = None
        for record in log_handler.records:
            if hasattr(record, "request_id"):
                log_record = record
                break

        assert log_record is not None
        assert log_record.request_id == request_id

    def test_does_not_log_api_key_header(self, client: TestClient, log_handler: ListHandler) -> None:
        """Test that API key is never included in logs."""
        # Make request with API key header
        client.get("/test", headers={"X-API-Key": "secret-key-12345"})

        # Check that no log contains the API key
        for record in log_handler.records:
            # Check message and all extra fields
            log_str = str(record.__dict__)
            assert "secret-key-12345" not in log_str

    def test_does_not_log_request_body(self, client: TestClient, log_handler: ListHandler) -> None:
        """Test that request body (message content) is never logged."""
        # Make request with sensitive body content
        client.post(
            "/submit",
            json={"message": "This is a secret message"},
        )

        # Check that no log contains the body content
        for record in log_handler.records:
            log_str = str(record.__dict__)
            assert "This is a secret message" not in log_str


class TestLogOutput:
    """Test that JSON output is properly formatted."""

    @pytest.fixture(autouse=True)
    def reset_logger(self) -> Generator[None]:
        """Reset the logger before and after each test."""
        logger = logging.getLogger("deadlock_assistant")
        logger.handlers.clear()
        yield
        logger.handlers.clear()

    def test_output_is_valid_json_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that log output is valid JSON."""
        configure_logging()
        logger = get_logger()
        logger.info("Test message")

        captured = capsys.readouterr()

        # Should be parseable as JSON
        parsed = json.loads(captured.out.strip())
        assert parsed["message"] == "Test message"
        assert parsed["level"] == "INFO"

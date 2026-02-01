# Deadlock AI Assistant - Chat Endpoint Tests

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from packages.ai_assistant.agent import (
    AgentAuthError,
    AgentConfigurationError,
    AgentRateLimitError,
    AgentRetryExhaustedError,
    AgentTimeoutError,
    StreamChunk,
)
from packages.api.app import app
from packages.integrations.conversation import ConversationMessage, ConversationNotFoundError
from packages.integrations.redis_client import RedisUnavailableError

# Module paths for patching
CHAT_MODULE = "packages.api.chat"
STREAM_RESPONSE_PATH = f"{CHAT_MODULE}.stream_response"
GET_HISTORY_PATH = f"{CHAT_MODULE}.get_conversation_history"
ADD_MESSAGE_PATH = f"{CHAT_MODULE}.add_message"
GENERATE_ID_PATH = f"{CHAT_MODULE}.generate_conversation_id"


@pytest.fixture
def client() -> Generator[TestClient]:
    """Create a test client."""
    yield TestClient(app)


@pytest.fixture
def valid_api_key() -> Generator[None]:
    """Set a valid API key for authentication."""
    with patch.dict("os.environ", {"API_KEYS": "test-key"}):
        yield


def parse_sse_events(response_text: str) -> list[dict[str, Any]]:
    """Parse SSE events from response text."""
    events = []
    for line in response_text.strip().split("\n\n"):
        if line.startswith("data: "):
            data = line[6:]  # Remove "data: " prefix
            events.append(json.loads(data))
    return events


async def mock_stream_response_success(*args: Any, **kwargs: Any) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
    """Mock successful streaming response."""
    yield StreamChunk(content="Hello", is_complete=False)
    yield StreamChunk(content=" world!", is_complete=False)
    yield StreamChunk(content="", is_complete=True)


async def mock_stream_response_empty(*args: Any, **kwargs: Any) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
    """Mock empty streaming response."""
    yield StreamChunk(content="", is_complete=True)


@pytest.mark.usefixtures("valid_api_key")
class TestChatEndpoint:
    """Tests for POST /chat endpoint."""

    def test_chat_new_conversation(self, client: TestClient) -> None:
        """Test chat with no conversation_id creates new conversation."""
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_stream_response_success),
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock) as mock_add,
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")

            events = parse_sse_events(response.text)
            assert len(events) == 4  # start, 2 deltas, end

            assert events[0]["event"] == "start"
            assert events[0]["conversation_id"] == "new-conv-id"

            assert events[1]["event"] == "delta"
            assert events[1]["content"] == "Hello"

            assert events[2]["event"] == "delta"
            assert events[2]["content"] == " world!"

            assert events[3]["event"] == "end"
            assert events[3]["conversation_id"] == "new-conv-id"

            # Verify messages were saved
            assert mock_add.call_count == 2
            mock_add.assert_any_call("new-conv-id", "user", "Hello")
            mock_add.assert_any_call("new-conv-id", "assistant", "Hello world!")

    def test_chat_existing_conversation(self, client: TestClient) -> None:
        """Test chat with existing conversation_id loads history."""
        history = [
            ConversationMessage(role="user", content="Previous message"),
            ConversationMessage(role="assistant", content="Previous response"),
        ]

        with (
            patch(GET_HISTORY_PATH, new_callable=AsyncMock, return_value=history),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_stream_response_success) as mock_stream,
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock),
        ):
            response = client.post(
                "/chat",
                json={"message": "New message", "conversation_id": "existing-id"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[0]["event"] == "start"
            assert events[0]["conversation_id"] == "existing-id"

            # Verify history was passed to stream_response
            call_args = mock_stream.call_args
            assert call_args[0][0] == "New message"
            assert call_args[1]["conversation_history"] == [
                {"role": "user", "content": "Previous message"},
                {"role": "assistant", "content": "Previous response"},
            ]

    def test_chat_conversation_not_found_starts_fresh(self, client: TestClient) -> None:
        """Test chat with non-existent conversation_id starts fresh."""
        with (
            patch(GET_HISTORY_PATH, new_callable=AsyncMock, side_effect=ConversationNotFoundError("Not found")),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_stream_response_success) as mock_stream,
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello", "conversation_id": "unknown-id"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[0]["event"] == "start"
            assert events[0]["conversation_id"] == "unknown-id"

            # Verify empty history was passed
            call_args = mock_stream.call_args
            assert call_args[1]["conversation_history"] == []

    def test_chat_redis_unavailable_on_load(self, client: TestClient) -> None:
        """Test chat returns error when Redis unavailable during history load."""
        with patch(GET_HISTORY_PATH, new_callable=AsyncMock, side_effect=RedisUnavailableError("Connection failed")):
            response = client.post(
                "/chat",
                json={"message": "Hello", "conversation_id": "some-id"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert len(events) == 1
            assert events[0]["event"] == "error"
            assert events[0]["code"] == "REDIS_ERROR"
            assert "load" in events[0]["error"].lower()

    def test_chat_redis_unavailable_on_save(self, client: TestClient) -> None:
        """Test chat returns error when Redis unavailable during save."""
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_stream_response_success),
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock, side_effect=RedisUnavailableError("Connection failed")),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            # Should have start, deltas, then error
            assert events[0]["event"] == "start"
            assert events[-1]["event"] == "error"
            assert events[-1]["code"] == "REDIS_ERROR"
            assert "save" in events[-1]["error"].lower()

    def test_chat_validation_error(self, client: TestClient) -> None:
        """Test chat returns 400 for invalid request body."""
        response = client.post(
            "/chat",
            json={},  # Missing required "message" field
            headers={"X-API-Key": "test-key"},
        )

        # Structured error handling returns 400 for validation errors
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_ERROR"
        assert "request_id" in data

    def test_chat_empty_message(self, client: TestClient) -> None:
        """Test chat with empty message still processes."""
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_stream_response_empty),
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock),
        ):
            response = client.post(
                "/chat",
                json={"message": ""},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[0]["event"] == "start"
            assert events[-1]["event"] == "end"

    def test_chat_requires_authentication(self, client: TestClient) -> None:
        """Test chat requires authentication."""
        response = client.post(
            "/chat",
            json={"message": "Hello"},
        )

        assert response.status_code == 401


@pytest.mark.usefixtures("valid_api_key")
class TestChatAgentErrors:
    """Tests for agent error handling in chat endpoint."""

    def test_chat_agent_configuration_error(self, client: TestClient) -> None:
        """Test chat handles agent configuration errors."""

        async def mock_error(*args: Any, **kwargs: Any) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentConfigurationError("ANTHROPIC_API_KEY not set")
            yield  # Make this an async generator

        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_error),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[0]["event"] == "start"
            assert events[1]["event"] == "error"
            assert events[1]["code"] == "AGENT_CONFIGURATION_ERROR"

    def test_chat_agent_timeout_error(self, client: TestClient) -> None:
        """Test chat handles agent timeout errors."""

        async def mock_error(*args: Any, **kwargs: Any) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentTimeoutError("Request timed out")
            yield

        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_error),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            events = parse_sse_events(response.text)
            assert events[1]["event"] == "error"
            assert events[1]["code"] == "AGENT_TIMEOUT"

    def test_chat_agent_auth_error(self, client: TestClient) -> None:
        """Test chat handles agent auth errors."""

        async def mock_error(*args: Any, **kwargs: Any) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentAuthError("Invalid API key")
            yield

        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_error),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            events = parse_sse_events(response.text)
            assert events[1]["event"] == "error"
            assert events[1]["code"] == "AGENT_AUTH_ERROR"

    def test_chat_agent_rate_limit_error(self, client: TestClient) -> None:
        """Test chat handles agent rate limit errors."""

        async def mock_error(*args: Any, **kwargs: Any) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentRateLimitError("Rate limit exceeded")
            yield

        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_error),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            events = parse_sse_events(response.text)
            assert events[1]["event"] == "error"
            assert events[1]["code"] == "AGENT_RATE_LIMIT"

    def test_chat_agent_retry_exhausted_error(self, client: TestClient) -> None:
        """Test chat handles agent retry exhausted errors."""

        async def mock_error(*args: Any, **kwargs: Any) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentRetryExhaustedError("All retries failed")
            yield

        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_error),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            events = parse_sse_events(response.text)
            assert events[1]["event"] == "error"
            assert events[1]["code"] == "AGENT_RETRY_EXHAUSTED"


@pytest.mark.usefixtures("valid_api_key")
class TestChatRequestValidation:
    """Tests for chat request validation."""

    def test_chat_accepts_valid_request(self, client: TestClient) -> None:
        """Test chat accepts valid request with all fields."""
        with (
            patch(GET_HISTORY_PATH, new_callable=AsyncMock, return_value=[]),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_stream_response_success),
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello", "conversation_id": "test-id"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

    def test_chat_accepts_null_conversation_id(self, client: TestClient) -> None:
        """Test chat accepts null conversation_id."""
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(STREAM_RESPONSE_PATH, side_effect=mock_stream_response_success),
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello", "conversation_id": None},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

    def test_chat_rejects_missing_message(self, client: TestClient) -> None:
        """Test chat rejects request without message field."""
        response = client.post(
            "/chat",
            json={"conversation_id": "test-id"},
            headers={"X-API-Key": "test-key"},
        )

        # Structured error handling returns 400 for validation errors
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_ERROR"

    def test_chat_rejects_wrong_message_type(self, client: TestClient) -> None:
        """Test chat rejects non-string message."""
        response = client.post(
            "/chat",
            json={"message": 123},
            headers={"X-API-Key": "test-key"},
        )

        # Structured error handling returns 400 for validation errors
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_ERROR"

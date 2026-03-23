# Deadlock AI Assistant - Chat Endpoint Tests

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
DEADLOCK_CLIENT_PATH = f"{CHAT_MODULE}.DeadlockAgentClient"
GET_AGENT_CONFIG_PATH = f"{CHAT_MODULE}.get_agent_config"
BUILD_PROMPT_PATH = f"{CHAT_MODULE}._build_prompt_with_history"
GET_HISTORY_PATH = f"{CHAT_MODULE}.get_conversation_history"
ADD_MESSAGE_PATH = f"{CHAT_MODULE}.add_message"
GENERATE_ID_PATH = f"{CHAT_MODULE}.generate_conversation_id"
GET_CACHED_PATH = f"{CHAT_MODULE}.get_cached_sse_stream"
CACHE_STREAM_PATH = f"{CHAT_MODULE}.cache_sse_stream"
GENERATE_CACHE_KEY_PATH = f"{CHAT_MODULE}.generate_cache_key"


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


def _make_mock_client(send_message_side_effect: Any) -> MagicMock:
    """Create a mock DeadlockAgentClient with the given send_message behavior.

    Args:
        send_message_side_effect: A callable (async generator factory) or list of them
            that will be used as side_effect for send_message.

    Returns:
        A MagicMock that behaves like DeadlockAgentClient.
    """
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.send_message = MagicMock(side_effect=send_message_side_effect)
    return mock_client


async def _chunks_success(message: str) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
    """Yield a successful response."""
    yield StreamChunk(content="Hello", is_complete=False)
    yield StreamChunk(content=" world!", is_complete=False)
    yield StreamChunk(content="", is_complete=True)


async def _chunks_empty(message: str) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
    """Yield an empty response."""
    yield StreamChunk(content="", is_complete=True)


def _make_chunks_empty_then_success(fail_count: int = 1) -> Any:
    """Create a side_effect that returns empty `fail_count` times, then succeeds."""
    call_count = 0

    async def _side_effect(message: str) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        if call_count <= fail_count:
            yield StreamChunk(content="", is_complete=True)
        else:
            yield StreamChunk(content="Recovered!", is_complete=False)
            yield StreamChunk(content="", is_complete=True)

    return _side_effect


def _patch_client(send_message_side_effect: Any) -> Any:
    """Patch DeadlockAgentClient to return a mock with given send_message behavior."""
    mock_client = _make_mock_client(send_message_side_effect)

    def _constructor(**kwargs: Any) -> MagicMock:  # noqa: ARG001
        return mock_client

    return patch(DEADLOCK_CLIENT_PATH, side_effect=_constructor), mock_client


@pytest.mark.usefixtures("valid_api_key")
class TestChatEndpoint:
    """Tests for POST /chat endpoint."""

    def test_chat_new_conversation(self, client: TestClient) -> None:
        """Test chat with no conversation_id creates new conversation."""
        client_patch, mock_agent = _patch_client(_chunks_success)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
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

            # Verify client was connected and disconnected
            mock_agent.connect.assert_called_once()
            mock_agent.disconnect.assert_called_once()

    def test_chat_existing_conversation(self, client: TestClient) -> None:
        """Test chat with existing conversation_id loads history."""
        history = [
            ConversationMessage(role="user", content="Previous message"),
            ConversationMessage(role="assistant", content="Previous response"),
        ]

        client_patch, mock_agent = _patch_client(_chunks_success)
        with (
            patch(GET_HISTORY_PATH, new_callable=AsyncMock, return_value=history),
            client_patch,
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

            # Verify send_message was called (first call is the user message with history)
            mock_agent.send_message.assert_called_once()

    def test_chat_conversation_not_found_starts_fresh(self, client: TestClient) -> None:
        """Test chat with non-existent conversation_id starts fresh."""
        client_patch, _ = _patch_client(_chunks_success)
        with (
            patch(GET_HISTORY_PATH, new_callable=AsyncMock, side_effect=ConversationNotFoundError("Not found")),
            client_patch,
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
        client_patch, _ = _patch_client(_chunks_success)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
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
        """Test chat with empty message returns error when agent produces no content."""
        client_patch, _ = _patch_client(_chunks_empty)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock) as mock_add,
        ):
            response = client.post(
                "/chat",
                json={"message": ""},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[0]["event"] == "start"
            assert events[-1]["event"] == "error"
            assert events[-1]["code"] == "EMPTY_RESPONSE"

            # Verify no messages were saved to history
            mock_add.assert_not_called()

    def test_chat_empty_agent_response(self, client: TestClient) -> None:
        """Test that agent returning empty content sends error instead of silent end."""
        client_patch, _ = _patch_client(_chunks_empty)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock) as mock_add,
        ):
            response = client.post(
                "/chat",
                json={"message": "Are there hero stats for weak lane?"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[0]["event"] == "start"
            assert events[-1]["event"] == "error"
            assert events[-1]["code"] == "EMPTY_RESPONSE"
            assert "unable to generate a response" in events[-1]["error"]

            # Verify no messages were saved to history
            mock_add.assert_not_called()

    def test_chat_empty_response_nudge_succeeds(self, client: TestClient) -> None:
        """Test that empty response triggers nudge within same session and succeeds."""
        client_patch, mock_agent = _patch_client(_make_chunks_empty_then_success(fail_count=1))
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock) as mock_add,
        ):
            response = client.post(
                "/chat",
                json={"message": "test retry"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[0]["event"] == "start"

            # Should have a retry notification delta
            retry_events = [e for e in events if e.get("event") == "delta" and "Retrying" in e.get("content", "")]
            assert len(retry_events) == 1

            # Should end successfully with the recovered content
            assert events[-1]["event"] == "end"

            # Content deltas should include the recovered response
            content_events = [e for e in events if e.get("event") == "delta" and "Recovered" in e.get("content", "")]
            assert len(content_events) == 1

            # Verify messages were saved
            assert mock_add.call_count == 2

            # Verify send_message was called twice: original + nudge (same session)
            assert mock_agent.send_message.call_count == 2

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

        async def mock_error(message: str) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentConfigurationError("ANTHROPIC_API_KEY not set")
            yield  # Make this an async generator

        client_patch, _ = _patch_client(mock_error)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
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

        async def mock_error(message: str) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentTimeoutError("Request timed out")
            yield

        client_patch, _ = _patch_client(mock_error)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
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

        async def mock_error(message: str) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentAuthError("Invalid API key")
            yield

        client_patch, _ = _patch_client(mock_error)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
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

        async def mock_error(message: str) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentRateLimitError("Rate limit exceeded")
            yield

        client_patch, _ = _patch_client(mock_error)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
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

        async def mock_error(message: str) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentRetryExhaustedError("All retries failed")
            yield

        client_patch, _ = _patch_client(mock_error)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
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
        client_patch, _ = _patch_client(_chunks_success)
        with (
            patch(GET_HISTORY_PATH, new_callable=AsyncMock, return_value=[]),
            client_patch,
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
        client_patch, _ = _patch_client(_chunks_success)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            client_patch,
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


@pytest.mark.usefixtures("valid_api_key")
class TestChatSSECaching:
    """Tests for SSE stream caching."""

    def test_cache_hit_replays_cached_stream(self, client: TestClient) -> None:
        """Test that cached responses are replayed without calling stream_response."""
        cached_events = [
            'data: {"event":"start","conversation_id":"cached-id"}\n\n',
            'data: {"event":"delta","content":"Cached response"}\n\n',
            'data: {"event":"end","conversation_id":"cached-id"}\n\n',
        ]

        client_patch, mock_agent = _patch_client(_chunks_success)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(GENERATE_CACHE_KEY_PATH, return_value="sse_cache:test"),
            patch(GET_CACHED_PATH, new_callable=AsyncMock, return_value=cached_events),
            client_patch,
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert len(events) == 3
            assert events[0]["event"] == "start"
            assert events[0]["conversation_id"] == "cached-id"
            assert events[1]["event"] == "delta"
            assert events[1]["content"] == "Cached response"
            assert events[2]["event"] == "end"

            # Agent should not be used when cache hits
            mock_agent.connect.assert_not_called()

    def test_cache_miss_generates_and_caches(self, client: TestClient) -> None:
        """Test that cache misses generate response and cache it."""
        client_patch, _ = _patch_client(_chunks_success)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(GENERATE_CACHE_KEY_PATH, return_value="sse_cache:test"),
            patch(GET_CACHED_PATH, new_callable=AsyncMock, return_value=None),
            client_patch,
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock),
            patch(CACHE_STREAM_PATH, new_callable=AsyncMock) as mock_cache,
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[0]["event"] == "start"
            assert events[-1]["event"] == "end"

            # Verify caching was called with collected events
            mock_cache.assert_called_once()
            call_args = mock_cache.call_args
            assert call_args[0][0] == "sse_cache:test"
            cached_events = call_args[0][1]
            assert len(cached_events) == 4  # start, 2 deltas, end

    def test_cache_lookup_failure_proceeds_without_cache(self, client: TestClient) -> None:
        """Test that cache lookup failure doesn't break the request."""
        client_patch, _ = _patch_client(_chunks_success)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(GENERATE_CACHE_KEY_PATH, return_value="sse_cache:test"),
            patch(GET_CACHED_PATH, new_callable=AsyncMock, side_effect=RedisUnavailableError("Connection failed")),
            client_patch,
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock),
            patch(CACHE_STREAM_PATH, new_callable=AsyncMock),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[0]["event"] == "start"
            assert events[-1]["event"] == "end"

    def test_cache_save_failure_does_not_affect_response(self, client: TestClient) -> None:
        """Test that cache save failure doesn't affect the response."""
        client_patch, _ = _patch_client(_chunks_success)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(GENERATE_CACHE_KEY_PATH, return_value="sse_cache:test"),
            patch(GET_CACHED_PATH, new_callable=AsyncMock, return_value=None),
            client_patch,
            patch(ADD_MESSAGE_PATH, new_callable=AsyncMock),
            patch(CACHE_STREAM_PATH, new_callable=AsyncMock, side_effect=RedisUnavailableError("Connection failed")),
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[0]["event"] == "start"
            # Response should complete successfully
            assert events[-1]["event"] == "end"

    def test_error_responses_are_not_cached(self, client: TestClient) -> None:
        """Test that error responses are not cached."""

        async def mock_error(message: str) -> AsyncIterator[StreamChunk]:  # noqa: ARG001
            raise AgentConfigurationError("ANTHROPIC_API_KEY not set")
            yield

        client_patch, _ = _patch_client(mock_error)
        with (
            patch(GENERATE_ID_PATH, return_value="new-conv-id"),
            patch(GENERATE_CACHE_KEY_PATH, return_value="sse_cache:test"),
            patch(GET_CACHED_PATH, new_callable=AsyncMock, return_value=None),
            client_patch,
            patch(CACHE_STREAM_PATH, new_callable=AsyncMock) as mock_cache,
        ):
            response = client.post(
                "/chat",
                json={"message": "Hello"},
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200

            events = parse_sse_events(response.text)
            assert events[-1]["event"] == "error"

            # Caching should not be called for error responses
            mock_cache.assert_not_called()

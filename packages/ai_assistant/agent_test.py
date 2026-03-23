"""Tests for the Claude Agent SDK wrapper using ClaudeSDKClient."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk._errors import CLIConnectionError, CLINotFoundError, ProcessError
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from packages.ai_assistant.agent import (
    DEFAULT_BACKOFF_MULTIPLIER,
    DEFAULT_INITIAL_BACKOFF,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TIMEOUT_SECONDS,
    AgentAuthError,
    AgentConfig,
    AgentConfigurationError,
    AgentRateLimitError,
    AgentRetryExhaustedError,
    AgentTimeoutError,
    DeadlockAgentClient,
    StreamChunk,
    _build_prompt_with_history,
    _is_retriable_error,
    _wrap_non_retriable_error,
    get_agent_config,
    get_response,
    stream_response,
    validate_configuration,
)

# Patch paths
CLIENT_PATH = "packages.ai_assistant.agent.ClaudeSDKClient"
SLEEP_PATH = "packages.ai_assistant.agent.asyncio.sleep"


@pytest.fixture
def mock_env_with_api_key() -> Generator[None]:
    """Set up environment with ANTHROPIC_API_KEY."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        yield


@pytest.fixture
def mock_env_with_oauth_token() -> Generator[None]:
    """Set up environment with CLAUDE_CODE_OAUTH_TOKEN."""
    with patch.dict("os.environ", {"CLAUDE_CODE_OAUTH_TOKEN": "test-oauth-token"}, clear=True):
        yield


@pytest.fixture
def mock_env_without_api_key() -> Generator[None]:
    """Set up environment without any authentication."""
    with patch.dict("os.environ", {}, clear=True):
        yield


class TestValidateConfiguration:
    """Tests for validate_configuration."""

    @pytest.mark.usefixtures("mock_env_with_api_key")
    def test_with_api_key(self) -> None:
        """Validation passes when ANTHROPIC_API_KEY is set."""
        validate_configuration()  # Should not raise

    @pytest.mark.usefixtures("mock_env_with_oauth_token")
    def test_with_oauth_token(self) -> None:
        """Validation passes when CLAUDE_CODE_OAUTH_TOKEN is set."""
        validate_configuration()  # Should not raise

    def test_with_both_keys(self) -> None:
        """Validation passes when both auth methods are set."""
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "test-key", "CLAUDE_CODE_OAUTH_TOKEN": "test-token"},
        ):
            validate_configuration()  # Should not raise

    def test_with_auth_token(self) -> None:
        """Validation passes when ANTHROPIC_AUTH_TOKEN is set (OpenRouter)."""
        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "or-test-token"}, clear=True):
            validate_configuration()  # Should not raise

    @pytest.mark.usefixtures("mock_env_without_api_key")
    def test_without_any_auth(self) -> None:
        """Validation fails when no authentication is set."""
        with pytest.raises(AgentConfigurationError) as exc_info:
            validate_configuration()
        assert "ANTHROPIC_API_KEY" in str(exc_info.value)
        assert "CLAUDE_CODE_OAUTH_TOKEN" in str(exc_info.value)
        assert "ANTHROPIC_AUTH_TOKEN" in str(exc_info.value)


class TestGetAgentConfig:
    """Tests for get_agent_config."""

    def test_default_values(self) -> None:
        """Default config uses expected values."""
        with patch.dict("os.environ", {}, clear=True):
            config = get_agent_config()
            assert config.model == DEFAULT_MODEL
            assert config.system_prompt == DEFAULT_SYSTEM_PROMPT
            assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
            assert config.max_retries == DEFAULT_MAX_RETRIES
            assert config.initial_backoff == DEFAULT_INITIAL_BACKOFF
            assert config.backoff_multiplier == DEFAULT_BACKOFF_MULTIPLIER

    def test_custom_model_from_env(self) -> None:
        """Model can be configured via environment variable."""
        with patch.dict("os.environ", {"AGENT_MODEL": "claude-opus-4-5"}):
            config = get_agent_config()
            assert config.model == "claude-opus-4-5"

    def test_custom_timeout_from_env(self) -> None:
        """Timeout can be configured via environment variable."""
        with patch.dict("os.environ", {"AGENT_TIMEOUT_SECONDS": "120"}):
            config = get_agent_config()
            assert config.timeout_seconds == 120.0


class TestIsRetriableError:
    """Tests for _is_retriable_error."""

    def test_authentication_error_not_retriable(self) -> None:
        """Authentication errors are not retriable."""
        assert not _is_retriable_error(Exception("authentication failed"))
        assert not _is_retriable_error(Exception("Invalid API key"))
        assert not _is_retriable_error(Exception("invalid_api_key error"))

    def test_rate_limit_error_not_retriable(self) -> None:
        """Rate limit errors are not retriable."""
        assert not _is_retriable_error(Exception("rate limit exceeded"))
        assert not _is_retriable_error(Exception("rate_limit_error"))
        assert not _is_retriable_error(Exception("Too many requests"))

    def test_cli_not_found_not_retriable(self) -> None:
        """CLI not found is not retriable."""
        assert not _is_retriable_error(CLINotFoundError("CLI not found"))

    def test_connection_error_retriable(self) -> None:
        """Connection errors are retriable."""
        assert _is_retriable_error(CLIConnectionError("Connection failed"))

    def test_process_error_retriable(self) -> None:
        """Process errors are retriable."""
        assert _is_retriable_error(ProcessError("Process crashed"))

    def test_generic_error_retriable(self) -> None:
        """Generic errors are retriable."""
        assert _is_retriable_error(Exception("Something went wrong"))


class TestWrapNonRetriableError:
    """Tests for _wrap_non_retriable_error."""

    def test_authentication_error(self) -> None:
        """Authentication errors are wrapped in AgentAuthError."""
        error = _wrap_non_retriable_error(Exception("authentication failed"))
        assert isinstance(error, AgentAuthError)
        assert "Authentication failed" in str(error)

    def test_api_key_error(self) -> None:
        """API key errors are wrapped in AgentAuthError."""
        error = _wrap_non_retriable_error(Exception("Invalid API key"))
        assert isinstance(error, AgentAuthError)

    def test_rate_limit_error(self) -> None:
        """Rate limit errors are wrapped in AgentRateLimitError."""
        error = _wrap_non_retriable_error(Exception("rate limit exceeded"))
        assert isinstance(error, AgentRateLimitError)
        assert "Rate limit exceeded" in str(error)

    def test_generic_error(self) -> None:
        """Generic errors are wrapped in AgentError."""
        from packages.ai_assistant.agent import AgentError

        error = _wrap_non_retriable_error(Exception("generic error"))
        assert isinstance(error, AgentError)
        assert not isinstance(error, (AgentAuthError, AgentRateLimitError))


class TestBuildPromptWithHistory:
    """Tests for _build_prompt_with_history."""

    def test_no_history(self) -> None:
        """Returns message as-is when no history."""
        result = _build_prompt_with_history("Hello", None)
        assert result == "Hello"

    def test_empty_history(self) -> None:
        """Returns message as-is when history is empty."""
        result = _build_prompt_with_history("Hello", [])
        assert result == "Hello"

    def test_with_history(self) -> None:
        """Formats history with message correctly."""
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        result = _build_prompt_with_history("How are you?", history)
        assert "Previous conversation:" in result
        assert "User: Hi" in result
        assert "Assistant: Hello!" in result
        assert "User: How are you?" in result


def create_mock_client(messages: list[Any]) -> MagicMock:
    """Create a mock ClaudeSDKClient that yields the given messages."""
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.interrupt = AsyncMock()

    async def mock_receive_response() -> AsyncIterator[Any]:
        for msg in messages:
            yield msg

    mock_client.receive_response = mock_receive_response
    return mock_client


class TestDeadlockAgentClient:
    """Tests for DeadlockAgentClient."""

    @pytest.fixture
    def mock_assistant_message(self) -> AssistantMessage:
        """Create a mock assistant message with text content."""
        return AssistantMessage(
            content=[TextBlock(text="Hello, world!")],
            model="claude-3-sonnet",
        )

    @pytest.fixture
    def mock_result_message(self) -> ResultMessage:
        """Create a mock result message."""
        return ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="test-session",
        )

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_context_manager_connects_and_disconnects(self) -> None:
        """Context manager properly connects and disconnects."""
        mock_client = create_mock_client([])

        with patch(CLIENT_PATH, return_value=mock_client):
            async with DeadlockAgentClient() as client:
                assert client._connected
                mock_client.connect.assert_called_once()

            mock_client.disconnect.assert_called_once()

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_send_message_success(
        self,
        mock_assistant_message: AssistantMessage,
        mock_result_message: ResultMessage,
    ) -> None:
        """Successfully sends message and receives response."""
        mock_client = create_mock_client([mock_assistant_message, mock_result_message])

        with patch(CLIENT_PATH, return_value=mock_client):
            async with DeadlockAgentClient() as client:
                chunks = []
                async for chunk in client.send_message("Hello"):
                    chunks.append(chunk)

                assert len(chunks) == 2
                assert chunks[0].content == "Hello, world!"
                assert not chunks[0].is_complete
                assert chunks[1].content == ""
                assert chunks[1].is_complete

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_send_message_without_connect_raises(self) -> None:
        """Raises error when sending without connecting first."""
        client = DeadlockAgentClient()

        with pytest.raises(AgentConfigurationError) as exc_info:
            async for _ in client.send_message("Hello"):
                pass

        assert "not connected" in str(exc_info.value)

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_interrupt(self) -> None:
        """Interrupt method calls underlying client interrupt."""
        mock_client = create_mock_client([])

        with patch(CLIENT_PATH, return_value=mock_client):
            async with DeadlockAgentClient() as client:
                await client.interrupt()
                mock_client.interrupt.assert_called_once()

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_tool_use_emits_empty_chunk_for_event_draining(self) -> None:
        """ToolUseBlock produces an empty StreamChunk so callers can drain queued tool events.

        Without this, tool start/end SSE events pile up in the queue and only
        flush when the first text chunk arrives — making all tool events appear
        at the very end instead of in real time.
        """
        # Simulate: model thinks, calls a tool, then responds with text
        tool_use_message = AssistantMessage(
            content=[ToolUseBlock(id="tool_1", name="wiki_search", input={"query": "Abrams"})],
            model="claude-3-sonnet",
        )
        text_message = AssistantMessage(
            content=[TextBlock(text="Here are the results.")],
            model="claude-3-sonnet",
        )
        result_message = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=2,
            session_id="test-session",
        )

        mock_client = create_mock_client([tool_use_message, text_message, result_message])

        with patch(CLIENT_PATH, return_value=mock_client):
            async with DeadlockAgentClient() as client:
                chunks = []
                async for chunk in client.send_message("Search for Abrams"):
                    chunks.append(chunk)

        # Should have 3 chunks:
        # 1. Empty chunk from ToolUseBlock (draining opportunity)
        # 2. Text chunk "Here are the results."
        # 3. Empty completion chunk from ResultMessage
        assert len(chunks) == 3
        assert chunks[0].content == ""
        assert not chunks[0].is_complete  # Not a completion marker
        assert chunks[1].content == "Here are the results."
        assert not chunks[1].is_complete
        assert chunks[2].content == ""
        assert chunks[2].is_complete

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_multiple_tool_uses_emit_one_chunk_each(self) -> None:
        """Each ToolUseBlock in a single message produces its own empty chunk."""
        multi_tool_message = AssistantMessage(
            content=[
                ToolUseBlock(id="tool_1", name="get_hero_mapping", input={}),
                ToolUseBlock(id="tool_2", name="deadlock_api_call", input={"path": "/stats"}),
            ],
            model="claude-3-sonnet",
        )
        result_message = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="test-session",
        )

        mock_client = create_mock_client([multi_tool_message, result_message])

        with patch(CLIENT_PATH, return_value=mock_client):
            async with DeadlockAgentClient() as client:
                chunks = []
                async for chunk in client.send_message("Get hero stats"):
                    chunks.append(chunk)

        # 2 empty chunks (one per tool use) + 1 completion chunk
        assert len(chunks) == 3
        assert all(c.content == "" for c in chunks)
        assert not chunks[0].is_complete
        assert not chunks[1].is_complete
        assert chunks[2].is_complete


class TestStreamResponse:
    """Tests for stream_response (legacy API)."""

    @pytest.fixture
    def mock_assistant_message(self) -> AssistantMessage:
        """Create a mock assistant message with text content."""
        return AssistantMessage(
            content=[TextBlock(text="Hello, world!")],
            model="claude-3-sonnet",
        )

    @pytest.fixture
    def mock_result_message(self) -> ResultMessage:
        """Create a mock result message."""
        return ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="test-session",
        )

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_stream_response_success(
        self,
        mock_assistant_message: AssistantMessage,
        mock_result_message: ResultMessage,
    ) -> None:
        """Successfully streams response chunks."""
        mock_client = create_mock_client([mock_assistant_message, mock_result_message])

        with patch(CLIENT_PATH, return_value=mock_client):
            chunks = []
            async for chunk in stream_response("Hello"):
                chunks.append(chunk)

            assert len(chunks) == 2
            assert chunks[0].content == "Hello, world!"
            assert not chunks[0].is_complete
            assert chunks[1].content == ""
            assert chunks[1].is_complete

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_stream_response_with_config(
        self,
        mock_assistant_message: AssistantMessage,
        mock_result_message: ResultMessage,
    ) -> None:
        """Uses custom config when provided."""
        captured_options = None

        def capture_client(options: Any = None) -> MagicMock:
            nonlocal captured_options
            captured_options = options
            return create_mock_client([mock_assistant_message, mock_result_message])

        config = AgentConfig(system_prompt="Custom prompt")

        with patch(CLIENT_PATH, side_effect=capture_client):
            async for _ in stream_response("Hello", config=config):
                pass

            assert captured_options is not None
            assert captured_options.system_prompt == "Custom prompt"

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_stream_response_timeout(self) -> None:
        """Raises AgentTimeoutError on timeout."""

        async def slow_receive() -> AsyncIterator[Any]:
            await asyncio.sleep(10)
            yield  # Never reached

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock()
        mock_client.receive_response = slow_receive

        config = AgentConfig(timeout_seconds=0.01)

        with (
            patch(CLIENT_PATH, return_value=mock_client),
            pytest.raises(AgentTimeoutError) as exc_info,
        ):
            async for _ in stream_response("Hello", config=config):
                pass

        assert "timed out" in str(exc_info.value)

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_stream_response_auth_error(self) -> None:
        """Raises AgentAuthError for authentication failures."""

        async def error_receive() -> AsyncIterator[Any]:
            raise ProcessError("authentication failed: invalid API key")
            yield  # Never reached

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock()
        mock_client.receive_response = error_receive

        with patch(CLIENT_PATH, return_value=mock_client), pytest.raises(AgentAuthError) as exc_info:
            async for _ in stream_response("Hello"):
                pass

        assert "Authentication failed" in str(exc_info.value)

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_stream_response_rate_limit_error(self) -> None:
        """Raises AgentRateLimitError for rate limit failures."""

        async def error_receive() -> AsyncIterator[Any]:
            raise ProcessError("rate limit exceeded")
            yield  # Never reached

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock()
        mock_client.receive_response = error_receive

        with patch(CLIENT_PATH, return_value=mock_client), pytest.raises(AgentRateLimitError) as exc_info:
            async for _ in stream_response("Hello"):
                pass

        assert "Rate limit exceeded" in str(exc_info.value)

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_stream_response_retries_on_transient_error(
        self,
        mock_assistant_message: AssistantMessage,
        mock_result_message: ResultMessage,
    ) -> None:
        """Retries on transient errors with exponential backoff."""
        attempts = 0

        async def retry_receive() -> AsyncIterator[Any]:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise CLIConnectionError("Connection failed")
            yield mock_assistant_message
            yield mock_result_message

        def create_retry_client(options: Any = None) -> MagicMock:  # noqa: ARG001
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.disconnect = AsyncMock()
            mock_client.query = AsyncMock()
            mock_client.receive_response = retry_receive
            return mock_client

        with (
            patch(CLIENT_PATH, side_effect=create_retry_client),
            patch(SLEEP_PATH, new_callable=AsyncMock) as mock_sleep,
        ):
            chunks = []
            async for chunk in stream_response("Hello"):
                chunks.append(chunk)

            assert attempts == 3  # Failed twice, succeeded on third
            assert len(chunks) == 2
            # Verify exponential backoff
            assert mock_sleep.call_count == 2
            mock_sleep.assert_any_call(DEFAULT_INITIAL_BACKOFF)
            mock_sleep.assert_any_call(DEFAULT_INITIAL_BACKOFF * DEFAULT_BACKOFF_MULTIPLIER)

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_stream_response_exhausts_retries(self) -> None:
        """Raises AgentRetryExhaustedError when all retries fail."""

        async def always_fail() -> AsyncIterator[Any]:
            raise CLIConnectionError("Connection failed")
            yield  # Never reached

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock()
        mock_client.receive_response = always_fail

        config = AgentConfig(max_retries=2, initial_backoff=0.001)

        with (
            patch(CLIENT_PATH, return_value=mock_client),
            patch(SLEEP_PATH, new_callable=AsyncMock),
            pytest.raises(AgentRetryExhaustedError) as exc_info,
        ):
            async for _ in stream_response("Hello", config=config):
                pass

        assert "Failed after 3 attempts" in str(exc_info.value)

    async def test_stream_response_without_any_auth(self) -> None:
        """Raises AgentConfigurationError when no authentication is set."""
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(AgentConfigurationError) as exc_info,
        ):
            async for _ in stream_response("Hello"):
                pass

        assert "ANTHROPIC_API_KEY" in str(exc_info.value)
        assert "CLAUDE_CODE_OAUTH_TOKEN" in str(exc_info.value)


class TestGetResponse:
    """Tests for get_response."""

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_get_response_collects_chunks(self) -> None:
        """Collects all chunks into complete response."""
        mock_assistant1 = AssistantMessage(
            content=[TextBlock(text="Hello, ")],
            model="claude-3-sonnet",
        )
        mock_assistant2 = AssistantMessage(
            content=[TextBlock(text="world!")],
            model="claude-3-sonnet",
        )
        mock_result = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="test-session",
        )

        mock_client = create_mock_client([mock_assistant1, mock_assistant2, mock_result])

        with patch(CLIENT_PATH, return_value=mock_client):
            response = await get_response("Hello")
            assert response == "Hello, world!"

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_get_response_with_history(self) -> None:
        """Passes conversation history correctly."""
        captured_message = None

        async def capture_query(message: str) -> None:
            nonlocal captured_message
            captured_message = message

        mock_assistant = AssistantMessage(
            content=[TextBlock(text="Response")],
            model="claude-3-sonnet",
        )
        mock_result = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="test-session",
        )

        mock_client = create_mock_client([mock_assistant, mock_result])
        mock_client.query = capture_query

        history = [{"role": "user", "content": "Previous message"}]

        with patch(CLIENT_PATH, return_value=mock_client):
            await get_response("New message", conversation_history=history)
            assert captured_message is not None
            assert "Previous message" in captured_message
            assert "New message" in captured_message


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_default_values(self) -> None:
        """AgentConfig has expected default values."""
        config = AgentConfig()
        assert config.system_prompt == DEFAULT_SYSTEM_PROMPT
        assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        assert config.max_retries == DEFAULT_MAX_RETRIES
        assert config.initial_backoff == DEFAULT_INITIAL_BACKOFF
        assert config.backoff_multiplier == DEFAULT_BACKOFF_MULTIPLIER

    def test_custom_values(self) -> None:
        """AgentConfig accepts custom values."""
        config = AgentConfig(
            system_prompt="Custom prompt",
            timeout_seconds=120.0,
            max_retries=5,
            initial_backoff=2.0,
            backoff_multiplier=3.0,
        )
        assert config.system_prompt == "Custom prompt"
        assert config.timeout_seconds == 120.0
        assert config.max_retries == 5
        assert config.initial_backoff == 2.0
        assert config.backoff_multiplier == 3.0


class TestStreamChunk:
    """Tests for StreamChunk dataclass."""

    def test_default_is_complete(self) -> None:
        """StreamChunk.is_complete defaults to False."""
        chunk = StreamChunk(content="text")
        assert chunk.content == "text"
        assert not chunk.is_complete

    def test_complete_chunk(self) -> None:
        """StreamChunk can mark completion."""
        chunk = StreamChunk(content="", is_complete=True)
        assert chunk.content == ""
        assert chunk.is_complete


class TestToolResultToonEncoding:
    """Tests for TOON encoding of tool results."""

    @pytest.fixture
    def mock_tool_instance(self) -> MagicMock:
        """Create a mock tool instance."""
        tool = MagicMock()
        tool.execute = AsyncMock()
        return tool

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_dict_result_encoded_as_toon(self, mock_tool_instance: MagicMock) -> None:
        """Dict results are encoded using TOON format."""
        from toon_format import encode as toon_encode

        mock_tool_instance.execute.return_value = {"name": "Alice", "age": 30}
        mock_client = create_mock_client([])

        with patch(CLIENT_PATH, return_value=mock_client):
            client = DeadlockAgentClient()
            tool_func = client._create_tool_function("test_tool", mock_tool_instance)
            result = await tool_func({})

            expected_toon = toon_encode({"name": "Alice", "age": 30})
            assert result["content"][0]["text"] == expected_toon
            assert "is_error" not in result

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_list_result_encoded_as_toon(self, mock_tool_instance: MagicMock) -> None:
        """List results are encoded using TOON format."""
        from toon_format import encode as toon_encode

        mock_tool_instance.execute.return_value = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        mock_client = create_mock_client([])

        with patch(CLIENT_PATH, return_value=mock_client):
            client = DeadlockAgentClient()
            tool_func = client._create_tool_function("test_tool", mock_tool_instance)
            result = await tool_func({})

            expected_toon = toon_encode([{"id": 1, "name": "A"}, {"id": 2, "name": "B"}])
            assert result["content"][0]["text"] == expected_toon

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_string_result_not_encoded(self, mock_tool_instance: MagicMock) -> None:
        """String results are returned as-is without TOON encoding."""
        mock_tool_instance.execute.return_value = "Simple string result"
        mock_client = create_mock_client([])

        with patch(CLIENT_PATH, return_value=mock_client):
            client = DeadlockAgentClient()
            tool_func = client._create_tool_function("test_tool", mock_tool_instance)
            result = await tool_func({})

            assert result["content"][0]["text"] == "Simple string result"

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_primitive_list_encoded_as_toon(self, mock_tool_instance: MagicMock) -> None:
        """Primitive lists are encoded using TOON format."""
        from toon_format import encode as toon_encode

        mock_tool_instance.execute.return_value = ["apple", "banana", "cherry"]
        mock_client = create_mock_client([])

        with patch(CLIENT_PATH, return_value=mock_client):
            client = DeadlockAgentClient()
            tool_func = client._create_tool_function("test_tool", mock_tool_instance)
            result = await tool_func({})

            expected_toon = toon_encode(["apple", "banana", "cherry"])
            assert result["content"][0]["text"] == expected_toon

    @pytest.mark.usefixtures("mock_env_with_api_key")
    async def test_tool_error_handling(self, mock_tool_instance: MagicMock) -> None:
        """Tool errors are properly formatted."""
        mock_tool_instance.execute.side_effect = ValueError("Something went wrong")
        mock_client = create_mock_client([])

        with patch(CLIENT_PATH, return_value=mock_client):
            client = DeadlockAgentClient()
            tool_func = client._create_tool_function("test_tool", mock_tool_instance)
            result = await tool_func({})

            assert "Error executing test_tool" in result["content"][0]["text"]
            assert result["is_error"] is True

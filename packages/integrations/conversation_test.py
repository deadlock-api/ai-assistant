# Deadlock AI Assistant - Conversation history tests

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from packages.integrations.conversation import (
    ConversationMessage,
    ConversationNotFoundError,
    add_message,
    generate_conversation_id,
    get_conversation_history,
    get_conversation_ttl,
    get_or_create_conversation,
    refresh_conversation_ttl,
    save_conversation_history,
)
from packages.integrations.redis_client import RedisUnavailableError

# Shorter path constants for patching
_REDIS_GET = "packages.integrations.conversation.redis_get"
_REDIS_SET = "packages.integrations.conversation.redis_set"
_REDIS_EXPIRE = "packages.integrations.conversation.redis_expire"


class TestConversationMessage:
    """Tests for ConversationMessage model."""

    def test_creates_message_with_role_and_content(self) -> None:
        """Test that message is created with required fields."""
        msg = ConversationMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.timestamp is not None

    def test_creates_message_with_custom_timestamp(self) -> None:
        """Test that message can have custom timestamp."""
        msg = ConversationMessage(role="assistant", content="Hi there", timestamp="2024-01-01T00:00:00Z")
        assert msg.timestamp == "2024-01-01T00:00:00Z"

    def test_serializes_to_dict(self) -> None:
        """Test that message serializes correctly."""
        msg = ConversationMessage(role="user", content="Test", timestamp="2024-01-01T00:00:00Z")
        data = msg.model_dump()
        assert data == {
            "role": "user",
            "content": "Test",
            "timestamp": "2024-01-01T00:00:00Z",
        }


class TestGetConversationTtl:
    """Tests for get_conversation_ttl function."""

    def test_returns_default_ttl_when_not_set(self) -> None:
        """Test that default TTL (24 hours) is returned."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CONVERSATION_TTL", None)
            ttl = get_conversation_ttl()
            assert ttl == 24 * 60 * 60  # 86400 seconds

    def test_returns_custom_ttl_from_env(self) -> None:
        """Test that custom TTL is read from environment."""
        with patch.dict(os.environ, {"CONVERSATION_TTL": "3600"}):
            ttl = get_conversation_ttl()
            assert ttl == 3600

    def test_returns_default_on_invalid_value(self) -> None:
        """Test that default is returned for invalid TTL values."""
        with patch.dict(os.environ, {"CONVERSATION_TTL": "not-a-number"}):
            ttl = get_conversation_ttl()
            assert ttl == 24 * 60 * 60


class TestGenerateConversationId:
    """Tests for generate_conversation_id function."""

    def test_generates_uuid(self) -> None:
        """Test that a valid UUID is generated."""
        conversation_id = generate_conversation_id()
        assert len(conversation_id) == 36  # UUID format: 8-4-4-4-12
        assert conversation_id.count("-") == 4

    def test_generates_unique_ids(self) -> None:
        """Test that generated IDs are unique."""
        ids = [generate_conversation_id() for _ in range(100)]
        assert len(set(ids)) == 100


class TestGetConversationHistory:
    """Tests for get_conversation_history function."""

    @pytest.mark.asyncio
    async def test_returns_messages_when_conversation_exists(self) -> None:
        """Test that messages are retrieved from Redis."""
        messages = [
            {"role": "user", "content": "Hello", "timestamp": "2024-01-01T00:00:00Z"},
            {"role": "assistant", "content": "Hi", "timestamp": "2024-01-01T00:00:01Z"},
        ]
        with patch(_REDIS_GET, new_callable=AsyncMock, return_value=json.dumps(messages)):
            result = await get_conversation_history("test-conv-id")
            assert len(result) == 2
            assert result[0].role == "user"
            assert result[0].content == "Hello"
            assert result[1].role == "assistant"
            assert result[1].content == "Hi"

    @pytest.mark.asyncio
    async def test_raises_not_found_when_conversation_missing(self) -> None:
        """Test that ConversationNotFoundError is raised for missing conversations."""
        with (
            patch(_REDIS_GET, new_callable=AsyncMock, return_value=None),
            pytest.raises(ConversationNotFoundError, match="Conversation test-id not found"),
        ):
            await get_conversation_history("test-id")

    @pytest.mark.asyncio
    async def test_uses_correct_key_pattern(self) -> None:
        """Test that the correct Redis key pattern is used."""
        mock_get = AsyncMock(return_value="[]")
        with patch(_REDIS_GET, mock_get):
            await get_conversation_history("my-conversation-123")
            mock_get.assert_called_once_with("conversation:my-conversation-123")

    @pytest.mark.asyncio
    async def test_propagates_redis_errors(self) -> None:
        """Test that Redis errors are propagated."""
        with (
            patch(_REDIS_GET, new_callable=AsyncMock, side_effect=RedisUnavailableError("Connection failed")),
            pytest.raises(RedisUnavailableError),
        ):
            await get_conversation_history("test-id")


class TestSaveConversationHistory:
    """Tests for save_conversation_history function."""

    @pytest.mark.asyncio
    async def test_saves_messages_to_redis(self) -> None:
        """Test that messages are saved to Redis."""
        messages = [
            ConversationMessage(role="user", content="Hello", timestamp="2024-01-01T00:00:00Z"),
        ]
        mock_set = AsyncMock(return_value=True)
        with patch(_REDIS_SET, mock_set):
            await save_conversation_history("test-conv-id", messages)
            mock_set.assert_called_once()
            call_args = mock_set.call_args
            assert call_args[0][0] == "conversation:test-conv-id"
            saved_data = json.loads(call_args[0][1])
            assert len(saved_data) == 1
            assert saved_data[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_saves_with_ttl(self) -> None:
        """Test that messages are saved with TTL."""
        messages = [ConversationMessage(role="user", content="Test", timestamp="2024-01-01T00:00:00Z")]
        mock_set = AsyncMock(return_value=True)
        with (
            patch(_REDIS_SET, mock_set),
            patch.dict(os.environ, {"CONVERSATION_TTL": "7200"}),
        ):
            await save_conversation_history("test-id", messages)
            call_args = mock_set.call_args
            assert call_args[1]["ex"] == 7200

    @pytest.mark.asyncio
    async def test_propagates_redis_errors(self) -> None:
        """Test that Redis errors are propagated."""
        with (
            patch(_REDIS_SET, new_callable=AsyncMock, side_effect=RedisUnavailableError("Connection failed")),
            pytest.raises(RedisUnavailableError),
        ):
            await save_conversation_history("test-id", [])


class TestAddMessage:
    """Tests for add_message function."""

    @pytest.mark.asyncio
    async def test_adds_message_to_existing_conversation(self) -> None:
        """Test that message is appended to existing history."""
        existing = [{"role": "user", "content": "Hello", "timestamp": "2024-01-01T00:00:00Z"}]
        mock_get = AsyncMock(return_value=json.dumps(existing))
        mock_set = AsyncMock(return_value=True)

        with patch(_REDIS_GET, mock_get), patch(_REDIS_SET, mock_set):
            result = await add_message("conv-123", "assistant", "Hi there")
            assert result.role == "assistant"
            assert result.content == "Hi there"

            # Check that save was called with 2 messages
            saved_data = json.loads(mock_set.call_args[0][1])
            assert len(saved_data) == 2
            assert saved_data[1]["content"] == "Hi there"

    @pytest.mark.asyncio
    async def test_creates_new_conversation_if_not_exists(self) -> None:
        """Test that message creates new conversation if needed."""
        mock_get = AsyncMock(return_value=None)  # Conversation doesn't exist
        mock_set = AsyncMock(return_value=True)

        with patch(_REDIS_GET, mock_get), patch(_REDIS_SET, mock_set):
            result = await add_message("new-conv", "user", "First message")
            assert result.role == "user"
            assert result.content == "First message"

            # Check that save was called with 1 message
            saved_data = json.loads(mock_set.call_args[0][1])
            assert len(saved_data) == 1


class TestGetOrCreateConversation:
    """Tests for get_or_create_conversation function."""

    @pytest.mark.asyncio
    async def test_creates_new_conversation_when_no_id_provided(self) -> None:
        """Test that new conversation is created when no ID given."""
        conversation_id, messages = await get_or_create_conversation(None)
        assert len(conversation_id) == 36  # UUID format
        assert messages == []

    @pytest.mark.asyncio
    async def test_returns_existing_conversation(self) -> None:
        """Test that existing conversation is returned."""
        existing = [{"role": "user", "content": "Hello", "timestamp": "2024-01-01T00:00:00Z"}]
        with patch(_REDIS_GET, new_callable=AsyncMock, return_value=json.dumps(existing)):
            conversation_id, messages = await get_or_create_conversation("existing-id")
            assert conversation_id == "existing-id"
            assert len(messages) == 1
            assert messages[0].content == "Hello"

    @pytest.mark.asyncio
    async def test_raises_not_found_for_missing_conversation(self) -> None:
        """Test that error is raised for missing conversation with ID."""
        with (
            patch(_REDIS_GET, new_callable=AsyncMock, return_value=None),
            pytest.raises(ConversationNotFoundError),
        ):
            await get_or_create_conversation("nonexistent-id")


class TestRefreshConversationTtl:
    """Tests for refresh_conversation_ttl function."""

    @pytest.mark.asyncio
    async def test_refreshes_ttl_on_existing_conversation(self) -> None:
        """Test that TTL is refreshed on existing conversation."""
        mock_expire = AsyncMock(return_value=True)
        with (
            patch(_REDIS_EXPIRE, mock_expire),
            patch.dict(os.environ, {"CONVERSATION_TTL": "3600"}),
        ):
            result = await refresh_conversation_ttl("conv-id")
            assert result is True
            mock_expire.assert_called_once_with("conversation:conv-id", 3600)

    @pytest.mark.asyncio
    async def test_returns_false_for_missing_conversation(self) -> None:
        """Test that False is returned for non-existent conversation."""
        mock_expire = AsyncMock(return_value=False)
        with patch(_REDIS_EXPIRE, mock_expire):
            result = await refresh_conversation_ttl("nonexistent")
            assert result is False

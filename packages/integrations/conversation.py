# Deadlock AI Assistant - Conversation History Storage

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from packages.integrations.redis_client import (
    RedisUnavailableError,
    redis_expire,
    redis_get,
    redis_set,
)

# Default TTL: 24 hours in seconds
DEFAULT_CONVERSATION_TTL = 24 * 60 * 60

# Maximum number of messages stored per conversation in Redis
MAX_STORED_MESSAGES = 50


class ConversationMessage(BaseModel):
    """A single message in a conversation."""

    role: Literal["user", "assistant"]
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ConversationError(Exception):
    """Base exception for conversation-related errors."""


class ConversationNotFoundError(ConversationError):
    """Raised when a conversation is not found."""


def get_conversation_ttl() -> int:
    """
    Get the conversation TTL from environment variable.

    Returns:
        TTL in seconds (default: 86400 = 24 hours).
    """
    ttl_str = os.environ.get("CONVERSATION_TTL")
    if ttl_str:
        try:
            return int(ttl_str)
        except ValueError:
            return DEFAULT_CONVERSATION_TTL
    return DEFAULT_CONVERSATION_TTL


def _get_conversation_key(conversation_id: str) -> str:
    """Generate the Redis key for a conversation."""
    return f"conversation:{conversation_id}"


def generate_conversation_id() -> str:
    """Generate a new unique conversation ID."""
    return str(uuid.uuid4())


async def get_conversation_history(
    conversation_id: str,
) -> list[ConversationMessage]:
    """
    Retrieve the full conversation history.

    Args:
        conversation_id: The conversation ID to retrieve.

    Returns:
        List of messages in the conversation.

    Raises:
        ConversationNotFoundError: If the conversation does not exist.
        RedisUnavailableError: If Redis is unavailable.
    """
    key = _get_conversation_key(conversation_id)
    try:
        data = await redis_get(key)
        if data is None:
            raise ConversationNotFoundError(f"Conversation {conversation_id} not found")
        messages_data = json.loads(data)
        return [ConversationMessage(**msg) for msg in messages_data]
    except RedisUnavailableError:
        raise


async def save_conversation_history(
    conversation_id: str,
    messages: list[ConversationMessage],
) -> None:
    """
    Save the full conversation history.

    Args:
        conversation_id: The conversation ID.
        messages: List of messages to save.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    key = _get_conversation_key(conversation_id)
    ttl = get_conversation_ttl()
    try:
        data = json.dumps([msg.model_dump() for msg in messages])
        await redis_set(key, data, ex=ttl)
    except RedisUnavailableError:
        raise


async def add_message(
    conversation_id: str,
    role: Literal["user", "assistant"],
    content: str,
) -> ConversationMessage:
    """
    Add a single message to the conversation history.

    Args:
        conversation_id: The conversation ID.
        role: The message role ("user" or "assistant").
        content: The message content.

    Returns:
        The created message.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    message = ConversationMessage(role=role, content=content)

    # Try to get existing history, or start with empty list
    try:
        messages = await get_conversation_history(conversation_id)
    except ConversationNotFoundError:
        messages = []

    messages.append(message)

    # Cap stored messages to prevent unbounded growth
    if len(messages) > MAX_STORED_MESSAGES:
        messages = messages[-MAX_STORED_MESSAGES:]

    await save_conversation_history(conversation_id, messages)
    return message


async def get_or_create_conversation(
    conversation_id: str | None = None,
) -> tuple[str, list[ConversationMessage]]:
    """
    Get an existing conversation or create a new one.

    Args:
        conversation_id: Optional conversation ID. If None, creates a new conversation.

    Returns:
        Tuple of (conversation_id, messages).
        For new conversations, messages is an empty list.

    Raises:
        ConversationNotFoundError: If the provided conversation_id does not exist.
        RedisUnavailableError: If Redis is unavailable.
    """
    if conversation_id is None:
        # Create new conversation
        new_id = generate_conversation_id()
        return new_id, []

    # Get existing conversation
    messages = await get_conversation_history(conversation_id)
    return conversation_id, messages


async def refresh_conversation_ttl(conversation_id: str) -> bool:
    """
    Refresh the TTL on a conversation.

    Args:
        conversation_id: The conversation ID.

    Returns:
        True if TTL was refreshed, False if conversation doesn't exist.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    key = _get_conversation_key(conversation_id)
    ttl = get_conversation_ttl()
    return await redis_expire(key, ttl)


__all__ = [
    "ConversationMessage",
    "ConversationError",
    "ConversationNotFoundError",
    "MAX_STORED_MESSAGES",
    "get_conversation_ttl",
    "generate_conversation_id",
    "get_conversation_history",
    "save_conversation_history",
    "add_message",
    "get_or_create_conversation",
    "refresh_conversation_ttl",
]

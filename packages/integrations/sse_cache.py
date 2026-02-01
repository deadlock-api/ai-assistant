# Deadlock AI Assistant - SSE Stream Caching

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncIterator

from packages.integrations.redis_client import (
    RedisUnavailableError,
    get_redis_client,
)

# Default TTL: 1 day in seconds
DEFAULT_SSE_CACHE_TTL = 24 * 60 * 60


def get_sse_cache_ttl() -> int:
    """
    Get the SSE cache TTL from environment variable.

    Returns:
        TTL in seconds (default: 1 day).
    """
    ttl_str = os.environ.get("SSE_CACHE_TTL")
    if ttl_str:
        try:
            return int(ttl_str)
        except ValueError:
            return DEFAULT_SSE_CACHE_TTL
    return DEFAULT_SSE_CACHE_TTL


def generate_cache_key(message: str, history: list[dict[str, str]]) -> str:
    """
    Generate a cache key from the message and conversation history.

    Uses SHA256 hash to create a deterministic key from the prompt content.

    Args:
        message: The user message.
        history: The conversation history as list of role/content dicts.

    Returns:
        A cache key string prefixed with "sse_cache:".
    """
    # Create a deterministic string representation
    content = json.dumps({"message": message, "history": history}, sort_keys=True)
    hash_digest = hashlib.sha256(content.encode()).hexdigest()
    return f"sse_cache:{hash_digest}"


async def get_cached_sse_stream(cache_key: str) -> list[str] | None:
    """
    Retrieve a cached SSE stream from Redis.

    Args:
        cache_key: The cache key to look up.

    Returns:
        List of serialized SSE events if found, None otherwise.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    try:
        client = await get_redis_client()
        # Get all events from the list
        events = await client.lrange(cache_key, 0, -1)  # type: ignore[misc]
        if not events:
            return None
        return events
    except RedisUnavailableError:
        raise


async def cache_sse_stream(cache_key: str, events: list[str]) -> None:
    """
    Cache an SSE stream in Redis.

    Args:
        cache_key: The cache key to store under.
        events: List of serialized SSE events to cache.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    if not events:
        return

    ttl = get_sse_cache_ttl()
    try:
        client = await get_redis_client()
        # Use pipeline for atomic operation
        async with client.pipeline() as pipe:
            # Delete any existing key first to ensure clean state
            pipe.delete(cache_key)
            # Push all events to the list
            pipe.rpush(cache_key, *events)
            # Set expiration
            pipe.expire(cache_key, ttl)
            await pipe.execute()
    except RedisUnavailableError:
        raise


async def replay_cached_stream(events: list[str]) -> AsyncIterator[str]:
    """
    Replay cached SSE events as an async iterator.

    Args:
        events: List of serialized SSE events.

    Yields:
        SSE event strings.
    """
    for event in events:
        yield event


__all__ = [
    "DEFAULT_SSE_CACHE_TTL",
    "get_sse_cache_ttl",
    "generate_cache_key",
    "get_cached_sse_stream",
    "cache_sse_stream",
    "replay_cached_stream",
]

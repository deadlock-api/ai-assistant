# Deadlock AI Assistant - Redis Client Integration

from __future__ import annotations

import logging
import os

from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.connection import ConnectionError as RedisConnectionError

logger = logging.getLogger("deadlock_assistant")

# Module-level connection pool and client
_pool: ConnectionPool | None = None
_client: Redis | None = None


class RedisError(Exception):
    """Base exception for Redis-related errors."""


class RedisUnavailableError(RedisError):
    """Raised when Redis is unavailable or connection fails."""


def get_redis_url() -> str | None:
    """Get Redis URL from environment variable."""
    return os.environ.get("REDIS_URL")


async def get_redis_client() -> Redis:
    """
    Get or create the Redis client with connection pooling.

    Returns a singleton Redis client configured from REDIS_URL.
    The connection pool is shared across all async operations.

    Raises:
        RedisUnavailableError: If REDIS_URL is not configured.
    """
    global _pool, _client

    if _client is not None:
        return _client

    redis_url = get_redis_url()
    if not redis_url:
        logger.error("Redis connection failed: REDIS_URL not set")
        raise RedisUnavailableError("REDIS_URL environment variable is not set")

    # Create connection pool with reasonable defaults for concurrent requests
    _pool = ConnectionPool.from_url(
        redis_url,
        max_connections=10,
        decode_responses=True,
    )
    _client = Redis(connection_pool=_pool)
    logger.info("Redis client initialized")
    return _client


async def close_redis_client() -> None:
    """Close the Redis client and connection pool."""
    global _pool, _client

    if _client is not None:
        await _client.aclose()
        _client = None

    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def redis_ping() -> bool:
    """
    Check if Redis is available and responding.

    Returns:
        True if Redis responds to PING, False otherwise.
    """
    try:
        client = await get_redis_client()
        result = await client.ping()  # type: ignore[misc]
        return result is True
    except RedisUnavailableError, RedisConnectionError, OSError:
        return False
    except Exception:
        return False


async def redis_get(key: str) -> str | None:
    """
    Get a value from Redis.

    Args:
        key: The key to retrieve.

    Returns:
        The value if found, None otherwise.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    try:
        client = await get_redis_client()
        result = await client.get(key)
        # Result is already a string due to decode_responses=True
        return result
    except RedisUnavailableError:
        raise
    except (RedisConnectionError, OSError) as e:
        logger.error("Redis GET failed", extra={"key": key, "error": str(e)})
        raise RedisUnavailableError(f"Redis connection failed: {e}") from e


async def redis_set(key: str, value: str, ex: int | None = None) -> bool:
    """
    Set a value in Redis.

    Args:
        key: The key to set.
        value: The value to store.
        ex: Optional expiration time in seconds.

    Returns:
        True if successful.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    try:
        client = await get_redis_client()
        await client.set(key, value, ex=ex)
        return True
    except RedisUnavailableError:
        raise
    except (RedisConnectionError, OSError) as e:
        logger.error("Redis SET failed", extra={"key": key, "error": str(e)})
        raise RedisUnavailableError(f"Redis connection failed: {e}") from e


async def redis_delete(key: str) -> bool:
    """
    Delete a key from Redis.

    Args:
        key: The key to delete.

    Returns:
        True if the key was deleted, False if it didn't exist.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    try:
        client = await get_redis_client()
        result = await client.delete(key)
        return result > 0
    except RedisUnavailableError:
        raise
    except (RedisConnectionError, OSError) as e:
        raise RedisUnavailableError(f"Redis connection failed: {e}") from e


async def redis_exists(key: str) -> bool:
    """
    Check if a key exists in Redis.

    Args:
        key: The key to check.

    Returns:
        True if the key exists, False otherwise.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    try:
        client = await get_redis_client()
        result = await client.exists(key)
        return result > 0
    except RedisUnavailableError:
        raise
    except (RedisConnectionError, OSError) as e:
        raise RedisUnavailableError(f"Redis connection failed: {e}") from e


async def redis_expire(key: str, seconds: int) -> bool:
    """
    Set expiration on a key.

    Args:
        key: The key to set expiration on.
        seconds: Time to live in seconds.

    Returns:
        True if expiration was set, False if key doesn't exist.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    try:
        client = await get_redis_client()
        result = await client.expire(key, seconds)
        return result is True
    except RedisUnavailableError:
        raise
    except (RedisConnectionError, OSError) as e:
        raise RedisUnavailableError(f"Redis connection failed: {e}") from e


async def redis_incr(key: str) -> int:
    """
    Increment a value in Redis by 1.

    Args:
        key: The key to increment.

    Returns:
        The new value after incrementing.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    try:
        client = await get_redis_client()
        result = await client.incr(key)
        return int(result)
    except RedisUnavailableError:
        raise
    except (RedisConnectionError, OSError) as e:
        raise RedisUnavailableError(f"Redis connection failed: {e}") from e


async def redis_ttl(key: str) -> int:
    """
    Get the time to live for a key in seconds.

    Args:
        key: The key to check.

    Returns:
        TTL in seconds, -1 if key has no TTL, -2 if key doesn't exist.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    try:
        client = await get_redis_client()
        result = await client.ttl(key)
        return int(result)
    except RedisUnavailableError:
        raise
    except (RedisConnectionError, OSError) as e:
        raise RedisUnavailableError(f"Redis connection failed: {e}") from e


# Type alias for external use
type RedisClient = Redis

__all__ = [
    "RedisClient",
    "RedisError",
    "RedisUnavailableError",
    "get_redis_client",
    "close_redis_client",
    "redis_ping",
    "redis_get",
    "redis_set",
    "redis_delete",
    "redis_exists",
    "redis_expire",
    "redis_incr",
    "redis_ttl",
]

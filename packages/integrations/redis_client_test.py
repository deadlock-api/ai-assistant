# Deadlock AI Assistant - Redis client tests

import os
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.integrations import redis_client
from packages.integrations.redis_client import (
    RedisUnavailableError,
    close_redis_client,
    get_redis_client,
    get_redis_url,
    redis_delete,
    redis_exists,
    redis_expire,
    redis_get,
    redis_ping,
    redis_set,
)

# Shorter path constant for patching
_CLIENT_PATH = "packages.integrations.redis_client.get_redis_client"
_POOL_PATH = "packages.integrations.redis_client.ConnectionPool.from_url"
_REDIS_PATH = "packages.integrations.redis_client.Redis"


@pytest.fixture(autouse=True)
def reset_redis_client() -> Generator[None]:
    """Reset module-level client state before and after each test."""
    redis_client._pool = None
    redis_client._client = None
    yield
    redis_client._pool = None
    redis_client._client = None


class TestGetRedisUrl:
    """Tests for get_redis_url function."""

    def test_returns_redis_url_from_env(self) -> None:
        """Test that REDIS_URL is returned when set."""
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/0"}):
            assert get_redis_url() == "redis://localhost:6379/0"

    def test_returns_none_when_not_set(self) -> None:
        """Test that None is returned when REDIS_URL is not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove REDIS_URL if it exists
            os.environ.pop("REDIS_URL", None)
            assert get_redis_url() is None


class TestGetRedisClient:
    """Tests for get_redis_client function."""

    @pytest.mark.asyncio
    async def test_raises_error_when_url_not_configured(self) -> None:
        """Test that RedisUnavailableError is raised when REDIS_URL is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("REDIS_URL", None)
            with pytest.raises(RedisUnavailableError, match="REDIS_URL environment variable is not set"):
                await get_redis_client()

    @pytest.mark.asyncio
    async def test_creates_client_with_connection_pool(self) -> None:
        """Test that client is created with connection pool from URL."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        with (
            patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/0"}),
            patch(_POOL_PATH, return_value=mock_pool) as mock_from_url,
            patch(_REDIS_PATH, return_value=mock_redis),
        ):
            client = await get_redis_client()

            mock_from_url.assert_called_once_with(
                "redis://localhost:6379/0",
                max_connections=10,
                decode_responses=True,
            )
            assert client == mock_redis

    @pytest.mark.asyncio
    async def test_returns_cached_client_on_subsequent_calls(self) -> None:
        """Test that the same client is returned on multiple calls."""
        mock_pool = MagicMock()
        mock_redis = MagicMock()

        with (
            patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/0"}),
            patch(_POOL_PATH, return_value=mock_pool),
            patch(_REDIS_PATH, return_value=mock_redis),
        ):
            client1 = await get_redis_client()
            client2 = await get_redis_client()

            assert client1 is client2


class TestCloseRedisClient:
    """Tests for close_redis_client function."""

    @pytest.mark.asyncio
    async def test_closes_client_and_pool(self) -> None:
        """Test that client and pool are closed properly."""
        mock_pool = MagicMock()
        mock_pool.aclose = AsyncMock()
        mock_redis = MagicMock()
        mock_redis.aclose = AsyncMock()

        with (
            patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/0"}),
            patch(_POOL_PATH, return_value=mock_pool),
            patch(_REDIS_PATH, return_value=mock_redis),
        ):
            await get_redis_client()
            await close_redis_client()

            mock_redis.aclose.assert_called_once()
            mock_pool.aclose.assert_called_once()
            assert redis_client._client is None
            assert redis_client._pool is None

    @pytest.mark.asyncio
    async def test_no_error_when_not_initialized(self) -> None:
        """Test that closing an uninitialized client does not raise error."""
        await close_redis_client()  # Should not raise


class TestRedisPing:
    """Tests for redis_ping function."""

    @pytest.mark.asyncio
    async def test_returns_true_when_redis_responds(self) -> None:
        """Test that ping returns True when Redis responds."""
        mock_redis = MagicMock()
        mock_redis.ping = AsyncMock(return_value=True)

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_ping()
            assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_redis_unavailable(self) -> None:
        """Test that ping returns False when Redis is unavailable."""
        with patch(
            _CLIENT_PATH,
            new_callable=AsyncMock,
            side_effect=RedisUnavailableError("Not configured"),
        ):
            result = await redis_ping()
            assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_connection_error(self) -> None:
        """Test that ping returns False on connection errors."""
        mock_redis = MagicMock()
        mock_redis.ping = AsyncMock(side_effect=OSError("Connection refused"))

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_ping()
            assert result is False


class TestRedisGet:
    """Tests for redis_get function."""

    @pytest.mark.asyncio
    async def test_returns_value_when_key_exists(self) -> None:
        """Test that get returns the value for existing key."""
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value="test_value")

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_get("test_key")
            assert result == "test_value"
            mock_redis.get.assert_called_once_with("test_key")

    @pytest.mark.asyncio
    async def test_returns_none_when_key_not_found(self) -> None:
        """Test that get returns None for non-existent key."""
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_get("nonexistent_key")
            assert result is None

    @pytest.mark.asyncio
    async def test_raises_unavailable_on_connection_error(self) -> None:
        """Test that get raises RedisUnavailableError on connection errors."""
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(side_effect=OSError("Connection refused"))

        with (
            patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis),
            pytest.raises(RedisUnavailableError, match="Redis connection failed"),
        ):
            await redis_get("test_key")


class TestRedisSet:
    """Tests for redis_set function."""

    @pytest.mark.asyncio
    async def test_sets_value_successfully(self) -> None:
        """Test that set stores value and returns True."""
        mock_redis = MagicMock()
        mock_redis.set = AsyncMock(return_value=True)

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_set("test_key", "test_value")
            assert result is True
            mock_redis.set.assert_called_once_with("test_key", "test_value", ex=None)

    @pytest.mark.asyncio
    async def test_sets_value_with_expiration(self) -> None:
        """Test that set stores value with TTL."""
        mock_redis = MagicMock()
        mock_redis.set = AsyncMock(return_value=True)

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_set("test_key", "test_value", ex=3600)
            assert result is True
            mock_redis.set.assert_called_once_with("test_key", "test_value", ex=3600)

    @pytest.mark.asyncio
    async def test_raises_unavailable_on_connection_error(self) -> None:
        """Test that set raises RedisUnavailableError on connection errors."""
        mock_redis = MagicMock()
        mock_redis.set = AsyncMock(side_effect=OSError("Connection refused"))

        with (
            patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis),
            pytest.raises(RedisUnavailableError, match="Redis connection failed"),
        ):
            await redis_set("test_key", "test_value")


class TestRedisDelete:
    """Tests for redis_delete function."""

    @pytest.mark.asyncio
    async def test_returns_true_when_key_deleted(self) -> None:
        """Test that delete returns True when key was deleted."""
        mock_redis = MagicMock()
        mock_redis.delete = AsyncMock(return_value=1)

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_delete("test_key")
            assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_key_not_found(self) -> None:
        """Test that delete returns False when key didn't exist."""
        mock_redis = MagicMock()
        mock_redis.delete = AsyncMock(return_value=0)

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_delete("nonexistent_key")
            assert result is False


class TestRedisExists:
    """Tests for redis_exists function."""

    @pytest.mark.asyncio
    async def test_returns_true_when_key_exists(self) -> None:
        """Test that exists returns True when key exists."""
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=1)

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_exists("test_key")
            assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_key_not_found(self) -> None:
        """Test that exists returns False when key doesn't exist."""
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=0)

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_exists("nonexistent_key")
            assert result is False


class TestRedisExpire:
    """Tests for redis_expire function."""

    @pytest.mark.asyncio
    async def test_returns_true_when_expiration_set(self) -> None:
        """Test that expire returns True when expiration was set."""
        mock_redis = MagicMock()
        mock_redis.expire = AsyncMock(return_value=True)

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_expire("test_key", 3600)
            assert result is True
            mock_redis.expire.assert_called_once_with("test_key", 3600)

    @pytest.mark.asyncio
    async def test_returns_false_when_key_not_found(self) -> None:
        """Test that expire returns False when key doesn't exist."""
        mock_redis = MagicMock()
        mock_redis.expire = AsyncMock(return_value=False)

        with patch(_CLIENT_PATH, new_callable=AsyncMock, return_value=mock_redis):
            result = await redis_expire("nonexistent_key", 3600)
            assert result is False

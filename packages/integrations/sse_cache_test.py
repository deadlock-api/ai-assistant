# Deadlock AI Assistant - SSE Cache Tests

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from packages.integrations.sse_cache import (
    DEFAULT_SSE_CACHE_TTL,
    cache_sse_stream,
    generate_cache_key,
    get_cached_sse_stream,
    get_sse_cache_ttl,
    replay_cached_stream,
)


class TestGetSSECacheTTL:
    """Tests for get_sse_cache_ttl function."""

    def test_returns_default_when_env_not_set(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            ttl = get_sse_cache_ttl()
            assert ttl == DEFAULT_SSE_CACHE_TTL
            assert ttl == 24 * 60 * 60  # 1 day

    def test_returns_env_value_when_set(self) -> None:
        with patch.dict("os.environ", {"SSE_CACHE_TTL": "7200"}):
            ttl = get_sse_cache_ttl()
            assert ttl == 7200

    def test_returns_default_on_invalid_env_value(self) -> None:
        with patch.dict("os.environ", {"SSE_CACHE_TTL": "invalid"}):
            ttl = get_sse_cache_ttl()
            assert ttl == DEFAULT_SSE_CACHE_TTL


class TestGenerateCacheKey:
    """Tests for generate_cache_key function."""

    def test_generates_consistent_key_for_same_input(self) -> None:
        message = "Hello, world!"
        history: list[dict[str, str]] = [{"role": "user", "content": "Hi"}]

        key1 = generate_cache_key(message, history)
        key2 = generate_cache_key(message, history)

        assert key1 == key2

    def test_generates_different_keys_for_different_messages(self) -> None:
        history: list[dict[str, str]] = []

        key1 = generate_cache_key("Hello", history)
        key2 = generate_cache_key("Goodbye", history)

        assert key1 != key2

    def test_generates_different_keys_for_different_history(self) -> None:
        message = "Hello"

        key1 = generate_cache_key(message, [])
        key2 = generate_cache_key(message, [{"role": "user", "content": "Hi"}])

        assert key1 != key2

    def test_key_has_correct_prefix(self) -> None:
        key = generate_cache_key("test", [])
        assert key.startswith("sse_cache:")

    def test_key_is_deterministic_regardless_of_dict_order(self) -> None:
        # History with same content but potentially different internal order
        history1: list[dict[str, str]] = [{"role": "user", "content": "Hi"}]
        history2: list[dict[str, str]] = [{"content": "Hi", "role": "user"}]

        key1 = generate_cache_key("Hello", history1)
        key2 = generate_cache_key("Hello", history2)

        # Keys should be the same because JSON serialization with sort_keys=True
        # produces deterministic output
        assert key1 == key2


class TestGetCachedSSEStream:
    """Tests for get_cached_sse_stream function."""

    async def test_returns_none_when_cache_miss(self) -> None:
        mock_client = AsyncMock()
        mock_client.lrange = AsyncMock(return_value=[])

        with patch(
            "packages.integrations.sse_cache.get_redis_client",
            return_value=mock_client,
        ):
            result = await get_cached_sse_stream("sse_cache:test")
            assert result is None

    async def test_returns_events_when_cache_hit(self) -> None:
        expected_events = ['data: {"event":"start"}\n\n', 'data: {"event":"end"}\n\n']
        mock_client = AsyncMock()
        mock_client.lrange = AsyncMock(return_value=expected_events)

        with patch(
            "packages.integrations.sse_cache.get_redis_client",
            return_value=mock_client,
        ):
            result = await get_cached_sse_stream("sse_cache:test")
            assert result == expected_events

    async def test_calls_lrange_with_correct_params(self) -> None:
        mock_client = AsyncMock()
        mock_client.lrange = AsyncMock(return_value=[])

        with patch(
            "packages.integrations.sse_cache.get_redis_client",
            return_value=mock_client,
        ):
            await get_cached_sse_stream("sse_cache:mykey")
            mock_client.lrange.assert_called_once_with("sse_cache:mykey", 0, -1)


class TestCacheSSEStream:
    """Tests for cache_sse_stream function."""

    async def test_does_nothing_for_empty_events(self) -> None:
        mock_client = AsyncMock()

        with patch(
            "packages.integrations.sse_cache.get_redis_client",
            return_value=mock_client,
        ):
            await cache_sse_stream("sse_cache:test", [])
            mock_client.pipeline.assert_not_called()

    async def test_caches_events_with_pipeline(self) -> None:
        events = ['data: {"event":"start"}\n\n', 'data: {"event":"end"}\n\n']

        mock_pipe = MagicMock()
        mock_pipe.delete = MagicMock()
        mock_pipe.rpush = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock()
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=None)

        mock_client = AsyncMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        with (
            patch(
                "packages.integrations.sse_cache.get_redis_client",
                return_value=mock_client,
            ),
            patch("packages.integrations.sse_cache.get_sse_cache_ttl", return_value=3600),
        ):
            await cache_sse_stream("sse_cache:mykey", events)

            mock_pipe.delete.assert_called_once_with("sse_cache:mykey")
            mock_pipe.rpush.assert_called_once_with("sse_cache:mykey", *events)
            mock_pipe.expire.assert_called_once_with("sse_cache:mykey", 3600)
            mock_pipe.execute.assert_called_once()


class TestReplayCachedStream:
    """Tests for replay_cached_stream function."""

    async def test_yields_all_events(self) -> None:
        events = ['data: {"event":"start"}\n\n', 'data: {"event":"delta"}\n\n', 'data: {"event":"end"}\n\n']

        collected = []
        async for event in replay_cached_stream(events):
            collected.append(event)

        assert collected == events

    async def test_yields_nothing_for_empty_list(self) -> None:
        collected = []
        async for event in replay_cached_stream([]):
            collected.append(event)

        assert collected == []

    async def test_preserves_event_order(self) -> None:
        events = ["first", "second", "third"]

        collected = []
        async for event in replay_cached_stream(events):
            collected.append(event)

        assert collected == events

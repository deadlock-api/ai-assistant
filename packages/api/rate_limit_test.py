# Deadlock AI Assistant - Rate limiting tests

import os
from collections.abc import Generator
from typing import Any, cast
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.api.errors import RequestIDMiddleware
from packages.api.rate_limit import (
    DEFAULT_API_KEY_LIMIT,
    DEFAULT_GLOBAL_LIMIT,
    DEFAULT_IP_LIMIT,
    DEFAULT_WINDOW_SECONDS,
    RateLimitConfig,
    RateLimitMiddleware,
    RateLimitResult,
    _get_client_ip,
    _hash_api_key,
    check_rate_limits,
    reload_rate_limit_config,
)


@pytest.fixture(autouse=True)
def reset_rate_limit_config() -> Generator[None]:
    """Reset rate limit config cache before each test."""
    # Reset to defaults before and after each test
    reload_rate_limit_config()
    yield
    reload_rate_limit_config()


def create_test_app() -> FastAPI:
    """Create a test FastAPI app with rate limit middleware."""
    test_app = FastAPI()
    # Cast needed due to Starlette's complex middleware typing with ParamSpec
    # RequestIDMiddleware must be added after rate limit so it runs first (LIFO)
    test_app.add_middleware(cast(Any, RateLimitMiddleware))
    test_app.add_middleware(cast(Any, RequestIDMiddleware))

    @test_app.get("/protected")
    async def protected_endpoint() -> dict[str, str]:
        return {"message": "success"}

    @test_app.get("/health")
    async def health_endpoint() -> dict[str, str]:
        return {"status": "healthy"}

    return test_app


class TestGetRateLimitConfig:
    """Tests for get_rate_limit_config function."""

    def test_returns_default_values(self) -> None:
        """Test that default values are used when env vars are not set."""
        env = os.environ.copy()
        for key in [
            "RATE_LIMIT_ENABLED",
            "RATE_LIMIT_GLOBAL",
            "RATE_LIMIT_PER_IP",
            "RATE_LIMIT_PER_API_KEY",
            "RATE_LIMIT_WINDOW_SECONDS",
            "RATE_LIMIT_TRUST_PROXY",
            "RATE_LIMIT_PROXY_COUNT",
        ]:
            env.pop(key, None)
        with mock.patch.dict(os.environ, env, clear=True):
            config = reload_rate_limit_config()
            assert config.enabled is True
            assert config.global_limit == DEFAULT_GLOBAL_LIMIT
            assert config.ip_limit == DEFAULT_IP_LIMIT
            assert config.api_key_limit == DEFAULT_API_KEY_LIMIT
            assert config.window_seconds == DEFAULT_WINDOW_SECONDS
            assert config.trust_proxy is False
            assert config.proxy_count == 1

    def test_respects_env_vars(self) -> None:
        """Test that environment variables override defaults."""
        with mock.patch.dict(
            os.environ,
            {
                "RATE_LIMIT_ENABLED": "true",
                "RATE_LIMIT_GLOBAL": "2000",
                "RATE_LIMIT_PER_IP": "200",
                "RATE_LIMIT_PER_API_KEY": "1000",
                "RATE_LIMIT_WINDOW_SECONDS": "120",
                "RATE_LIMIT_TRUST_PROXY": "true",
                "RATE_LIMIT_PROXY_COUNT": "2",
            },
        ):
            config = reload_rate_limit_config()
            assert config.enabled is True
            assert config.global_limit == 2000
            assert config.ip_limit == 200
            assert config.api_key_limit == 1000
            assert config.window_seconds == 120
            assert config.trust_proxy is True
            assert config.proxy_count == 2

    def test_disabled_when_env_false(self) -> None:
        """Test that rate limiting can be disabled."""
        with mock.patch.dict(os.environ, {"RATE_LIMIT_ENABLED": "false"}):
            config = reload_rate_limit_config()
            assert config.enabled is False


class TestHashApiKey:
    """Tests for _hash_api_key function."""

    def test_returns_consistent_hash(self) -> None:
        """Test that the same key always returns the same hash."""
        hash1 = _hash_api_key("test-api-key")
        hash2 = _hash_api_key("test-api-key")
        assert hash1 == hash2

    def test_different_keys_produce_different_hashes(self) -> None:
        """Test that different keys produce different hashes."""
        hash1 = _hash_api_key("key1")
        hash2 = _hash_api_key("key2")
        assert hash1 != hash2

    def test_returns_16_character_hash(self) -> None:
        """Test that the hash is truncated to 16 characters."""
        result = _hash_api_key("any-key")
        assert len(result) == 16


class TestGetClientIP:
    """Tests for _get_client_ip function."""

    def _make_config(self, trust_proxy: bool = False, proxy_count: int = 1) -> RateLimitConfig:
        """Create a config for testing."""
        return RateLimitConfig(
            global_limit=1000,
            ip_limit=100,
            api_key_limit=500,
            window_seconds=60,
            enabled=True,
            trust_proxy=trust_proxy,
            proxy_count=proxy_count,
        )

    def test_prefers_cf_connecting_ip_over_x_forwarded_for(self) -> None:
        """Test that CF-Connecting-IP is preferred when present (Cloudflare)."""
        mock_request = MagicMock()
        mock_request.headers = {
            "CF-Connecting-IP": "203.0.113.100",
            "X-Forwarded-For": "spoofed_ip, 203.0.113.200",
        }
        mock_request.client = MagicMock(host="10.0.0.1")

        config = self._make_config(trust_proxy=True, proxy_count=1)
        result = _get_client_ip(mock_request, config)
        assert result == "203.0.113.100"

    def test_extracts_ip_from_x_forwarded_for_when_trusted(self) -> None:
        """Test that X-Forwarded-For header is used when CF-Connecting-IP is absent."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "203.0.113.195"}
        mock_request.client = MagicMock(host="10.0.0.1")

        config = self._make_config(trust_proxy=True, proxy_count=1)
        result = _get_client_ip(mock_request, config)
        assert result == "203.0.113.195"

    def test_extracts_rightmost_ip_with_single_proxy(self) -> None:
        """Test that the rightmost IP is used with proxy_count=1."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "fake_ip, real_client_ip"}
        mock_request.client = MagicMock(host="10.0.0.1")

        config = self._make_config(trust_proxy=True, proxy_count=1)
        result = _get_client_ip(mock_request, config)
        assert result == "real_client_ip"

    def test_extracts_correct_ip_with_multiple_proxies(self) -> None:
        """Test that the correct IP is extracted with proxy_count=2."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "fake_ip, real_client_ip, cdn_ip"}
        mock_request.client = MagicMock(host="10.0.0.1")

        config = self._make_config(trust_proxy=True, proxy_count=2)
        result = _get_client_ip(mock_request, config)
        assert result == "real_client_ip"

    def test_ignores_x_forwarded_for_when_not_trusted(self) -> None:
        """Test that X-Forwarded-For is ignored when trust_proxy is false."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "spoofed_ip"}
        mock_request.client = MagicMock(host="192.168.1.1")

        config = self._make_config(trust_proxy=False)
        result = _get_client_ip(mock_request, config)
        assert result == "192.168.1.1"

    def test_falls_back_to_client_host(self) -> None:
        """Test fallback to request.client.host when no X-Forwarded-For."""
        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.client = MagicMock(host="192.168.1.1")

        config = self._make_config(trust_proxy=True)
        result = _get_client_ip(mock_request, config)
        assert result == "192.168.1.1"

    def test_returns_unknown_when_no_client(self) -> None:
        """Test that 'unknown' is returned when client is None."""
        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.client = None

        config = self._make_config(trust_proxy=False)
        result = _get_client_ip(mock_request, config)
        assert result == "unknown"

    def test_falls_back_to_first_ip_when_chain_shorter_than_proxy_count(self) -> None:
        """Test fallback when X-Forwarded-For has fewer IPs than proxy_count."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "single_ip"}
        mock_request.client = MagicMock(host="10.0.0.1")

        config = self._make_config(trust_proxy=True, proxy_count=3)
        result = _get_client_ip(mock_request, config)
        assert result == "single_ip"


class TestCheckRateLimits:
    """Tests for check_rate_limits function."""

    def _make_config(
        self,
        global_limit: int = 100,
        ip_limit: int = 50,
        api_key_limit: int = 75,
    ) -> RateLimitConfig:
        """Create a config for testing."""
        return RateLimitConfig(
            global_limit=global_limit,
            ip_limit=ip_limit,
            api_key_limit=api_key_limit,
            window_seconds=60,
            enabled=True,
            trust_proxy=False,
            proxy_count=1,
        )

    @pytest.mark.asyncio
    async def test_allows_request_under_all_limits(self) -> None:
        """Test that request is allowed when under all limits."""
        mock_request = MagicMock()
        mock_request.headers = {"X-API-Key": "test-key"}
        mock_request.client = MagicMock(host="192.168.1.1")

        config = self._make_config()

        with mock.patch("packages.api.rate_limit._check_rate_limit", new_callable=AsyncMock) as mock_check:
            # Return counts under all limits
            mock_check.side_effect = [
                (10, 60),  # API key check
                (5, 60),  # IP check
                (50, 60),  # Global check
            ]

            result = await check_rate_limits(mock_request, config)

            assert result.allowed is True
            assert mock_check.call_count == 3

    @pytest.mark.asyncio
    async def test_rejects_when_api_key_limit_exceeded(self) -> None:
        """Test that request is rejected when API key limit is exceeded."""
        mock_request = MagicMock()
        mock_request.headers = {"X-API-Key": "test-key"}
        mock_request.client = MagicMock(host="192.168.1.1")

        config = self._make_config(api_key_limit=10)

        with mock.patch("packages.api.rate_limit._check_rate_limit", new_callable=AsyncMock) as mock_check:
            # API key count exceeds limit
            mock_check.return_value = (11, 30)

            result = await check_rate_limits(mock_request, config)

            assert result.allowed is False
            assert result.limit_type == "api_key"
            assert result.limit == 10
            assert result.remaining == 0
            assert result.reset_seconds == 30

    @pytest.mark.asyncio
    async def test_rejects_when_ip_limit_exceeded(self) -> None:
        """Test that request is rejected when IP limit is exceeded."""
        mock_request = MagicMock()
        mock_request.headers = {}  # No API key
        mock_request.client = MagicMock(host="192.168.1.1")

        config = self._make_config(ip_limit=10)

        with mock.patch("packages.api.rate_limit._check_rate_limit", new_callable=AsyncMock) as mock_check:
            # IP count exceeds limit
            mock_check.return_value = (11, 45)

            result = await check_rate_limits(mock_request, config)

            assert result.allowed is False
            assert result.limit_type == "ip"
            assert result.limit == 10
            assert result.remaining == 0
            assert result.reset_seconds == 45

    @pytest.mark.asyncio
    async def test_rejects_when_global_limit_exceeded(self) -> None:
        """Test that request is rejected when global limit is exceeded."""
        mock_request = MagicMock()
        mock_request.headers = {}  # No API key
        mock_request.client = MagicMock(host="192.168.1.1")

        config = self._make_config(global_limit=10, ip_limit=100)

        with mock.patch("packages.api.rate_limit._check_rate_limit", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = [
                (5, 60),  # IP check - under limit
                (11, 20),  # Global check - exceeds limit
            ]

            result = await check_rate_limits(mock_request, config)

            assert result.allowed is False
            assert result.limit_type == "global"
            assert result.limit == 10
            assert result.remaining == 0
            assert result.reset_seconds == 20


class TestRateLimitMiddleware:
    """Tests for RateLimitMiddleware class."""

    def test_bypasses_public_paths(self) -> None:
        """Test that public paths bypass rate limiting."""
        with mock.patch("packages.api.rate_limit.check_rate_limits") as mock_check:
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/health")

            assert response.status_code == 200
            mock_check.assert_not_called()

    def test_allows_request_under_limit(self) -> None:
        """Test that request under limit is allowed with rate limit headers."""
        with mock.patch(
            "packages.api.rate_limit.check_rate_limits",
            new_callable=AsyncMock,
            return_value=RateLimitResult(
                allowed=True,
                limit=100,
                remaining=99,
                reset_seconds=60,
                limit_type="ip",
            ),
        ):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/protected")

            assert response.status_code == 200
            assert response.headers["X-RateLimit-Limit"] == "100"
            assert response.headers["X-RateLimit-Remaining"] == "99"
            assert response.headers["X-RateLimit-Reset"] == "60"

    def test_rejects_request_over_limit(self) -> None:
        """Test that request over limit returns 429 with proper headers."""
        with mock.patch(
            "packages.api.rate_limit.check_rate_limits",
            new_callable=AsyncMock,
            return_value=RateLimitResult(
                allowed=False,
                limit=10,
                remaining=0,
                reset_seconds=30,
                limit_type="ip",
            ),
        ):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/protected")

            assert response.status_code == 429
            assert response.headers["X-RateLimit-Limit"] == "10"
            assert response.headers["X-RateLimit-Remaining"] == "0"
            assert response.headers["X-RateLimit-Reset"] == "30"
            assert response.headers["Retry-After"] == "30"

            data = response.json()
            assert data["code"] == "RATE_LIMIT_EXCEEDED"
            assert "ip" in data["error"]

    def test_fails_open_when_redis_unavailable(self) -> None:
        """Test that requests are allowed when Redis is unavailable."""
        from packages.integrations.redis_client import RedisUnavailableError

        with mock.patch(
            "packages.api.rate_limit.check_rate_limits",
            new_callable=AsyncMock,
            side_effect=RedisUnavailableError("Redis not available"),
        ):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/protected")

            # Request should be allowed (fail-open)
            assert response.status_code == 200

    def test_skips_when_disabled(self) -> None:
        """Test that rate limiting can be disabled via environment variable."""
        with mock.patch.dict(os.environ, {"RATE_LIMIT_ENABLED": "false"}):
            # Reload config to pick up the new environment variable
            reload_rate_limit_config()
            with mock.patch("packages.api.rate_limit.check_rate_limits") as mock_check:
                app = create_test_app()
                client = TestClient(app)
                response = client.get("/protected")

                assert response.status_code == 200
                mock_check.assert_not_called()


class TestRateLimitErrorResponse:
    """Tests for rate limit error response format."""

    def test_api_key_rate_limit_error_message(self) -> None:
        """Test error message for API key rate limit."""
        with mock.patch(
            "packages.api.rate_limit.check_rate_limits",
            new_callable=AsyncMock,
            return_value=RateLimitResult(
                allowed=False,
                limit=500,
                remaining=0,
                reset_seconds=60,
                limit_type="api_key",
            ),
        ):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/protected")

            data = response.json()
            assert "api_key" in data["error"]

    def test_global_rate_limit_error_message(self) -> None:
        """Test error message for global rate limit."""
        with mock.patch(
            "packages.api.rate_limit.check_rate_limits",
            new_callable=AsyncMock,
            return_value=RateLimitResult(
                allowed=False,
                limit=1000,
                remaining=0,
                reset_seconds=60,
                limit_type="global",
            ),
        ):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/protected")

            data = response.json()
            assert "global" in data["error"]

    def test_response_includes_request_id(self) -> None:
        """Test that error response includes request ID."""
        with mock.patch(
            "packages.api.rate_limit.check_rate_limits",
            new_callable=AsyncMock,
            return_value=RateLimitResult(
                allowed=False,
                limit=10,
                remaining=0,
                reset_seconds=30,
                limit_type="ip",
            ),
        ):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/protected")

            data = response.json()
            assert "request_id" in data
            assert data["request_id"] != ""

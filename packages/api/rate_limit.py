# Deadlock AI Assistant - Rate Limiting Middleware
#
# Provides configurable rate limiting with Redis backend.
# Supports global, per-IP, and per-API key rate limits.

import hashlib
import logging
import os
from dataclasses import dataclass

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from packages.api.errors import ErrorCode, ErrorResponse, get_request_id
from packages.integrations.redis_client import (
    RedisUnavailableError,
    redis_expire,
    redis_incr,
    redis_ttl,
)

logger = logging.getLogger("deadlock_assistant")

# Paths that bypass rate limiting
PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/auth/patreon",
    "/auth/patreon/callback",
    "/auth/patreon/logout",
    "/auth/patreon/status",
}

# Redis key prefixes for rate limiting
RATE_LIMIT_PREFIX = "ratelimit:"
GLOBAL_KEY = f"{RATE_LIMIT_PREFIX}global"
IP_KEY_PREFIX = f"{RATE_LIMIT_PREFIX}ip:"
API_KEY_PREFIX = f"{RATE_LIMIT_PREFIX}apikey:"
PATRON_KEY_PREFIX = f"{RATE_LIMIT_PREFIX}patron:"

# Default rate limits (requests per window)
DEFAULT_GLOBAL_LIMIT = 1000
DEFAULT_IP_LIMIT = 100
DEFAULT_API_KEY_LIMIT = 500

# Default window size in seconds
DEFAULT_WINDOW_SECONDS = 60

# Cached configuration (loaded once at startup)
_cached_config: RateLimitConfig | None = None


class RateLimitConfigError(Exception):
    """Raised when rate limit configuration is invalid."""


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    global_limit: int
    ip_limit: int
    api_key_limit: int
    window_seconds: int
    enabled: bool
    trust_proxy: bool
    proxy_count: int


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    limit_type: str  # "global", "ip", "api_key", or "patron"


def _parse_positive_int(value: str, name: str) -> int:
    """Parse a string as a positive integer.

    Args:
        value: The string value to parse.
        name: The name of the configuration for error messages.

    Returns:
        The parsed positive integer.

    Raises:
        RateLimitConfigError: If the value is not a positive integer.
    """
    try:
        result = int(value)
    except ValueError as err:
        raise RateLimitConfigError(f"{name} must be an integer, got: {value!r}") from err

    if result <= 0:
        raise RateLimitConfigError(f"{name} must be a positive integer (> 0), got: {result}")

    return result


def _load_rate_limit_config() -> RateLimitConfig:
    """Load and validate rate limit configuration from environment variables.

    Returns:
        Validated RateLimitConfig instance.

    Raises:
        RateLimitConfigError: If any configuration value is invalid.
    """
    enabled = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() != "false"
    # Default to not trusting X-Forwarded-For for security
    trust_proxy = os.environ.get("RATE_LIMIT_TRUST_PROXY", "false").lower() == "true"

    global_limit = _parse_positive_int(
        os.environ.get("RATE_LIMIT_GLOBAL", str(DEFAULT_GLOBAL_LIMIT)),
        "RATE_LIMIT_GLOBAL",
    )
    ip_limit = _parse_positive_int(
        os.environ.get("RATE_LIMIT_PER_IP", str(DEFAULT_IP_LIMIT)),
        "RATE_LIMIT_PER_IP",
    )
    api_key_limit = _parse_positive_int(
        os.environ.get("RATE_LIMIT_PER_API_KEY", str(DEFAULT_API_KEY_LIMIT)),
        "RATE_LIMIT_PER_API_KEY",
    )
    window_seconds = _parse_positive_int(
        os.environ.get("RATE_LIMIT_WINDOW_SECONDS", str(DEFAULT_WINDOW_SECONDS)),
        "RATE_LIMIT_WINDOW_SECONDS",
    )
    proxy_count = _parse_positive_int(
        os.environ.get("RATE_LIMIT_PROXY_COUNT", "1"),
        "RATE_LIMIT_PROXY_COUNT",
    )

    return RateLimitConfig(
        global_limit=global_limit,
        ip_limit=ip_limit,
        api_key_limit=api_key_limit,
        window_seconds=window_seconds,
        enabled=enabled,
        trust_proxy=trust_proxy,
        proxy_count=proxy_count,
    )


def get_rate_limit_config() -> RateLimitConfig:
    """Get the cached rate limit configuration.

    Configuration is loaded once from environment variables on first access
    and cached for subsequent calls. Use reload_rate_limit_config() to
    force a reload.

    Environment variables:
        RATE_LIMIT_ENABLED: Enable/disable rate limiting (default: true)
        RATE_LIMIT_GLOBAL: Global requests per window (default: 1000)
        RATE_LIMIT_PER_IP: Requests per IP per window (default: 100)
        RATE_LIMIT_PER_API_KEY: Requests per API key per window (default: 500)
        RATE_LIMIT_WINDOW_SECONDS: Window duration in seconds (default: 60)
        RATE_LIMIT_TRUST_PROXY: Trust X-Forwarded-For header (default: false)
        RATE_LIMIT_PROXY_COUNT: Number of trusted proxies in chain (default: 1)

    Returns:
        The cached RateLimitConfig instance.

    Raises:
        RateLimitConfigError: If any configuration value is invalid.
    """
    global _cached_config
    if _cached_config is None:
        _cached_config = _load_rate_limit_config()
    return _cached_config


def reload_rate_limit_config() -> RateLimitConfig:
    """Force reload of rate limit configuration from environment variables.

    Clears the cached configuration and reloads from environment variables.
    Useful for testing or when environment variables have changed.

    Returns:
        The newly loaded RateLimitConfig instance.

    Raises:
        RateLimitConfigError: If any configuration value is invalid.
    """
    global _cached_config
    _cached_config = None
    return get_rate_limit_config()


def _hash_api_key(api_key: str) -> str:
    """Hash an API key for use as a Redis key component.

    Args:
        api_key: The API key to hash.

    Returns:
        SHA-256 hash of the API key (first 16 characters).
    """
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _get_client_ip(request: Request, config: RateLimitConfig) -> str:
    """Extract client IP address from request.

    When trust_proxy is enabled, checks headers in order of reliability:
    1. CF-Connecting-IP (Cloudflare's verified client IP - most reliable)
    2. X-Forwarded-For (parsed based on proxy_count)

    Security note: These headers can be spoofed by clients if not behind
    a trusted proxy. Only enable trust_proxy when deployed behind a properly
    configured reverse proxy (Cloudflare, nginx, etc.).

    Args:
        request: The FastAPI request.
        config: Rate limit configuration with proxy settings.

    Returns:
        The client IP address.

    Example with Cloudflare:
        CF-Connecting-IP: "real_client_ip"
        Returns: "real_client_ip" (Cloudflare's verified client IP)

    Example with proxy_count=2 (non-Cloudflare CDN -> Load Balancer -> App):
        X-Forwarded-For: "fake_ip, real_client_ip, cdn_ip"
        Returns: "real_client_ip" (second from right)
    """
    if config.trust_proxy:
        # Prefer Cloudflare's CF-Connecting-IP header (cannot be spoofed when behind CF)
        cf_connecting_ip = request.headers.get("CF-Connecting-IP")
        if cf_connecting_ip:
            return cf_connecting_ip.strip()

        # Fall back to X-Forwarded-For parsing
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ips = [ip.strip() for ip in forwarded_for.split(",")]
            # Take the IP at position -proxy_count (counting from right)
            # This is the IP as seen by the first trusted proxy
            if len(ips) >= config.proxy_count:
                return ips[-config.proxy_count]
            # If fewer IPs than proxy_count, the chain is shorter than expected
            # Take the first (leftmost) IP as the client
            return ips[0]

    # Use direct client connection IP (most secure, but requires direct connection)
    if request.client:
        return request.client.host
    return "unknown"


async def _check_rate_limit(key: str, window_seconds: int) -> tuple[int, int]:
    """Check and increment rate limit counter.

    Uses a simple fixed-window counter algorithm:
    1. Increment the counter for the current window
    2. Set expiration if this is a new window
    3. Return current count and remaining TTL

    Args:
        key: Redis key for the rate limit counter.
        window_seconds: Window duration in seconds.

    Returns:
        Tuple of (current_count, ttl_seconds).

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    # Increment counter
    current_count = await redis_incr(key)

    # If this is the first request in the window, set expiration
    if current_count == 1:
        await redis_expire(key, window_seconds)
        return current_count, window_seconds

    # Get remaining TTL
    ttl = await redis_ttl(key)
    if ttl < 0:
        # Key has no TTL (shouldn't happen, but handle gracefully)
        await redis_expire(key, window_seconds)
        ttl = window_seconds

    return current_count, ttl


async def check_rate_limits(
    request: Request,
    config: RateLimitConfig,
) -> RateLimitResult:
    """Check all applicable rate limits for a request.

    Checks rate limits in order of specificity:
    1. Per-API key (if API key is present)
    2. Per-patron (if Patreon session is present, uses tier-specific limit)
    3. Per-IP (fallback for unauthenticated requests)
    4. Global

    Args:
        request: The FastAPI request.
        config: Rate limit configuration.

    Returns:
        RateLimitResult indicating whether the request is allowed.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    api_key = request.headers.get("X-API-Key")
    if api_key is not None:
        api_key = api_key.strip() or None  # Treat empty/whitespace as no API key

    # Track API key rate limit results if present
    api_key_ttl = 0
    api_key_remaining = 0

    # Check per-API key limit if API key is present
    if api_key:
        key_hash = _hash_api_key(api_key)
        key = f"{API_KEY_PREFIX}{key_hash}"
        api_key_count, api_key_ttl = await _check_rate_limit(key, config.window_seconds)
        api_key_remaining = max(0, config.api_key_limit - api_key_count)
        if api_key_count > config.api_key_limit:
            return RateLimitResult(
                allowed=False,
                limit=config.api_key_limit,
                remaining=0,
                reset_seconds=api_key_ttl,
                limit_type="api_key",
            )

    # Check for patron authentication (set by AuthMiddleware)
    patron_user_id = getattr(request.state, "patron_user_id", None)
    patron_rate_limit = getattr(request.state, "patron_rate_limit", None)

    # Track patron rate limit results if authenticated
    patron_ttl = 0
    patron_remaining = 0

    # Check per-patron limit if patron is authenticated
    if patron_user_id is not None and patron_rate_limit is not None:
        patron_key = f"{PATRON_KEY_PREFIX}{patron_user_id}"
        patron_count, patron_ttl = await _check_rate_limit(patron_key, config.window_seconds)
        patron_remaining = max(0, patron_rate_limit - patron_count)
        if patron_count > patron_rate_limit:
            return RateLimitResult(
                allowed=False,
                limit=patron_rate_limit,
                remaining=0,
                reset_seconds=patron_ttl,
                limit_type="patron",
            )

    # Check per-IP limit (only for non-patron requests)
    ip_ttl = 0
    ip_remaining = 0

    if patron_user_id is None:
        client_ip = _get_client_ip(request, config)
        ip_key = f"{IP_KEY_PREFIX}{client_ip}"
        ip_count, ip_ttl = await _check_rate_limit(ip_key, config.window_seconds)
        ip_remaining = max(0, config.ip_limit - ip_count)
        if ip_count > config.ip_limit:
            return RateLimitResult(
                allowed=False,
                limit=config.ip_limit,
                remaining=0,
                reset_seconds=ip_ttl,
                limit_type="ip",
            )

    # Check global limit
    global_count, global_ttl = await _check_rate_limit(GLOBAL_KEY, config.window_seconds)
    global_remaining = max(0, config.global_limit - global_count)
    if global_count > config.global_limit:
        return RateLimitResult(
            allowed=False,
            limit=config.global_limit,
            remaining=0,
            reset_seconds=global_ttl,
            limit_type="global",
        )

    # Request is allowed - return the most restrictive remaining count
    # This helps clients understand their most limiting factor
    if (
        api_key
        and api_key_remaining <= global_remaining
        and (patron_user_id is None or api_key_remaining <= patron_remaining)
    ):
        return RateLimitResult(
            allowed=True,
            limit=config.api_key_limit,
            remaining=api_key_remaining,
            reset_seconds=api_key_ttl,
            limit_type="api_key",
        )

    # Patron rate limit (replaces IP rate limit for authenticated patrons)
    if patron_user_id is not None and patron_rate_limit is not None and patron_remaining <= global_remaining:
        return RateLimitResult(
            allowed=True,
            limit=patron_rate_limit,
            remaining=patron_remaining,
            reset_seconds=patron_ttl,
            limit_type="patron",
        )

    # IP rate limit (for non-patron requests)
    if patron_user_id is None and ip_remaining <= global_remaining:
        return RateLimitResult(
            allowed=True,
            limit=config.ip_limit,
            remaining=ip_remaining,
            reset_seconds=ip_ttl,
            limit_type="ip",
        )

    return RateLimitResult(
        allowed=True,
        limit=config.global_limit,
        remaining=global_remaining,
        reset_seconds=global_ttl,
        limit_type="global",
    )


def _rate_limit_response(result: RateLimitResult) -> Response:
    """Create a 429 Too Many Requests response.

    Args:
        result: The rate limit check result.

    Returns:
        Response with structured error body and rate limit headers.
    """
    error_response = ErrorResponse(
        error=f"Rate limit exceeded ({result.limit_type})",
        code=ErrorCode.RATE_LIMIT_EXCEEDED,
        request_id=get_request_id(),
    )
    response = Response(
        content=error_response.model_dump_json(),
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        media_type="application/json",
    )
    _add_rate_limit_headers(response, result)
    return response


def _add_rate_limit_headers(response: Response, result: RateLimitResult) -> None:
    """Add rate limit headers to a response.

    Headers follow the IETF draft standard:
    https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-ratelimit-headers

    Args:
        response: The response to add headers to.
        result: The rate limit check result.
    """
    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    response.headers["X-RateLimit-Reset"] = str(result.reset_seconds)
    if not result.allowed:
        response.headers["Retry-After"] = str(result.reset_seconds)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce rate limits.

    Rate limits are checked against Redis counters using a fixed-window
    algorithm. If Redis is unavailable, requests are allowed through
    (fail-open behavior to prevent blocking legitimate traffic).

    Rate limits are applied in order:
    1. Per-API key (if X-API-Key header is present)
    2. Per-IP address
    3. Global

    A request is rejected if any limit is exceeded.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        config = get_rate_limit_config()

        # Skip rate limiting if disabled or in dev mode
        if not config.enabled or os.environ.get("DEV_MODE", "false").lower() == "true":
            return await call_next(request)

        # Skip rate limiting for public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Check rate limits
        try:
            result = await check_rate_limits(request, config)
        except RedisUnavailableError:
            logger.warning("Rate limit check failed: Redis unavailable, failing open", extra={"path": request.url.path})
            # Fail open - allow request through if Redis is unavailable
            return await call_next(request)

        if not result.allowed:
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "limit_type": result.limit_type,
                    "limit": result.limit,
                    "reset_seconds": result.reset_seconds,
                    "path": request.url.path,
                },
            )
            return _rate_limit_response(result)

        # Proceed with request
        response = await call_next(request)

        # Add rate limit headers to successful responses
        _add_rate_limit_headers(response, result)

        return response

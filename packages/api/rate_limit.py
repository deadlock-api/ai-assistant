# Deadlock AI Assistant - Rate Limiting Middleware
#
# Provides configurable rate limiting with Redis backend.
# Supports global, per-IP, and per-API key rate limits.

import hashlib
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

# Paths that bypass rate limiting
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

# Redis key prefixes for rate limiting
RATE_LIMIT_PREFIX = "ratelimit:"
GLOBAL_KEY = f"{RATE_LIMIT_PREFIX}global"
IP_KEY_PREFIX = f"{RATE_LIMIT_PREFIX}ip:"
API_KEY_PREFIX = f"{RATE_LIMIT_PREFIX}apikey:"

# Default rate limits (requests per window)
DEFAULT_GLOBAL_LIMIT = 1000
DEFAULT_IP_LIMIT = 100
DEFAULT_API_KEY_LIMIT = 500

# Default window size in seconds
DEFAULT_WINDOW_SECONDS = 60


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    global_limit: int
    ip_limit: int
    api_key_limit: int
    window_seconds: int
    enabled: bool


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    limit_type: str  # "global", "ip", or "api_key"


def get_rate_limit_config() -> RateLimitConfig:
    """Load rate limit configuration from environment variables.

    Environment variables:
        RATE_LIMIT_ENABLED: Enable/disable rate limiting (default: true)
        RATE_LIMIT_GLOBAL: Global requests per window (default: 1000)
        RATE_LIMIT_PER_IP: Requests per IP per window (default: 100)
        RATE_LIMIT_PER_API_KEY: Requests per API key per window (default: 500)
        RATE_LIMIT_WINDOW_SECONDS: Window duration in seconds (default: 60)
    """
    enabled = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() != "false"
    global_limit = int(os.environ.get("RATE_LIMIT_GLOBAL", str(DEFAULT_GLOBAL_LIMIT)))
    ip_limit = int(os.environ.get("RATE_LIMIT_PER_IP", str(DEFAULT_IP_LIMIT)))
    api_key_limit = int(os.environ.get("RATE_LIMIT_PER_API_KEY", str(DEFAULT_API_KEY_LIMIT)))
    window_seconds = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", str(DEFAULT_WINDOW_SECONDS)))

    return RateLimitConfig(
        global_limit=global_limit,
        ip_limit=ip_limit,
        api_key_limit=api_key_limit,
        window_seconds=window_seconds,
        enabled=enabled,
    )


def _hash_api_key(api_key: str) -> str:
    """Hash an API key for use as a Redis key component.

    Args:
        api_key: The API key to hash.

    Returns:
        SHA-256 hash of the API key (first 16 characters).
    """
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _get_client_ip(request: Request) -> str:
    """Extract client IP address from request.

    Checks X-Forwarded-For header first (for proxied requests),
    then falls back to the direct client host.

    Args:
        request: The FastAPI request.

    Returns:
        The client IP address.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, take the first (client)
        return forwarded_for.split(",")[0].strip()

    # Fall back to direct client
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
    2. Per-IP
    3. Global

    Args:
        request: The FastAPI request.
        config: Rate limit configuration.

    Returns:
        RateLimitResult indicating whether the request is allowed.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    api_key = request.headers.get("X-API-Key")

    # Track API key rate limit results if present
    api_key_count = 0
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

    # Check per-IP limit
    client_ip = _get_client_ip(request)
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
    if api_key and api_key_remaining <= ip_remaining and api_key_remaining <= global_remaining:
        return RateLimitResult(
            allowed=True,
            limit=config.api_key_limit,
            remaining=api_key_remaining,
            reset_seconds=api_key_ttl,
            limit_type="api_key",
        )

    if ip_remaining <= global_remaining:
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
        # Load config on each request to support runtime changes
        config = get_rate_limit_config()

        # Skip rate limiting if disabled
        if not config.enabled:
            return await call_next(request)

        # Skip rate limiting for public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Check rate limits
        try:
            result = await check_rate_limits(request, config)
        except RedisUnavailableError:
            # Fail open - allow request through if Redis is unavailable
            return await call_next(request)

        if not result.allowed:
            return _rate_limit_response(result)

        # Proceed with request
        response = await call_next(request)

        # Add rate limit headers to successful responses
        _add_rate_limit_headers(response, result)

        return response

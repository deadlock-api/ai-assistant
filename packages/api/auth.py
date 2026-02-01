# Deadlock AI Assistant - Authentication Middleware
# Supports API key and Cloudflare Turnstile authentication

import hashlib
import os

import httpx
from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from packages.api.errors import ErrorCode, ErrorResponse, get_request_id
from packages.integrations.redis_client import RedisUnavailableError, redis_exists, redis_set

# Paths that bypass authentication
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

# Cloudflare Turnstile verification endpoint
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Cache TTL for verified Turnstile tokens (6 hours in seconds)
TURNSTILE_CACHE_TTL = 6 * 60 * 60

# Redis key prefix for Turnstile token cache
TURNSTILE_CACHE_PREFIX = "turnstile:verified:"


def get_valid_api_keys() -> set[str]:
    """Load valid API keys from environment variable."""
    api_keys_str = os.environ.get("API_KEYS", "")
    if not api_keys_str:
        return set()
    return {key.strip() for key in api_keys_str.split(",") if key.strip()}


def get_turnstile_secret_key() -> str | None:
    """Load Turnstile secret key from environment variable."""
    return os.environ.get("TURNSTILE_SECRET_KEY")


def _get_turnstile_cache_key(token: str) -> str:
    """Generate a Redis cache key for a Turnstile token using SHA-256 hash."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return f"{TURNSTILE_CACHE_PREFIX}{token_hash}"


async def _is_token_cached(token: str) -> bool:
    """Check if a Turnstile token verification is cached in Redis."""
    try:
        cache_key = _get_turnstile_cache_key(token)
        return await redis_exists(cache_key)
    except RedisUnavailableError:
        return False


async def _cache_token(token: str) -> None:
    """Cache a verified Turnstile token in Redis."""
    try:
        cache_key = _get_turnstile_cache_key(token)
        await redis_set(cache_key, "1", ex=TURNSTILE_CACHE_TTL)
    except RedisUnavailableError:
        pass


async def verify_turnstile_token(token: str, secret_key: str) -> bool:
    """Verify a Turnstile token against Cloudflare's siteverify API.

    Turnstile tokens can only be verified once with Cloudflare, so successful
    verifications are cached in Redis for 6 hours.
    """
    # Check if token was already verified
    if await _is_token_cached(token):
        return True

    async with httpx.AsyncClient() as client:
        response = await client.post(
            TURNSTILE_VERIFY_URL,
            data={"secret": secret_key, "response": token},
        )
        if response.status_code != 200:
            return False
        result = response.json()
        is_valid = result.get("success", False)

    # Cache successful verification
    if is_valid:
        await _cache_token(token)

    return is_valid


def _unauthorized_response(message: str = "Invalid or missing API key") -> Response:
    """Create a 401 Unauthorized JSON response with structured error format."""
    error_response = ErrorResponse(
        error=message,
        code=ErrorCode.AUTH_FAILED,
        request_id=get_request_id(),
    )
    return Response(
        content=error_response.model_dump_json(),
        status_code=status.HTTP_401_UNAUTHORIZED,
        media_type="application/json",
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to authenticate requests using API key or Cloudflare Turnstile.

    Authentication methods (checked in order):
    1. X-API-Key header - validated against API_KEYS environment variable
    2. cf-turnstile-response header - validated against Cloudflare siteverify API
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Bypass authentication if DEV_MODE is enabled
        if os.environ.get("DEV_MODE", "false").lower() == "true":
            return await call_next(request)

        # Bypass authentication for public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Try API key authentication first
        api_key = request.headers.get("X-API-Key")
        if api_key:
            valid_keys = get_valid_api_keys()
            if api_key in valid_keys:
                return await call_next(request)
            return _unauthorized_response()

        # Try Turnstile authentication
        turnstile_token = request.headers.get("cf-turnstile-response")
        if turnstile_token:
            secret_key = get_turnstile_secret_key()
            if not secret_key:
                # Turnstile not configured, reject request
                return _unauthorized_response("Turnstile verification failed")

            is_valid = await verify_turnstile_token(turnstile_token, secret_key)
            if is_valid:
                return await call_next(request)
            return _unauthorized_response("Turnstile verification failed")

        # No authentication provided
        return _unauthorized_response()


# Backwards compatibility alias
APIKeyAuthMiddleware = AuthMiddleware

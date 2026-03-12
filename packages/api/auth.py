# Deadlock AI Assistant - Authentication Middleware
# Supports API key, Patreon OAuth2, and Cloudflare Turnstile authentication

import hashlib
import logging
import os

import httpx
from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from packages.api.errors import ErrorCode, ErrorResponse, get_request_id
from packages.api.patreon import (
    PatreonAPIError,
    check_and_refresh_patron_status,
    check_and_refresh_token,
    get_patreon_session,
)
from packages.integrations.redis_client import RedisUnavailableError, redis_exists, redis_set

logger = logging.getLogger("deadlock_assistant")

# Paths that bypass authentication
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


def _unauthorized_response_with_code(message: str, error_code: str) -> Response:
    """Create a 401 Unauthorized JSON response with a custom error code.

    Used for specific error conditions like TOKEN_REFRESH_FAILED that clients
    may need to handle differently (e.g., triggering re-authentication).
    """
    # Include the error code in the response body for client identification
    import json

    response_body = {
        "error": message,
        "code": error_code,
        "request_id": get_request_id(),
    }
    return Response(
        content=json.dumps(response_body),
        status_code=status.HTTP_401_UNAUTHORIZED,
        media_type="application/json",
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to authenticate requests using API key, Patreon OAuth2, or Cloudflare Turnstile.

    Authentication methods (checked in order):
    1. X-API-Key header - validated against API_KEYS environment variable
    2. X-Patreon-Token header - validated against Redis session store
    3. cf-turnstile-response header - validated against Cloudflare siteverify API

    Patreon authentication attaches patron tier info to request.state:
    - patron_user_id: Patreon user ID
    - patron_tier: Tier level (0-3)
    - patron_tier_name: Human-readable tier name
    - patron_rate_limit: Requests per minute for this tier
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
            logger.warning("Auth failed: invalid API key", extra={"path": request.url.path})
            return _unauthorized_response()

        # Try Patreon token authentication
        patreon_token = request.headers.get("X-Patreon-Token")
        if patreon_token:
            try:
                session = await get_patreon_session(patreon_token)
            except RedisUnavailableError:
                return _unauthorized_response("Patreon authentication temporarily unavailable")

            if session is None:
                logger.warning("Auth failed: invalid Patreon session", extra={"path": request.url.path})
                return _unauthorized_response("Invalid or expired Patreon session")

            # Check if access token is expiring soon and refresh if needed
            try:
                session = await check_and_refresh_token(patreon_token, session)
            except PatreonAPIError as e:
                logger.warning("Patreon token refresh failed", extra={"error": str(e), "code": e.code})
                # Token refresh failed, session was deleted
                return _unauthorized_response_with_code(
                    "Patreon token refresh failed. Please re-authenticate.",
                    e.code or "TOKEN_REFRESH_FAILED",
                )
            except RedisUnavailableError:
                # Redis unavailable during refresh, continue with potentially stale token
                pass

            # Check if patron status needs refresh (background, non-blocking on failure)
            # This updates the tier if the patron's subscription has changed
            session = await check_and_refresh_patron_status(patreon_token, session)

            # Attach patron tier info to request state
            request.state.patron_user_id = session.user_id
            request.state.patron_tier_id = session.tier_id
            request.state.patron_tier_name = session.tier_name
            request.state.patron_rate_limit = session.rate_limit

            return await call_next(request)

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
            logger.warning("Auth failed: Turnstile verification rejected", extra={"path": request.url.path})
            return _unauthorized_response("Turnstile verification failed")

        # No authentication provided
        logger.debug("Auth failed: no credentials provided", extra={"path": request.url.path})
        return _unauthorized_response()


# Backwards compatibility alias
APIKeyAuthMiddleware = AuthMiddleware

# Deadlock AI Assistant - Patreon OAuth2 Authentication Routes
#
# Provides OAuth2 endpoints for Patreon authentication:
# - GET /auth/patreon: Redirect to Patreon authorization
# - GET /auth/patreon/callback: Handle OAuth2 callback (US-005)
# - POST /auth/patreon/logout: End authenticated session (US-011)
# - GET /auth/patreon/status: Get current patron status (US-012)

from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from starlette.responses import RedirectResponse

from packages.api.patreon import (
    PatreonAPIError,
    PatreonSession,
    create_patreon_session,
    delete_patreon_session,
    determine_patron_tier,
    exchange_code_for_tokens,
    fetch_user_identity,
    get_authorization_redirect,
    get_patreon_session,
    is_patreon_enabled,
    validate_oauth_state,
)

router = APIRouter(prefix="/auth/patreon", tags=["auth"])


class CallbackResponse(BaseModel):
    """Response from OAuth2 callback endpoint."""

    session_token: str
    tier_id: str | None
    tier_name: str
    rate_limit: int


class LogoutResponse(BaseModel):
    """Response from logout endpoint."""

    message: str


class StatusResponse(BaseModel):
    """Response from patron status endpoint."""

    authenticated: bool
    tier_id: str | None
    tier_name: str
    rate_limit: int
    email: str  # Masked email (e.g., u***@example.com)
    expires_at: str  # ISO format timestamp


@router.get("")
async def patreon_auth_redirect() -> RedirectResponse:
    """Initiate Patreon OAuth2 authorization flow.

    Redirects the user to Patreon's authorization page where they can
    grant access to their identity and email. After authorization,
    Patreon redirects back to /auth/patreon/callback with an
    authorization code.

    The endpoint generates a UUID state parameter stored in Redis
    with a 5-minute TTL to prevent CSRF attacks.

    Returns:
        RedirectResponse to Patreon OAuth2 authorization URL.

    Raises:
        HTTPException 503: If Patreon auth is not enabled or Redis unavailable.
    """
    if not is_patreon_enabled():
        raise HTTPException(status_code=503, detail="Patreon authentication is not configured")

    authorization_url, _state = await get_authorization_redirect()
    return RedirectResponse(url=authorization_url, status_code=302)


@router.get("/callback")
async def patreon_auth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> CallbackResponse:
    """Handle Patreon OAuth2 callback.

    Processes the OAuth2 callback from Patreon after user authorization.
    Validates the state parameter, exchanges the authorization code for
    tokens, fetches user identity and membership data, and creates a
    session.

    Args:
        code: Authorization code from Patreon (on success).
        state: State parameter for CSRF validation.
        error: Error code from Patreon (on failure).
        error_description: Human-readable error description.

    Returns:
        CallbackResponse with session_token, tier, tier_name, and rate_limit.

    Raises:
        HTTPException 400: If state is invalid/expired, code is missing,
            or token exchange fails.
        HTTPException 503: If Patreon auth is not enabled.
    """
    if not is_patreon_enabled():
        raise HTTPException(status_code=503, detail="Patreon authentication is not configured")

    # Handle error response from Patreon
    if error:
        detail = error_description or error
        raise HTTPException(status_code=400, detail=f"Patreon authorization failed: {detail}")

    # Validate required parameters
    if not state:
        raise HTTPException(status_code=400, detail="Missing state parameter")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    # Validate state (CSRF protection) - consumes the state
    state_valid = await validate_oauth_state(state)
    if not state_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")

    # Exchange authorization code for tokens
    try:
        tokens = await exchange_code_for_tokens(code)
    except PatreonAPIError as e:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {e}") from e

    # Fetch user identity and membership data
    try:
        user_data = await fetch_user_identity(tokens.access_token)
    except PatreonAPIError as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch user data: {e}") from e

    # Determine patron tier
    tier_result = determine_patron_tier(user_data.membership_data)

    # Create session
    now = datetime.now(UTC)
    session = PatreonSession(
        user_id=user_data.user_id,
        email=user_data.email,
        tier_id=tier_result.tier_id,
        tier_name=tier_result.tier_name,
        rate_limit=tier_result.rate_limit,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        access_token_expires_at=tokens.expires_at,
        last_verified_at=now,
    )
    session_token = await create_patreon_session(session)

    return CallbackResponse(
        session_token=session_token,
        tier_id=tier_result.tier_id,
        tier_name=tier_result.tier_name,
        rate_limit=tier_result.rate_limit,
    )


@router.post("/logout")
async def patreon_logout(
    x_patreon_token: str | None = Header(default=None),
) -> LogoutResponse:
    """End an authenticated Patreon session.

    Deletes the session from Redis, effectively logging out the user.
    The session token can no longer be used for authentication after this.

    Args:
        x_patreon_token: The session token from the X-Patreon-Token header.

    Returns:
        LogoutResponse with success message.

    Raises:
        HTTPException 401: If token is missing or invalid.
    """
    if not x_patreon_token:
        raise HTTPException(status_code=401, detail="Missing X-Patreon-Token header")

    # Verify the session exists before deleting
    session = await get_patreon_session(x_patreon_token)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

    # Delete the session from Redis
    await delete_patreon_session(x_patreon_token)

    return LogoutResponse(message="Successfully logged out")


def _mask_email(email: str) -> str:
    """Mask an email address for privacy.

    Masks the local part of the email, showing only the first character
    followed by asterisks.

    Examples:
        user@example.com -> u***@example.com
        ab@test.org -> a***@test.org
        a@domain.com -> a***@domain.com

    Args:
        email: The email address to mask.

    Returns:
        The masked email address.
    """
    if not email or "@" not in email:
        return "***@***"

    local, domain = email.split("@", 1)
    if not local:
        return f"***@{domain}"

    # Show only the first character of the local part
    return f"{local[0]}***@{domain}"


@router.get("/status")
async def patreon_status(
    x_patreon_token: str | None = Header(default=None),
) -> StatusResponse:
    """Get the current patron status for an authenticated session.

    Returns information about the authenticated patron's tier, rate limits,
    and session expiration. The email is masked for privacy.

    Args:
        x_patreon_token: The session token from the X-Patreon-Token header.

    Returns:
        StatusResponse with authenticated status, tier info, masked email,
        and session expiration.

    Raises:
        HTTPException 401: If token is missing or invalid.
    """
    if not x_patreon_token:
        raise HTTPException(status_code=401, detail="Missing X-Patreon-Token header")

    # Verify the session exists
    session = await get_patreon_session(x_patreon_token)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

    # Calculate session expiration (7 days from last activity)
    # The session TTL is refreshed on certain operations, but we base it on
    # the stored data for display purposes
    from datetime import timedelta

    from packages.api.patreon import SESSION_TTL_SECONDS

    expires_at = session.last_verified_at + timedelta(seconds=SESSION_TTL_SECONDS)

    return StatusResponse(
        authenticated=True,
        tier_id=session.tier_id,
        tier_name=session.tier_name,
        rate_limit=session.rate_limit,
        email=_mask_email(session.email),
        expires_at=expires_at.isoformat(),
    )

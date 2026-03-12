# Deadlock AI Assistant - Patreon OAuth2 Configuration
#
# Provides configuration for Patreon OAuth2 authentication and patron tier mapping.
# Patrons receive higher API rate limits based on their subscription tier.

import json
import os
import platform
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from packages.integrations.redis_client import (
    redis_delete,
    redis_expire,
    redis_get,
    redis_set,
)


class PatreonConfigError(Exception):
    """Raised when Patreon configuration is invalid or incomplete."""


class PatreonSessionError(Exception):
    """Raised when a Patreon session operation fails."""


class PatreonAPIError(Exception):
    """Raised when a Patreon API call fails."""

    def __init__(self, message: str, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code  # Error code for client identification (e.g., TOKEN_REFRESH_FAILED)


@dataclass
class PatreonSession:
    """Patreon session data stored in Redis.

    Stores all information needed to authenticate and rate limit
    requests from a patron.
    """

    user_id: str
    email: str
    tier_id: str | None  # Patreon tier ID (None for non-patrons)
    tier_name: str
    rate_limit: int
    access_token: str
    refresh_token: str
    access_token_expires_at: datetime
    last_verified_at: datetime

    def to_dict(self) -> dict:
        """Convert session to a dictionary for JSON serialization."""
        return {
            "user_id": self.user_id,
            "email": self.email,
            "tier_id": self.tier_id,
            "tier_name": self.tier_name,
            "rate_limit": self.rate_limit,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_token_expires_at": self.access_token_expires_at.isoformat(),
            "last_verified_at": self.last_verified_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> PatreonSession:
        """Create a session from a dictionary (JSON deserialization)."""
        return cls(
            user_id=data["user_id"],
            email=data["email"],
            tier_id=data.get("tier_id"),  # Use .get() for backwards compatibility
            tier_name=data["tier_name"],
            rate_limit=data["rate_limit"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            access_token_expires_at=datetime.fromisoformat(data["access_token_expires_at"]),
            last_verified_at=datetime.fromisoformat(data["last_verified_at"]),
        )


@dataclass
class PatreonTierConfig:
    """Configuration for a single patron tier."""

    tier_id: str  # Patreon membership/tier ID
    name: str  # Human-readable tier name
    rate_limit: int  # Requests per minute for this tier


@dataclass
class PatronTierResult:
    """Result of determining a patron's tier from membership data."""

    tier_id: str | None  # Patreon tier ID (None for non-patrons)
    tier_name: str  # Human-readable tier name
    rate_limit: int  # Requests per minute for this tier


@dataclass
class PatreonTokenResponse:
    """Response from Patreon OAuth2 token exchange."""

    access_token: str
    refresh_token: str
    expires_at: datetime  # When the access token expires
    token_type: str
    scope: str


@dataclass
class PatreonUserData:
    """User identity and membership data from Patreon API."""

    user_id: str
    email: str
    membership_data: dict | None  # Contains pledge amount if patron


@dataclass
class PatreonConfig:
    """Configuration for Patreon OAuth2 authentication."""

    client_id: str
    client_secret: str
    redirect_uri: str
    campaign_id: str


# Hardcoded tier configuration based on Patreon membership IDs
# Rate limits follow ~10 requests per euro per month
# Ordered from lowest to highest tier
PATREON_TIERS: list[PatreonTierConfig] = [
    PatreonTierConfig(tier_id="23783537", name="VIP Hexe", rate_limit=30),  # €3/month
    PatreonTierConfig(tier_id="24044667", name="Super VIP", rate_limit=70),  # €7/month
    PatreonTierConfig(tier_id="23883135", name="Supporter Hexe", rate_limit=150),  # €15/month
    PatreonTierConfig(tier_id="26319827", name="Deadlock API Support", rate_limit=200),  # €20/month
    PatreonTierConfig(tier_id="24258871", name="Deadlock API Support", rate_limit=500),  # €50/month
    PatreonTierConfig(tier_id="24339295", name="Deadlock API Support", rate_limit=1000),  # €100/month
    PatreonTierConfig(tier_id="26672299", name="Deadlock API Support", rate_limit=2000),  # €200/month
]

# Build lookup dict for O(1) tier lookup by ID
TIER_BY_ID: dict[str, PatreonTierConfig] = {tier.tier_id: tier for tier in PATREON_TIERS}

# Default rate limit for non-patrons
DEFAULT_NON_PATRON_RATE_LIMIT = 100
DEFAULT_NON_PATRON_TIER_NAME = "Non-Patron"

# Cached configuration (loaded once at startup)
_cached_config: PatreonConfig | None = None


def _parse_positive_int(value: str, name: str) -> int:
    """Parse a string as a positive integer.

    Args:
        value: The string value to parse.
        name: The name of the configuration for error messages.

    Returns:
        The parsed positive integer.

    Raises:
        PatreonConfigError: If the value is not a positive integer.
    """
    try:
        result = int(value)
    except ValueError as err:
        raise PatreonConfigError(f"{name} must be an integer, got: {value!r}") from err

    if result <= 0:
        raise PatreonConfigError(f"{name} must be a positive integer (> 0), got: {result}")

    return result


def _load_patreon_config() -> PatreonConfig:
    """Load and validate Patreon configuration from environment variables.

    Required environment variables (when Patreon auth is enabled):
        PATREON_CLIENT_ID: OAuth2 client ID from Patreon developer portal
        PATREON_CLIENT_SECRET: OAuth2 client secret from Patreon developer portal
        PATREON_REDIRECT_URI: OAuth2 callback URL (e.g., https://api.example.com/auth/patreon/callback)
        PATREON_CAMPAIGN_ID: Your Patreon campaign ID

    Returns:
        Validated PatreonConfig instance.

    Raises:
        PatreonConfigError: If any required configuration is missing or invalid.
    """
    # Load required OAuth2 credentials
    client_id = os.environ.get("PATREON_CLIENT_ID", "")
    client_secret = os.environ.get("PATREON_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("PATREON_REDIRECT_URI", "")
    campaign_id = os.environ.get("PATREON_CAMPAIGN_ID", "")

    # Validate required values
    missing = []
    if not client_id:
        missing.append("PATREON_CLIENT_ID")
    if not client_secret:
        missing.append("PATREON_CLIENT_SECRET")
    if not redirect_uri:
        missing.append("PATREON_REDIRECT_URI")
    if not campaign_id:
        missing.append("PATREON_CAMPAIGN_ID")

    if missing:
        raise PatreonConfigError(f"Missing required Patreon configuration: {', '.join(missing)}")

    return PatreonConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        campaign_id=campaign_id,
    )


def is_patreon_enabled() -> bool:
    """Check if Patreon authentication is enabled.

    Patreon auth is considered enabled if PATREON_CLIENT_ID is set.

    Returns:
        True if Patreon auth is enabled, False otherwise.
    """
    return bool(os.environ.get("PATREON_CLIENT_ID", ""))


def get_patreon_config() -> PatreonConfig:
    """Get the cached Patreon configuration.

    Configuration is loaded once from environment variables on first access
    and cached for subsequent calls. Use reload_patreon_config() to
    force a reload.

    Returns:
        The cached PatreonConfig instance.

    Raises:
        PatreonConfigError: If any required configuration is missing or invalid.
    """
    global _cached_config
    if _cached_config is None:
        _cached_config = _load_patreon_config()
    return _cached_config


def reload_patreon_config() -> PatreonConfig:
    """Force reload of Patreon configuration from environment variables.

    Clears the cached configuration and reloads from environment variables.
    Useful for testing or when environment variables have changed.

    Returns:
        The newly loaded PatreonConfig instance.

    Raises:
        PatreonConfigError: If any required configuration is missing or invalid.
    """
    global _cached_config
    _cached_config = None
    return get_patreon_config()


def determine_patron_tier(
    membership_data: dict | None,
) -> PatronTierResult:
    """Determine a patron's tier level from Patreon membership data.

    Looks up the patron's tier by their entitled tier IDs and returns
    the corresponding rate limit. Tier configuration is hardcoded based
    on Patreon membership IDs.

    Args:
        membership_data: Patreon membership data dict containing tier info.
            Expected structure: {"entitled_tier_ids": ["tier_id", ...], ...}
            Can be None for non-patrons or missing data.

    Returns:
        PatronTierResult with tier_id, tier_name, and rate_limit.
    """
    # Non-patron: no membership data
    if membership_data is None:
        return PatronTierResult(
            tier_id=None,
            tier_name=DEFAULT_NON_PATRON_TIER_NAME,
            rate_limit=DEFAULT_NON_PATRON_RATE_LIMIT,
        )

    # Get entitled tier IDs from membership data
    entitled_tier_ids = membership_data.get("entitled_tier_ids", [])
    if not entitled_tier_ids:
        return PatronTierResult(
            tier_id=None,
            tier_name=DEFAULT_NON_PATRON_TIER_NAME,
            rate_limit=DEFAULT_NON_PATRON_RATE_LIMIT,
        )

    # Find the highest tier the patron is entitled to
    # (in case they have multiple, pick the one with the highest rate limit)
    best_tier: PatreonTierConfig | None = None
    for tier_id in entitled_tier_ids:
        tier = TIER_BY_ID.get(tier_id)
        if tier is not None and (best_tier is None or tier.rate_limit > best_tier.rate_limit):
            best_tier = tier

    if best_tier is None:
        # Patron has a tier but it's not one we recognize
        return PatronTierResult(
            tier_id=None,
            tier_name=DEFAULT_NON_PATRON_TIER_NAME,
            rate_limit=DEFAULT_NON_PATRON_RATE_LIMIT,
        )

    return PatronTierResult(
        tier_id=best_tier.tier_id,
        tier_name=best_tier.name,
        rate_limit=best_tier.rate_limit,
    )


# =============================================================================
# Session Management
# =============================================================================

# Redis key prefix for Patreon sessions
SESSION_KEY_PREFIX = "patreon:session:"

# Session TTL in seconds (7 days)
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60


def _get_session_key(token: str) -> str:
    """Generate Redis key for a session token.

    Args:
        token: The session token (UUID4).

    Returns:
        Redis key in format: patreon:session:{token}
    """
    return f"{SESSION_KEY_PREFIX}{token}"


async def create_patreon_session(session: PatreonSession) -> str:
    """Create a new Patreon session and store it in Redis.

    Generates a secure UUID4 token and stores the session data
    in Redis with a 7-day TTL.

    Args:
        session: The PatreonSession data to store.

    Returns:
        The generated session token (UUID4).

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    token = str(uuid.uuid4())
    key = _get_session_key(token)
    session_json = json.dumps(session.to_dict())
    await redis_set(key, session_json, ex=SESSION_TTL_SECONDS)
    return token


async def get_patreon_session(token: str) -> PatreonSession | None:
    """Retrieve a Patreon session from Redis.

    Args:
        token: The session token to look up.

    Returns:
        The PatreonSession if found, None if not found or expired.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    key = _get_session_key(token)
    session_json = await redis_get(key)

    if session_json is None:
        return None

    try:
        data = json.loads(session_json)
        return PatreonSession.from_dict(data)
    except json.JSONDecodeError, KeyError, ValueError:
        # Invalid session data, treat as not found
        return None


async def update_patreon_session(token: str, session: PatreonSession) -> bool:
    """Update an existing Patreon session in Redis.

    Updates the session data and extends the TTL to 7 days.

    Args:
        token: The session token to update.
        session: The new PatreonSession data.

    Returns:
        True if the session was updated, False if it doesn't exist.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    key = _get_session_key(token)

    # Check if session exists before updating
    existing = await redis_get(key)
    if existing is None:
        return False

    session_json = json.dumps(session.to_dict())
    await redis_set(key, session_json, ex=SESSION_TTL_SECONDS)
    return True


async def delete_patreon_session(token: str) -> bool:
    """Delete a Patreon session from Redis.

    Args:
        token: The session token to delete.

    Returns:
        True if the session was deleted, False if it didn't exist.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    key = _get_session_key(token)
    return await redis_delete(key)


async def extend_session_ttl(token: str) -> bool:
    """Extend the TTL of a Patreon session to 7 days.

    Use this to refresh the session expiration without updating
    any session data.

    Args:
        token: The session token to extend.

    Returns:
        True if the TTL was extended, False if the session doesn't exist.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    key = _get_session_key(token)
    return await redis_expire(key, SESSION_TTL_SECONDS)


# =============================================================================
# OAuth2 State Management
# =============================================================================

# Redis key prefix for OAuth2 state tokens
STATE_KEY_PREFIX = "patreon:state:"

# State TTL in seconds (5 minutes)
STATE_TTL_SECONDS = 5 * 60

# Patreon OAuth2 authorization URL base
PATREON_AUTHORIZE_URL = "https://www.patreon.com/oauth2/authorize"


def _get_state_key(state: str) -> str:
    """Generate Redis key for an OAuth2 state token.

    Args:
        state: The state parameter (UUID4).

    Returns:
        Redis key in format: patreon:state:{state}
    """
    return f"{STATE_KEY_PREFIX}{state}"


async def create_oauth_state() -> str:
    """Create a new OAuth2 state parameter and store it in Redis.

    Generates a secure UUID4 state token and stores it in Redis
    with a 5-minute TTL to prevent CSRF attacks.

    Returns:
        The generated state token (UUID4).

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    state = str(uuid.uuid4())
    key = _get_state_key(state)
    # Store empty value - we only need to verify existence
    await redis_set(key, "1", ex=STATE_TTL_SECONDS)
    return state


async def validate_oauth_state(state: str) -> bool:
    """Validate and consume an OAuth2 state parameter.

    Checks if the state exists in Redis and deletes it to prevent
    replay attacks. State tokens can only be used once.

    Args:
        state: The state parameter to validate.

    Returns:
        True if the state was valid, False if invalid or expired.

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    key = _get_state_key(state)
    # Delete returns True if key existed
    return await redis_delete(key)


def build_authorization_url(config: PatreonConfig | None = None) -> str:
    """Build the Patreon OAuth2 authorization URL (without state parameter).

    Constructs the base authorization URL with the required parameters
    for initiating the OAuth2 flow. The state parameter should be
    appended separately after creating it via create_oauth_state().

    Args:
        config: Optional PatreonConfig instance. If not provided, uses the
            cached configuration from get_patreon_config().

    Returns:
        The base authorization URL without state parameter.

    Note:
        Use get_authorization_redirect() for a complete URL with state.
    """
    if config is None:
        config = get_patreon_config()

    # Required scopes: identity for user info, identity[email] for email
    scopes = "identity identity[email]"

    # Build query parameters
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": scopes,
    }

    # Build URL manually to avoid URL encoding issues
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{PATREON_AUTHORIZE_URL}?{query_string}"


async def get_authorization_redirect(config: PatreonConfig | None = None) -> tuple[str, str]:
    """Get the full authorization URL with state parameter.

    Creates a new state parameter, stores it in Redis, and builds
    the authorization URL. Use this function for the /auth/patreon endpoint.

    Args:
        config: Optional PatreonConfig instance. If not provided, uses the
            cached configuration from get_patreon_config().

    Returns:
        A tuple of (authorization_url, state) where:
        - authorization_url: The full URL to redirect the user to (includes state)
        - state: The generated state parameter (stored in Redis)

    Raises:
        RedisUnavailableError: If Redis is unavailable.
    """
    if config is None:
        config = get_patreon_config()

    # Create and store state parameter
    state = await create_oauth_state()

    # Build base URL
    base_url = build_authorization_url(config)

    # Append state parameter
    authorization_url = f"{base_url}&state={state}"

    return authorization_url, state


# =============================================================================
# Patreon API Functions
# =============================================================================

# Patreon API URLs
PATREON_TOKEN_URL = "https://www.patreon.com/api/oauth2/token"
PATREON_IDENTITY_URL = "https://www.patreon.com/api/oauth2/v2/identity"


def _get_user_agent() -> str:
    """Generate a user agent string for Patreon API requests."""
    return f"DeadlockAIAssistant/1.0, platform {platform.platform()}"


async def exchange_code_for_tokens(
    code: str,
    config: PatreonConfig | None = None,
) -> PatreonTokenResponse:
    """Exchange an authorization code for access and refresh tokens.

    Makes a POST request to Patreon's token endpoint to exchange the
    authorization code received from the OAuth2 callback.

    Args:
        code: The authorization code from the OAuth2 callback.
        config: Optional PatreonConfig instance. If not provided, uses the
            cached configuration from get_patreon_config().

    Returns:
        PatreonTokenResponse with access_token, refresh_token, and expiration.

    Raises:
        PatreonAPIError: If the token exchange fails.
    """
    if config is None:
        config = get_patreon_config()

    params = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "redirect_uri": config.redirect_uri,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            PATREON_TOKEN_URL,
            data=params,
            headers={"User-Agent": _get_user_agent()},
        )

    if response.status_code != 200:
        raise PatreonAPIError(
            f"Token exchange failed: {response.text}",
            status_code=response.status_code,
        )

    data = response.json()

    # Check for error response
    if "error" in data:
        raise PatreonAPIError(
            f"Token exchange failed: {data.get('error_description', data['error'])}",
        )

    # Calculate expiration time
    expires_in = data.get("expires_in", 3600)  # Default 1 hour
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    return PatreonTokenResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=expires_at,
        token_type=data.get("token_type", "Bearer"),
        scope=data.get("scope", ""),
    )


async def fetch_user_identity(
    access_token: str,
    campaign_id: str | None = None,
) -> PatreonUserData:
    """Fetch user identity and membership data from Patreon API.

    Makes a GET request to Patreon's identity endpoint to get the
    authenticated user's info and their membership in the specified campaign.

    Args:
        access_token: The access token for API authentication.
        campaign_id: Optional campaign ID to filter memberships. If not provided,
            uses the campaign_id from the cached configuration.

    Returns:
        PatreonUserData with user_id, email, and membership_data.

    Raises:
        PatreonAPIError: If the API request fails.
    """
    if campaign_id is None:
        config = get_patreon_config()
        campaign_id = config.campaign_id

    # Request user identity with membership data including entitled tiers
    params = {
        "include": "memberships,memberships.campaign,memberships.currently_entitled_tiers",
        "fields[user]": "email",
        "fields[member]": "patron_status",
        "fields[tier]": "title",
        "fields[campaign]": "vanity",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            PATREON_IDENTITY_URL,
            params=params,
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": _get_user_agent(),
            },
        )

    if response.status_code != 200:
        raise PatreonAPIError(
            f"Failed to fetch user identity: {response.text}",
            status_code=response.status_code,
        )

    data = response.json()

    # Check for error response
    if "errors" in data:
        error_msg = data["errors"][0].get("detail", "Unknown error")
        raise PatreonAPIError(f"Failed to fetch user identity: {error_msg}")

    # Extract user data from JSON:API response
    user_data = data.get("data", {})
    user_id = user_data.get("id", "")
    email = user_data.get("attributes", {}).get("email", "")

    # Find membership for our campaign
    membership_data = None
    included = data.get("included", [])

    for item in included:
        if item.get("type") != "member":
            continue

        # Check if this membership is for our campaign
        relationships = item.get("relationships", {})
        campaign_rel = relationships.get("campaign", {}).get("data", {})
        if campaign_rel.get("id") == campaign_id:
            attributes = item.get("attributes", {})
            # Only include active patrons
            if attributes.get("patron_status") == "active_patron":
                # Extract entitled tier IDs from the relationship
                entitled_tiers_rel = relationships.get("currently_entitled_tiers", {}).get("data", [])
                entitled_tier_ids = [tier["id"] for tier in entitled_tiers_rel if tier.get("type") == "tier"]
                membership_data = {
                    "entitled_tier_ids": entitled_tier_ids,
                    "patron_status": attributes.get("patron_status"),
                }
            break

    return PatreonUserData(
        user_id=user_id,
        email=email,
        membership_data=membership_data,
    )


# =============================================================================
# Token Refresh
# =============================================================================

# Time buffer before expiration to trigger refresh (5 minutes)
TOKEN_REFRESH_BUFFER_SECONDS = 5 * 60


async def refresh_access_token(
    refresh_token: str,
    config: PatreonConfig | None = None,
) -> PatreonTokenResponse:
    """Refresh an expired access token using the refresh token.

    Makes a POST request to Patreon's token endpoint with grant_type=refresh_token
    to obtain a new access token.

    Args:
        refresh_token: The refresh token from the original OAuth2 flow.
        config: Optional PatreonConfig instance. If not provided, uses the
            cached configuration from get_patreon_config().

    Returns:
        PatreonTokenResponse with new access_token, refresh_token, and expiration.

    Raises:
        PatreonAPIError: If the token refresh fails.
    """
    if config is None:
        config = get_patreon_config()

    params = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            PATREON_TOKEN_URL,
            data=params,
            headers={"User-Agent": _get_user_agent()},
        )

    if response.status_code != 200:
        raise PatreonAPIError(
            f"Token refresh failed: {response.text}",
            status_code=response.status_code,
            code="TOKEN_REFRESH_FAILED",
        )

    data = response.json()

    # Check for error response
    if "error" in data:
        raise PatreonAPIError(
            f"Token refresh failed: {data.get('error_description', data['error'])}",
            code="TOKEN_REFRESH_FAILED",
        )

    # Calculate expiration time
    expires_in = data.get("expires_in", 3600)  # Default 1 hour
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    return PatreonTokenResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=expires_at,
        token_type=data.get("token_type", "Bearer"),
        scope=data.get("scope", ""),
    )


# =============================================================================
# Patron Status Refresh
# =============================================================================

# Time interval for periodic patron status verification (1 hour)
PATRON_STATUS_REFRESH_INTERVAL_SECONDS = 60 * 60


def is_patron_status_stale(session: PatreonSession) -> bool:
    """Check if the patron status needs to be refreshed.

    Patron status is considered stale if the last verification was more than
    1 hour ago. This is used to trigger background refresh of patron tier.

    Args:
        session: The current PatreonSession.

    Returns:
        True if the patron status should be refreshed, False otherwise.
    """
    now = datetime.now(UTC)
    last_verified = session.last_verified_at

    # Ensure last_verified is timezone-aware for comparison
    if last_verified.tzinfo is None:
        last_verified = last_verified.replace(tzinfo=UTC)

    time_since_verification = (now - last_verified).total_seconds()
    return time_since_verification > PATRON_STATUS_REFRESH_INTERVAL_SECONDS


async def refresh_patron_status(
    token: str,
    session: PatreonSession,
) -> PatreonSession:
    """Refresh the patron's membership status from Patreon API.

    Fetches the current membership data from Patreon and updates the session
    tier if it has changed. If the patron is no longer active, they are
    downgraded to tier 0.

    Args:
        token: The session token (for Redis operations).
        session: The current PatreonSession.

    Returns:
        The updated PatreonSession with refreshed tier info.

    Raises:
        PatreonAPIError: If the Patreon API call fails.
    """
    # Fetch current membership data from Patreon
    user_data = await fetch_user_identity(session.access_token)

    # Determine new tier from membership data
    tier_result = determine_patron_tier(user_data.membership_data)

    # Create updated session with new tier info and verification time
    now = datetime.now(UTC)
    updated_session = PatreonSession(
        user_id=session.user_id,
        email=session.email,
        tier_id=tier_result.tier_id,
        tier_name=tier_result.tier_name,
        rate_limit=tier_result.rate_limit,
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        access_token_expires_at=session.access_token_expires_at,
        last_verified_at=now,
    )

    # Update session in Redis (extends TTL to 7 days)
    await update_patreon_session(token, updated_session)

    return updated_session


async def check_and_refresh_patron_status(
    token: str,
    session: PatreonSession,
) -> PatreonSession:
    """Check if patron status is stale and refresh if needed (non-blocking on failure).

    If the patron status verification is more than 1 hour old, this function
    attempts to refresh it by fetching current membership from Patreon API.
    Failures are logged but don't block the request - the cached tier is used.

    Args:
        token: The session token (for Redis operations).
        session: The current PatreonSession.

    Returns:
        The updated PatreonSession if refresh was successful,
        or the original session if no refresh needed or refresh failed.
    """
    import logging

    logger = logging.getLogger(__name__)

    if not is_patron_status_stale(session):
        # Status is fresh, no refresh needed
        return session

    try:
        return await refresh_patron_status(token, session)
    except PatreonAPIError as e:
        # Log the failure but continue with cached tier
        logger.warning(
            "Failed to refresh patron status for user %s: %s",
            session.user_id,
            str(e),
        )
        return session
    except Exception as e:
        # Catch any unexpected errors to ensure the request isn't blocked
        logger.exception(
            "Unexpected error refreshing patron status for user %s: %s",
            session.user_id,
            str(e),
        )
        return session


async def check_and_refresh_token(
    token: str,
    session: PatreonSession,
) -> PatreonSession:
    """Check if the access token is expiring soon and refresh if needed.

    If the access token expires within 5 minutes, this function will attempt
    to refresh it using the refresh token. On success, the session is updated
    in Redis with the new tokens and extended TTL.

    Args:
        token: The session token (for Redis operations).
        session: The current PatreonSession.

    Returns:
        The updated PatreonSession if refresh was needed and successful,
        or the original session if no refresh was needed.

    Raises:
        PatreonAPIError: If the token refresh fails. The session is deleted
            from Redis before raising. The error will have code="TOKEN_REFRESH_FAILED".
    """
    now = datetime.now(UTC)
    expires_at = session.access_token_expires_at

    # Ensure expires_at is timezone-aware for comparison
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    # Check if token expires within the buffer time
    time_until_expiry = (expires_at - now).total_seconds()
    if time_until_expiry > TOKEN_REFRESH_BUFFER_SECONDS:
        # Token is still valid, no refresh needed
        return session

    # Token is expiring soon, attempt refresh
    try:
        new_tokens = await refresh_access_token(session.refresh_token)

        # Create updated session with new tokens
        updated_session = PatreonSession(
            user_id=session.user_id,
            email=session.email,
            tier_id=session.tier_id,
            tier_name=session.tier_name,
            rate_limit=session.rate_limit,
            access_token=new_tokens.access_token,
            refresh_token=new_tokens.refresh_token,
            access_token_expires_at=new_tokens.expires_at,
            last_verified_at=session.last_verified_at,
        )

        # Update session in Redis (extends TTL to 7 days)
        await update_patreon_session(token, updated_session)

        return updated_session

    except PatreonAPIError:
        # Refresh failed, delete the session
        await delete_patreon_session(token)
        raise

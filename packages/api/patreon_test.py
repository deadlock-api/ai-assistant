# Deadlock AI Assistant - Patreon authentication tests

import json
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest import mock
from unittest.mock import AsyncMock

import httpx
import pytest

from packages.api.patreon import (
    DEFAULT_NON_PATRON_RATE_LIMIT,
    DEFAULT_NON_PATRON_TIER_NAME,
    PATREON_TIERS,
    PATRON_STATUS_REFRESH_INTERVAL_SECONDS,
    SESSION_TTL_SECONDS,
    STATE_TTL_SECONDS,
    TIER_BY_ID,
    PatreonAPIError,
    PatreonConfig,
    PatreonConfigError,
    PatreonSession,
    PatreonTokenResponse,
    PatreonUserData,
    build_authorization_url,
    check_and_refresh_patron_status,
    check_and_refresh_token,
    create_oauth_state,
    create_patreon_session,
    delete_patreon_session,
    determine_patron_tier,
    exchange_code_for_tokens,
    extend_session_ttl,
    fetch_user_identity,
    get_authorization_redirect,
    get_patreon_session,
    is_patreon_enabled,
    is_patron_status_stale,
    refresh_access_token,
    refresh_patron_status,
    reload_patreon_config,
    update_patreon_session,
    validate_oauth_state,
)


@pytest.fixture(autouse=True)
def reset_patreon_config() -> Generator[None]:
    """Reset patreon config cache before each test."""
    # Clear cached config before each test
    import packages.api.patreon as patreon_module

    patreon_module._cached_config = None
    yield
    patreon_module._cached_config = None


def _make_test_config() -> PatreonConfig:
    """Create a test Patreon configuration."""
    return PatreonConfig(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="https://api.example.com/auth/patreon/callback",
        campaign_id="test-campaign-id",
    )


# Test tier for convenience (VIP Hexe - €3/month)
TEST_TIER = PATREON_TIERS[0]


def _make_test_session(
    tier_id: str | None = None,
    access_token_expires_at: datetime | None = None,
    last_verified_at: datetime | None = None,
) -> PatreonSession:
    """Create a test Patreon session."""
    now = datetime.now(UTC)
    if access_token_expires_at is None:
        access_token_expires_at = now + timedelta(hours=1)
    if last_verified_at is None:
        last_verified_at = now

    # Default to test tier if not specified
    if tier_id is None:
        tier_id = TEST_TIER.tier_id

    # Look up tier info
    tier = TIER_BY_ID.get(tier_id) if tier_id else None
    tier_name = tier.name if tier else DEFAULT_NON_PATRON_TIER_NAME
    rate_limit = tier.rate_limit if tier else DEFAULT_NON_PATRON_RATE_LIMIT

    return PatreonSession(
        user_id="123456",
        email="user@example.com",
        tier_id=tier_id,
        tier_name=tier_name,
        rate_limit=rate_limit,
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        access_token_expires_at=access_token_expires_at,
        last_verified_at=last_verified_at,
    )


class TestPatreonConfig:
    """Tests for Patreon configuration loading."""

    def test_is_patreon_enabled_returns_true_when_client_id_set(self) -> None:
        """Test that is_patreon_enabled returns True when PATREON_CLIENT_ID is set."""
        with mock.patch.dict(os.environ, {"PATREON_CLIENT_ID": "test-id"}):
            assert is_patreon_enabled() is True

    def test_is_patreon_enabled_returns_false_when_client_id_not_set(self) -> None:
        """Test that is_patreon_enabled returns False when PATREON_CLIENT_ID is not set."""
        env = os.environ.copy()
        env.pop("PATREON_CLIENT_ID", None)
        with mock.patch.dict(os.environ, env, clear=True):
            assert is_patreon_enabled() is False

    def test_config_loads_from_environment(self) -> None:
        """Test that configuration is loaded from environment variables."""
        env = {
            "PATREON_CLIENT_ID": "client-123",
            "PATREON_CLIENT_SECRET": "secret-456",
            "PATREON_REDIRECT_URI": "https://example.com/callback",
            "PATREON_CAMPAIGN_ID": "campaign-789",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = reload_patreon_config()
            assert config.client_id == "client-123"
            assert config.client_secret == "secret-456"
            assert config.redirect_uri == "https://example.com/callback"
            assert config.campaign_id == "campaign-789"

    def test_config_raises_error_for_missing_required_values(self) -> None:
        """Test that PatreonConfigError is raised for missing required values."""
        env = {"PATREON_CLIENT_ID": "client-123"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(PatreonConfigError) as exc_info:
                reload_patreon_config()
            assert "PATREON_CLIENT_SECRET" in str(exc_info.value)
            assert "PATREON_REDIRECT_URI" in str(exc_info.value)
            assert "PATREON_CAMPAIGN_ID" in str(exc_info.value)


class TestHardcodedTiers:
    """Tests for hardcoded tier configuration."""

    def test_all_tiers_have_unique_ids(self) -> None:
        """Test that all tier IDs are unique."""
        tier_ids = [tier.tier_id for tier in PATREON_TIERS]
        assert len(tier_ids) == len(set(tier_ids))

    def test_tier_by_id_lookup_works(self) -> None:
        """Test that TIER_BY_ID lookup returns correct tiers."""
        for tier in PATREON_TIERS:
            assert TIER_BY_ID[tier.tier_id] is tier

    def test_tiers_ordered_by_rate_limit(self) -> None:
        """Test that tiers are ordered from lowest to highest rate limit."""
        rate_limits = [tier.rate_limit for tier in PATREON_TIERS]
        assert rate_limits == sorted(rate_limits)

    def test_expected_tier_count(self) -> None:
        """Test that we have 7 configured tiers."""
        assert len(PATREON_TIERS) == 7


class TestDeterminePatronTier:
    """Tests for determine_patron_tier function."""

    def test_returns_non_patron_for_none_membership(self) -> None:
        """Test that non-patron tier is returned for None membership data."""
        result = determine_patron_tier(None)
        assert result.tier_id is None
        assert result.tier_name == DEFAULT_NON_PATRON_TIER_NAME
        assert result.rate_limit == DEFAULT_NON_PATRON_RATE_LIMIT

    def test_returns_non_patron_for_empty_membership(self) -> None:
        """Test that non-patron tier is returned for empty membership data."""
        result = determine_patron_tier({})
        assert result.tier_id is None
        assert result.tier_name == DEFAULT_NON_PATRON_TIER_NAME
        assert result.rate_limit == DEFAULT_NON_PATRON_RATE_LIMIT

    def test_returns_non_patron_for_empty_tier_list(self) -> None:
        """Test that non-patron tier is returned when entitled_tier_ids is empty."""
        result = determine_patron_tier({"entitled_tier_ids": []})
        assert result.tier_id is None
        assert result.rate_limit == DEFAULT_NON_PATRON_RATE_LIMIT

    def test_returns_correct_tier_for_known_tier_id(self) -> None:
        """Test that correct tier is returned for a known tier ID."""
        # Test with VIP Hexe tier (€3/month, 30 req)
        tier = PATREON_TIERS[0]
        result = determine_patron_tier({"entitled_tier_ids": [tier.tier_id]})
        assert result.tier_id == tier.tier_id
        assert result.tier_name == tier.name
        assert result.rate_limit == tier.rate_limit

    def test_returns_highest_tier_when_multiple_entitled(self) -> None:
        """Test that highest rate limit tier is returned when multiple are entitled."""
        # Give both lowest and highest tier
        lowest_tier = PATREON_TIERS[0]  # 30 req
        highest_tier = PATREON_TIERS[-1]  # 2000 req
        result = determine_patron_tier({"entitled_tier_ids": [lowest_tier.tier_id, highest_tier.tier_id]})
        assert result.tier_id == highest_tier.tier_id
        assert result.rate_limit == highest_tier.rate_limit

    def test_returns_non_patron_for_unknown_tier_id(self) -> None:
        """Test that non-patron tier is returned for unknown tier ID."""
        result = determine_patron_tier({"entitled_tier_ids": ["unknown-tier-id"]})
        assert result.tier_id is None
        assert result.rate_limit == DEFAULT_NON_PATRON_RATE_LIMIT

    def test_all_hardcoded_tiers_work(self) -> None:
        """Test that all hardcoded tier IDs are recognized."""
        for tier in PATREON_TIERS:
            result = determine_patron_tier({"entitled_tier_ids": [tier.tier_id]})
            assert result.tier_id == tier.tier_id
            assert result.tier_name == tier.name
            assert result.rate_limit == tier.rate_limit


class TestPatreonSession:
    """Tests for PatreonSession dataclass serialization."""

    def test_session_to_dict(self) -> None:
        """Test that PatreonSession serializes to dict correctly."""
        session = _make_test_session()
        data = session.to_dict()

        assert data["user_id"] == "123456"
        assert data["email"] == "user@example.com"
        assert data["tier_id"] == TEST_TIER.tier_id
        assert data["tier_name"] == TEST_TIER.name
        assert data["access_token"] == "test-access-token"
        assert data["refresh_token"] == "test-refresh-token"
        assert "access_token_expires_at" in data
        assert "last_verified_at" in data

    def test_session_from_dict(self) -> None:
        """Test that PatreonSession deserializes from dict correctly."""
        session = _make_test_session()
        data = session.to_dict()
        restored = PatreonSession.from_dict(data)

        assert restored.user_id == session.user_id
        assert restored.email == session.email
        assert restored.tier_id == session.tier_id
        assert restored.tier_name == session.tier_name
        assert restored.rate_limit == session.rate_limit
        assert restored.access_token == session.access_token
        assert restored.refresh_token == session.refresh_token

    def test_session_roundtrip_json(self) -> None:
        """Test that session survives JSON serialization roundtrip."""
        session = _make_test_session()
        json_str = json.dumps(session.to_dict())
        data = json.loads(json_str)
        restored = PatreonSession.from_dict(data)

        assert restored.user_id == session.user_id
        assert restored.tier_id == session.tier_id


class TestSessionManagement:
    """Tests for Patreon session CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_session_returns_uuid_token(self) -> None:
        """Test that create_patreon_session returns a UUID token."""
        session = _make_test_session()

        with mock.patch("packages.api.patreon.redis_set", new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True
            token = await create_patreon_session(session)

            # Verify token is a valid UUID format
            import uuid

            uuid.UUID(token)  # Will raise if not valid UUID

            # Verify Redis was called correctly
            mock_set.assert_called_once()
            call_args = mock_set.call_args
            assert call_args[0][0] == f"patreon:session:{token}"
            assert call_args[1]["ex"] == SESSION_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_get_session_returns_session_when_found(self) -> None:
        """Test that get_patreon_session returns session when it exists."""
        session = _make_test_session()
        session_json = json.dumps(session.to_dict())

        with mock.patch("packages.api.patreon.redis_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = session_json
            result = await get_patreon_session("test-token")

            assert result is not None
            assert result.user_id == session.user_id
            assert result.tier_id == session.tier_id
            mock_get.assert_called_once_with("patreon:session:test-token")

    @pytest.mark.asyncio
    async def test_get_session_returns_none_when_not_found(self) -> None:
        """Test that get_patreon_session returns None when session doesn't exist."""
        with mock.patch("packages.api.patreon.redis_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            result = await get_patreon_session("nonexistent-token")

            assert result is None

    @pytest.mark.asyncio
    async def test_get_session_returns_none_for_invalid_json(self) -> None:
        """Test that get_patreon_session returns None for corrupted session data."""
        with mock.patch("packages.api.patreon.redis_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = "not-valid-json"
            result = await get_patreon_session("test-token")

            assert result is None

    @pytest.mark.asyncio
    async def test_update_session_returns_true_when_exists(self) -> None:
        """Test that update_patreon_session returns True when session exists."""
        session = _make_test_session()
        session_json = json.dumps(session.to_dict())

        with (
            mock.patch("packages.api.patreon.redis_get", new_callable=AsyncMock) as mock_get,
            mock.patch("packages.api.patreon.redis_set", new_callable=AsyncMock) as mock_set,
        ):
            mock_get.return_value = session_json
            mock_set.return_value = True

            result = await update_patreon_session("test-token", session)

            assert result is True
            mock_set.assert_called_once()
            assert mock_set.call_args[1]["ex"] == SESSION_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_update_session_returns_false_when_not_exists(self) -> None:
        """Test that update_patreon_session returns False when session doesn't exist."""
        session = _make_test_session()

        with mock.patch("packages.api.patreon.redis_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            result = await update_patreon_session("nonexistent-token", session)

            assert result is False

    @pytest.mark.asyncio
    async def test_delete_session_returns_true_when_deleted(self) -> None:
        """Test that delete_patreon_session returns True when session is deleted."""
        with mock.patch("packages.api.patreon.redis_delete", new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = True
            result = await delete_patreon_session("test-token")

            assert result is True
            mock_delete.assert_called_once_with("patreon:session:test-token")

    @pytest.mark.asyncio
    async def test_delete_session_returns_false_when_not_exists(self) -> None:
        """Test that delete_patreon_session returns False when session doesn't exist."""
        with mock.patch("packages.api.patreon.redis_delete", new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = False
            result = await delete_patreon_session("nonexistent-token")

            assert result is False

    @pytest.mark.asyncio
    async def test_extend_session_ttl_calls_redis_expire(self) -> None:
        """Test that extend_session_ttl calls redis_expire with correct TTL."""
        with mock.patch("packages.api.patreon.redis_expire", new_callable=AsyncMock) as mock_expire:
            mock_expire.return_value = True
            result = await extend_session_ttl("test-token")

            assert result is True
            mock_expire.assert_called_once_with("patreon:session:test-token", SESSION_TTL_SECONDS)


class TestOAuth2State:
    """Tests for OAuth2 state management."""

    @pytest.mark.asyncio
    async def test_create_oauth_state_stores_in_redis(self) -> None:
        """Test that create_oauth_state stores state in Redis with correct TTL."""
        with mock.patch("packages.api.patreon.redis_set", new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True
            state = await create_oauth_state()

            # Verify state is a valid UUID
            import uuid

            uuid.UUID(state)

            # Verify Redis was called correctly
            mock_set.assert_called_once()
            call_args = mock_set.call_args
            assert call_args[0][0] == f"patreon:state:{state}"
            assert call_args[0][1] == "1"
            assert call_args[1]["ex"] == STATE_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_validate_oauth_state_returns_true_and_deletes_when_valid(self) -> None:
        """Test that validate_oauth_state returns True and deletes state when valid."""
        with mock.patch("packages.api.patreon.redis_delete", new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = True
            result = await validate_oauth_state("valid-state")

            assert result is True
            mock_delete.assert_called_once_with("patreon:state:valid-state")

    @pytest.mark.asyncio
    async def test_validate_oauth_state_returns_false_when_invalid(self) -> None:
        """Test that validate_oauth_state returns False when state doesn't exist."""
        with mock.patch("packages.api.patreon.redis_delete", new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = False
            result = await validate_oauth_state("invalid-state")

            assert result is False


class TestAuthorizationURL:
    """Tests for OAuth2 authorization URL generation."""

    def test_build_authorization_url_includes_required_params(self) -> None:
        """Test that authorization URL includes all required parameters."""
        config = _make_test_config()
        url = build_authorization_url(config)

        assert "response_type=code" in url
        assert f"client_id={config.client_id}" in url
        assert f"redirect_uri={config.redirect_uri}" in url
        assert "scope=identity" in url
        assert "identity[email]" in url
        assert url.startswith("https://www.patreon.com/oauth2/authorize")

    @pytest.mark.asyncio
    async def test_get_authorization_redirect_creates_state(self) -> None:
        """Test that get_authorization_redirect creates and includes state."""
        config = _make_test_config()

        with mock.patch("packages.api.patreon.create_oauth_state", new_callable=AsyncMock) as mock_state:
            mock_state.return_value = "test-state-uuid"
            url, state = await get_authorization_redirect(config)

            assert state == "test-state-uuid"
            assert f"state={state}" in url


class TestTokenExchange:
    """Tests for OAuth2 token exchange."""

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_success(self) -> None:
        """Test successful token exchange."""
        config = _make_test_config()
        mock_response = httpx.Response(
            200,
            json={
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "identity identity[email]",
            },
        )

        with mock.patch("packages.api.patreon.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await exchange_code_for_tokens("auth-code", config)

            assert result.access_token == "new-access-token"
            assert result.refresh_token == "new-refresh-token"
            assert result.token_type == "Bearer"
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_failure_status(self) -> None:
        """Test token exchange failure with non-200 status."""
        config = _make_test_config()
        mock_response = httpx.Response(400, text="Invalid code")

        with mock.patch("packages.api.patreon.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(PatreonAPIError) as exc_info:
                await exchange_code_for_tokens("invalid-code", config)

            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_failure_error_response(self) -> None:
        """Test token exchange failure with error in response body."""
        config = _make_test_config()
        mock_response = httpx.Response(
            200,
            json={"error": "invalid_grant", "error_description": "Authorization code expired"},
        )

        with mock.patch("packages.api.patreon.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(PatreonAPIError) as exc_info:
                await exchange_code_for_tokens("expired-code", config)

            assert "Authorization code expired" in str(exc_info.value)


class TestFetchUserIdentity:
    """Tests for fetching user identity from Patreon API."""

    @pytest.mark.asyncio
    async def test_fetch_user_identity_success_with_active_patron(self) -> None:
        """Test fetching user identity with active patron membership."""
        tier = PATREON_TIERS[0]  # VIP Hexe
        mock_response = httpx.Response(
            200,
            json={
                "data": {"id": "12345", "attributes": {"email": "patron@example.com"}},
                "included": [
                    {
                        "type": "member",
                        "attributes": {
                            "patron_status": "active_patron",
                        },
                        "relationships": {
                            "campaign": {"data": {"id": "test-campaign-id"}},
                            "currently_entitled_tiers": {
                                "data": [{"type": "tier", "id": tier.tier_id}]
                            },
                        },
                    }
                ],
            },
        )

        with (
            mock.patch("packages.api.patreon.httpx.AsyncClient") as mock_client_cls,
            mock.patch("packages.api.patreon.get_patreon_config") as mock_config,
        ):
            mock_config.return_value = _make_test_config()
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await fetch_user_identity("access-token")

            assert result.user_id == "12345"
            assert result.email == "patron@example.com"
            assert result.membership_data is not None
            assert result.membership_data["entitled_tier_ids"] == [tier.tier_id]

    @pytest.mark.asyncio
    async def test_fetch_user_identity_with_inactive_patron(self) -> None:
        """Test fetching user identity with inactive patron status."""
        mock_response = httpx.Response(
            200,
            json={
                "data": {"id": "12345", "attributes": {"email": "former@example.com"}},
                "included": [
                    {
                        "type": "member",
                        "attributes": {
                            "patron_status": "former_patron",
                        },
                        "relationships": {
                            "campaign": {"data": {"id": "test-campaign-id"}},
                            "currently_entitled_tiers": {"data": []},
                        },
                    }
                ],
            },
        )

        with (
            mock.patch("packages.api.patreon.httpx.AsyncClient") as mock_client_cls,
            mock.patch("packages.api.patreon.get_patreon_config") as mock_config,
        ):
            mock_config.return_value = _make_test_config()
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await fetch_user_identity("access-token")

            # Inactive patrons should have None membership_data
            assert result.membership_data is None

    @pytest.mark.asyncio
    async def test_fetch_user_identity_without_membership(self) -> None:
        """Test fetching user identity for non-patron user."""
        mock_response = httpx.Response(
            200,
            json={
                "data": {"id": "12345", "attributes": {"email": "user@example.com"}},
                "included": [],
            },
        )

        with (
            mock.patch("packages.api.patreon.httpx.AsyncClient") as mock_client_cls,
            mock.patch("packages.api.patreon.get_patreon_config") as mock_config,
        ):
            mock_config.return_value = _make_test_config()
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await fetch_user_identity("access-token")

            assert result.membership_data is None

    @pytest.mark.asyncio
    async def test_fetch_user_identity_api_error(self) -> None:
        """Test fetching user identity with API error."""
        mock_response = httpx.Response(401, text="Unauthorized")

        with (
            mock.patch("packages.api.patreon.httpx.AsyncClient") as mock_client_cls,
            mock.patch("packages.api.patreon.get_patreon_config") as mock_config,
        ):
            mock_config.return_value = _make_test_config()
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(PatreonAPIError) as exc_info:
                await fetch_user_identity("invalid-token")

            assert exc_info.value.status_code == 401


class TestTokenRefresh:
    """Tests for automatic token refresh."""

    @pytest.mark.asyncio
    async def test_refresh_access_token_success(self) -> None:
        """Test successful token refresh."""
        config = _make_test_config()
        mock_response = httpx.Response(
            200,
            json={
                "access_token": "refreshed-access-token",
                "refresh_token": "refreshed-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "identity identity[email]",
            },
        )

        with mock.patch("packages.api.patreon.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await refresh_access_token("old-refresh-token", config)

            assert result.access_token == "refreshed-access-token"
            assert result.refresh_token == "refreshed-refresh-token"

    @pytest.mark.asyncio
    async def test_refresh_access_token_failure(self) -> None:
        """Test token refresh failure."""
        config = _make_test_config()
        mock_response = httpx.Response(400, text="Invalid refresh token")

        with mock.patch("packages.api.patreon.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(PatreonAPIError) as exc_info:
                await refresh_access_token("invalid-refresh-token", config)

            assert exc_info.value.code == "TOKEN_REFRESH_FAILED"

    @pytest.mark.asyncio
    async def test_check_and_refresh_token_no_refresh_needed(self) -> None:
        """Test check_and_refresh_token when token is not expiring soon."""
        now = datetime.now(UTC)
        session = _make_test_session(access_token_expires_at=now + timedelta(hours=2))

        # Token expires in 2 hours, no refresh needed
        result = await check_and_refresh_token("test-token", session)

        assert result is session  # Same session returned

    @pytest.mark.asyncio
    async def test_check_and_refresh_token_refreshes_when_expiring_soon(self) -> None:
        """Test check_and_refresh_token refreshes when token expires within buffer."""
        now = datetime.now(UTC)
        session = _make_test_session(access_token_expires_at=now + timedelta(minutes=3))

        mock_token_response = PatreonTokenResponse(
            access_token="new-access-token",
            refresh_token="new-refresh-token",
            expires_at=now + timedelta(hours=1),
            token_type="Bearer",
            scope="identity identity[email]",
        )

        with (
            mock.patch("packages.api.patreon.refresh_access_token", new_callable=AsyncMock) as mock_refresh,
            mock.patch("packages.api.patreon.update_patreon_session", new_callable=AsyncMock) as mock_update,
        ):
            mock_refresh.return_value = mock_token_response
            mock_update.return_value = True

            result = await check_and_refresh_token("test-token", session)

            assert result.access_token == "new-access-token"
            mock_refresh.assert_called_once_with(session.refresh_token)
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_and_refresh_token_deletes_session_on_failure(self) -> None:
        """Test check_and_refresh_token deletes session when refresh fails."""
        now = datetime.now(UTC)
        session = _make_test_session(access_token_expires_at=now + timedelta(minutes=1))

        with (
            mock.patch("packages.api.patreon.refresh_access_token", new_callable=AsyncMock) as mock_refresh,
            mock.patch("packages.api.patreon.delete_patreon_session", new_callable=AsyncMock) as mock_delete,
        ):
            mock_refresh.side_effect = PatreonAPIError("Refresh failed", code="TOKEN_REFRESH_FAILED")
            mock_delete.return_value = True

            with pytest.raises(PatreonAPIError) as exc_info:
                await check_and_refresh_token("test-token", session)

            assert exc_info.value.code == "TOKEN_REFRESH_FAILED"
            mock_delete.assert_called_once_with("test-token")


class TestPatronStatusRefresh:
    """Tests for periodic patron status refresh."""

    def test_is_patron_status_stale_returns_false_when_fresh(self) -> None:
        """Test is_patron_status_stale returns False for recent verification."""
        now = datetime.now(UTC)
        session = _make_test_session(last_verified_at=now - timedelta(minutes=30))

        assert is_patron_status_stale(session) is False

    def test_is_patron_status_stale_returns_true_when_old(self) -> None:
        """Test is_patron_status_stale returns True for old verification."""
        now = datetime.now(UTC)
        session = _make_test_session(
            last_verified_at=now - timedelta(seconds=PATRON_STATUS_REFRESH_INTERVAL_SECONDS + 60)
        )

        assert is_patron_status_stale(session) is True

    @pytest.mark.asyncio
    async def test_refresh_patron_status_updates_tier(self) -> None:
        """Test refresh_patron_status updates tier when membership changes."""
        low_tier = PATREON_TIERS[0]  # VIP Hexe - 30 req
        high_tier = PATREON_TIERS[3]  # Deadlock API €20 - 200 req
        session = _make_test_session(tier_id=low_tier.tier_id)  # Start at low tier

        # Mock returning higher tier membership data
        mock_user_data = PatreonUserData(
            user_id="123456",
            email="user@example.com",
            membership_data={"entitled_tier_ids": [high_tier.tier_id]},
        )

        with (
            mock.patch("packages.api.patreon.fetch_user_identity", new_callable=AsyncMock) as mock_fetch,
            mock.patch("packages.api.patreon.update_patreon_session", new_callable=AsyncMock) as mock_update,
        ):
            mock_fetch.return_value = mock_user_data
            mock_update.return_value = True

            result = await refresh_patron_status("test-token", session)

            assert result.tier_id == high_tier.tier_id
            assert result.tier_name == high_tier.name
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_and_refresh_patron_status_no_refresh_when_fresh(self) -> None:
        """Test check_and_refresh_patron_status skips refresh when status is fresh."""
        now = datetime.now(UTC)
        session = _make_test_session(last_verified_at=now)

        with mock.patch("packages.api.patreon.refresh_patron_status", new_callable=AsyncMock) as mock_refresh:
            result = await check_and_refresh_patron_status("test-token", session)

            assert result is session
            mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_and_refresh_patron_status_returns_cached_on_failure(self) -> None:
        """Test check_and_refresh_patron_status returns cached tier on failure."""
        now = datetime.now(UTC)
        high_tier = PATREON_TIERS[3]
        session = _make_test_session(
            tier_id=high_tier.tier_id,
            last_verified_at=now - timedelta(hours=2),
        )

        with mock.patch("packages.api.patreon.refresh_patron_status", new_callable=AsyncMock) as mock_refresh:
            mock_refresh.side_effect = PatreonAPIError("API error")

            result = await check_and_refresh_patron_status("test-token", session)

            # Original session returned (cached tier used)
            assert result is session
            assert result.tier_id == high_tier.tier_id

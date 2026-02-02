# Deadlock AI Assistant - Patreon OAuth2 endpoint tests

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest import mock
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.api.auth_patreon import _mask_email, router
from packages.api.errors import RequestIDMiddleware
from packages.api.patreon import (
    DEFAULT_TIER_0_LIMIT,
    DEFAULT_TIER_1_CENTS,
    DEFAULT_TIER_1_LIMIT,
    DEFAULT_TIER_2_CENTS,
    DEFAULT_TIER_2_LIMIT,
    DEFAULT_TIER_3_CENTS,
    DEFAULT_TIER_3_LIMIT,
    TIER_NAMES,
    PatreonAPIError,
    PatreonConfig,
    PatreonSession,
    PatreonTierConfig,
    PatreonTokenResponse,
    PatreonUserData,
)


@pytest.fixture(autouse=True)
def reset_patreon_config() -> Generator[None]:
    """Reset patreon config cache before each test."""
    import packages.api.patreon as patreon_module

    patreon_module._cached_config = None
    yield
    patreon_module._cached_config = None


def create_test_app() -> FastAPI:
    """Create a test FastAPI app with Patreon auth routes."""
    test_app = FastAPI()
    test_app.add_middleware(cast(Any, RequestIDMiddleware))
    test_app.include_router(router)
    return test_app


def _make_test_config() -> PatreonConfig:
    """Create a test Patreon configuration."""
    return PatreonConfig(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="https://api.example.com/auth/patreon/callback",
        campaign_id="test-campaign-id",
        tier_1=PatreonTierConfig(threshold_cents=DEFAULT_TIER_1_CENTS, rate_limit=DEFAULT_TIER_1_LIMIT),
        tier_2=PatreonTierConfig(threshold_cents=DEFAULT_TIER_2_CENTS, rate_limit=DEFAULT_TIER_2_LIMIT),
        tier_3=PatreonTierConfig(threshold_cents=DEFAULT_TIER_3_CENTS, rate_limit=DEFAULT_TIER_3_LIMIT),
        default_rate_limit=DEFAULT_TIER_0_LIMIT,
    )


def _make_test_session(tier: int = 1) -> PatreonSession:
    """Create a test Patreon session."""
    now = datetime.now(UTC)
    return PatreonSession(
        user_id="123456",
        email="user@example.com",
        tier=tier,
        tier_name=TIER_NAMES[tier],
        rate_limit=[DEFAULT_TIER_0_LIMIT, DEFAULT_TIER_1_LIMIT, DEFAULT_TIER_2_LIMIT, DEFAULT_TIER_3_LIMIT][tier],
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        access_token_expires_at=now + timedelta(hours=1),
        last_verified_at=now,
    )


class TestPatreonAuthRedirect:
    """Tests for GET /auth/patreon endpoint."""

    def test_returns_503_when_patreon_not_enabled(self) -> None:
        """Test that 503 is returned when Patreon auth is not configured."""
        env = os.environ.copy()
        env.pop("PATREON_CLIENT_ID", None)
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("packages.api.auth_patreon.is_patreon_enabled", return_value=False),
        ):
            app = create_test_app()
            client = TestClient(app, follow_redirects=False)
            response = client.get("/auth/patreon")

            assert response.status_code == 503
            assert "not configured" in response.json()["detail"]

    def test_redirects_to_patreon_authorization_url(self) -> None:
        """Test that endpoint redirects to Patreon OAuth2 authorization URL."""
        with (
            mock.patch("packages.api.auth_patreon.is_patreon_enabled", return_value=True),
            mock.patch("packages.api.auth_patreon.get_authorization_redirect", new_callable=AsyncMock) as mock_redirect,
        ):
            mock_redirect.return_value = (
                "https://www.patreon.com/oauth2/authorize?client_id=test&state=abc123",
                "abc123",
            )

            app = create_test_app()
            client = TestClient(app, follow_redirects=False)
            response = client.get("/auth/patreon")

            assert response.status_code == 302
            assert "patreon.com/oauth2/authorize" in response.headers["location"]
            mock_redirect.assert_called_once()


class TestPatreonAuthCallback:
    """Tests for GET /auth/patreon/callback endpoint."""

    def test_returns_503_when_patreon_not_enabled(self) -> None:
        """Test that 503 is returned when Patreon auth is not configured."""
        with mock.patch("packages.api.auth_patreon.is_patreon_enabled", return_value=False):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/auth/patreon/callback?code=abc&state=xyz")

            assert response.status_code == 503

    def test_returns_400_for_patreon_error_response(self) -> None:
        """Test that 400 is returned when Patreon returns an error."""
        with mock.patch("packages.api.auth_patreon.is_patreon_enabled", return_value=True):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/auth/patreon/callback?error=access_denied&error_description=User+denied+access")

            assert response.status_code == 400
            assert "User denied access" in response.json()["detail"]

    def test_returns_400_for_missing_state(self) -> None:
        """Test that 400 is returned when state parameter is missing."""
        with mock.patch("packages.api.auth_patreon.is_patreon_enabled", return_value=True):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/auth/patreon/callback?code=abc")

            assert response.status_code == 400
            assert "Missing state" in response.json()["detail"]

    def test_returns_400_for_missing_code(self) -> None:
        """Test that 400 is returned when authorization code is missing."""
        with mock.patch("packages.api.auth_patreon.is_patreon_enabled", return_value=True):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/auth/patreon/callback?state=xyz")

            assert response.status_code == 400
            assert "Missing authorization code" in response.json()["detail"]

    def test_returns_400_for_invalid_state(self) -> None:
        """Test that 400 is returned for invalid/expired state parameter."""
        with (
            mock.patch("packages.api.auth_patreon.is_patreon_enabled", return_value=True),
            mock.patch("packages.api.auth_patreon.validate_oauth_state", new_callable=AsyncMock) as mock_validate,
        ):
            mock_validate.return_value = False

            app = create_test_app()
            client = TestClient(app)
            response = client.get("/auth/patreon/callback?code=abc&state=invalid")

            assert response.status_code == 400
            assert "Invalid or expired state" in response.json()["detail"]

    def test_returns_400_for_token_exchange_failure(self) -> None:
        """Test that 400 is returned when token exchange fails."""
        with (
            mock.patch("packages.api.auth_patreon.is_patreon_enabled", return_value=True),
            mock.patch("packages.api.auth_patreon.validate_oauth_state", new_callable=AsyncMock) as mock_validate,
            mock.patch("packages.api.auth_patreon.exchange_code_for_tokens", new_callable=AsyncMock) as mock_exchange,
        ):
            mock_validate.return_value = True
            mock_exchange.side_effect = PatreonAPIError("Invalid code", status_code=400)

            app = create_test_app()
            client = TestClient(app)
            response = client.get("/auth/patreon/callback?code=invalid&state=valid")

            assert response.status_code == 400
            assert "Token exchange failed" in response.json()["detail"]

    def test_returns_400_for_user_data_fetch_failure(self) -> None:
        """Test that 400 is returned when user data fetch fails."""
        now = datetime.now(UTC)
        mock_tokens = PatreonTokenResponse(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=now + timedelta(hours=1),
            token_type="Bearer",
            scope="identity identity[email]",
        )

        with (
            mock.patch("packages.api.auth_patreon.is_patreon_enabled", return_value=True),
            mock.patch("packages.api.auth_patreon.validate_oauth_state", new_callable=AsyncMock) as mock_validate,
            mock.patch("packages.api.auth_patreon.exchange_code_for_tokens", new_callable=AsyncMock) as mock_exchange,
            mock.patch("packages.api.auth_patreon.fetch_user_identity", new_callable=AsyncMock) as mock_fetch,
        ):
            mock_validate.return_value = True
            mock_exchange.return_value = mock_tokens
            mock_fetch.side_effect = PatreonAPIError("Unauthorized", status_code=401)

            app = create_test_app()
            client = TestClient(app)
            response = client.get("/auth/patreon/callback?code=valid&state=valid")

            assert response.status_code == 400
            assert "Failed to fetch user data" in response.json()["detail"]

    def test_successful_callback_returns_session_token(self) -> None:
        """Test that successful callback returns session token and tier info."""
        now = datetime.now(UTC)
        mock_tokens = PatreonTokenResponse(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=now + timedelta(hours=1),
            token_type="Bearer",
            scope="identity identity[email]",
        )
        mock_user = PatreonUserData(
            user_id="12345",
            email="patron@example.com",
            membership_data={"currently_entitled_amount_cents": 1000},
        )

        with (
            mock.patch("packages.api.auth_patreon.is_patreon_enabled", return_value=True),
            mock.patch("packages.api.auth_patreon.validate_oauth_state", new_callable=AsyncMock) as mock_validate,
            mock.patch("packages.api.auth_patreon.exchange_code_for_tokens", new_callable=AsyncMock) as mock_exchange,
            mock.patch("packages.api.auth_patreon.fetch_user_identity", new_callable=AsyncMock) as mock_fetch,
            mock.patch("packages.api.auth_patreon.determine_patron_tier") as mock_tier,
            mock.patch("packages.api.auth_patreon.create_patreon_session", new_callable=AsyncMock) as mock_session,
        ):
            mock_validate.return_value = True
            mock_exchange.return_value = mock_tokens
            mock_fetch.return_value = mock_user
            mock_tier.return_value = mock.MagicMock(tier=2, tier_name="Contributor", rate_limit=500)
            mock_session.return_value = "session-token-uuid"

            app = create_test_app()
            client = TestClient(app)
            response = client.get("/auth/patreon/callback?code=valid&state=valid")

            assert response.status_code == 200
            data = response.json()
            assert data["session_token"] == "session-token-uuid"
            assert data["tier"] == 2
            assert data["tier_name"] == "Contributor"
            assert data["rate_limit"] == 500


class TestPatreonLogout:
    """Tests for POST /auth/patreon/logout endpoint."""

    def test_returns_401_for_missing_token(self) -> None:
        """Test that 401 is returned when X-Patreon-Token header is missing."""
        app = create_test_app()
        client = TestClient(app)
        response = client.post("/auth/patreon/logout")

        assert response.status_code == 401
        assert "Missing X-Patreon-Token" in response.json()["detail"]

    def test_returns_401_for_invalid_token(self) -> None:
        """Test that 401 is returned for invalid session token."""
        with mock.patch("packages.api.auth_patreon.get_patreon_session", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            app = create_test_app()
            client = TestClient(app)
            response = client.post(
                "/auth/patreon/logout",
                headers={"X-Patreon-Token": "invalid-token"},
            )

            assert response.status_code == 401
            assert "Invalid or expired" in response.json()["detail"]

    def test_successful_logout_deletes_session(self) -> None:
        """Test that successful logout deletes session and returns success message."""
        session = _make_test_session()

        with (
            mock.patch("packages.api.auth_patreon.get_patreon_session", new_callable=AsyncMock) as mock_get,
            mock.patch("packages.api.auth_patreon.delete_patreon_session", new_callable=AsyncMock) as mock_delete,
        ):
            mock_get.return_value = session
            mock_delete.return_value = True

            app = create_test_app()
            client = TestClient(app)
            response = client.post(
                "/auth/patreon/logout",
                headers={"X-Patreon-Token": "valid-token"},
            )

            assert response.status_code == 200
            assert response.json()["message"] == "Successfully logged out"
            mock_delete.assert_called_once_with("valid-token")


class TestPatreonStatus:
    """Tests for GET /auth/patreon/status endpoint."""

    def test_returns_401_for_missing_token(self) -> None:
        """Test that 401 is returned when X-Patreon-Token header is missing."""
        app = create_test_app()
        client = TestClient(app)
        response = client.get("/auth/patreon/status")

        assert response.status_code == 401
        assert "Missing X-Patreon-Token" in response.json()["detail"]

    def test_returns_401_for_invalid_token(self) -> None:
        """Test that 401 is returned for invalid session token."""
        with mock.patch("packages.api.auth_patreon.get_patreon_session", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            app = create_test_app()
            client = TestClient(app)
            response = client.get(
                "/auth/patreon/status",
                headers={"X-Patreon-Token": "invalid-token"},
            )

            assert response.status_code == 401
            assert "Invalid or expired" in response.json()["detail"]

    def test_returns_status_with_masked_email(self) -> None:
        """Test that status endpoint returns patron info with masked email."""
        session = _make_test_session(tier=2)

        with mock.patch("packages.api.auth_patreon.get_patreon_session", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = session

            app = create_test_app()
            client = TestClient(app)
            response = client.get(
                "/auth/patreon/status",
                headers={"X-Patreon-Token": "valid-token"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["authenticated"] is True
            assert data["tier"] == 2
            assert data["tier_name"] == TIER_NAMES[2]
            assert data["rate_limit"] == DEFAULT_TIER_2_LIMIT
            assert "***" in data["email"]  # Email is masked
            assert "expires_at" in data


class TestMaskEmail:
    """Tests for email masking helper."""

    def test_masks_normal_email(self) -> None:
        """Test masking a normal email address."""
        assert _mask_email("user@example.com") == "u***@example.com"

    def test_masks_short_local_part(self) -> None:
        """Test masking email with short local part."""
        assert _mask_email("a@test.org") == "a***@test.org"

    def test_handles_empty_email(self) -> None:
        """Test handling empty email."""
        assert _mask_email("") == "***@***"

    def test_handles_email_without_at(self) -> None:
        """Test handling malformed email without @."""
        assert _mask_email("notanemail") == "***@***"

    def test_handles_email_with_empty_local(self) -> None:
        """Test handling email with empty local part."""
        assert _mask_email("@domain.com") == "***@domain.com"

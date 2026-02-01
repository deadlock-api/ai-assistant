# Deadlock AI Assistant - Authentication tests

import os
from typing import Any, cast
from unittest import mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.api.auth import APIKeyAuthMiddleware, verify_turnstile_token
from packages.api.errors import RequestIDMiddleware


def create_test_app() -> FastAPI:
    """Create a test FastAPI app with authentication middleware."""
    test_app = FastAPI()
    # Cast needed due to Starlette's complex middleware typing with ParamSpec
    # RequestIDMiddleware must be added after auth so it runs first (LIFO)
    test_app.add_middleware(cast(Any, APIKeyAuthMiddleware))
    test_app.add_middleware(cast(Any, RequestIDMiddleware))

    @test_app.get("/protected")
    async def protected_endpoint() -> dict[str, str]:
        return {"message": "success"}

    @test_app.get("/health")
    async def health_endpoint() -> dict[str, str]:
        return {"status": "healthy"}

    return test_app


def test_valid_api_key_allows_request() -> None:
    """Test that a valid API key allows the request to proceed."""
    with mock.patch.dict(os.environ, {"API_KEYS": "test-key-123,other-key"}):
        app = create_test_app()
        client = TestClient(app)
        response = client.get("/protected", headers={"X-API-Key": "test-key-123"})
        assert response.status_code == 200
        assert response.json() == {"message": "success"}


def test_second_valid_api_key_allows_request() -> None:
    """Test that any valid API key from the comma-separated list works."""
    with mock.patch.dict(os.environ, {"API_KEYS": "test-key-123,other-key"}):
        app = create_test_app()
        client = TestClient(app)
        response = client.get("/protected", headers={"X-API-Key": "other-key"})
        assert response.status_code == 200
        assert response.json() == {"message": "success"}


def test_invalid_api_key_returns_401() -> None:
    """Test that an invalid API key returns 401 Unauthorized."""
    with mock.patch.dict(os.environ, {"API_KEYS": "test-key-123"}):
        app = create_test_app()
        client = TestClient(app)
        response = client.get("/protected", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "Invalid or missing API key"
        assert data["code"] == "AUTH_FAILED"
        assert "request_id" in data


def test_missing_api_key_returns_401() -> None:
    """Test that a missing API key returns 401 Unauthorized."""
    with mock.patch.dict(os.environ, {"API_KEYS": "test-key-123"}):
        app = create_test_app()
        client = TestClient(app)
        response = client.get("/protected")
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "Invalid or missing API key"
        assert data["code"] == "AUTH_FAILED"
        assert "request_id" in data


def test_health_endpoint_bypasses_authentication() -> None:
    """Test that /health endpoint does not require authentication."""
    with mock.patch.dict(os.environ, {"API_KEYS": "test-key-123"}):
        app = create_test_app()
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


def test_empty_api_keys_env_returns_401() -> None:
    """Test that empty API_KEYS environment returns 401 for protected routes."""
    with mock.patch.dict(os.environ, {"API_KEYS": ""}, clear=False):
        # Temporarily remove API_KEYS if it exists
        env_copy = os.environ.copy()
        if "API_KEYS" in env_copy:
            del env_copy["API_KEYS"]
        with mock.patch.dict(os.environ, env_copy, clear=True):
            app = create_test_app()
            client = TestClient(app)
            response = client.get("/protected", headers={"X-API-Key": "any-key"})
            assert response.status_code == 401


def test_api_key_with_whitespace_is_trimmed() -> None:
    """Test that API keys with whitespace in env var are properly trimmed."""
    with mock.patch.dict(os.environ, {"API_KEYS": "  test-key-123  , other-key  "}):
        app = create_test_app()
        client = TestClient(app)
        response = client.get("/protected", headers={"X-API-Key": "test-key-123"})
        assert response.status_code == 200


# Turnstile authentication tests


def test_valid_turnstile_token_allows_request() -> None:
    """Test that a valid Turnstile token allows the request to proceed."""
    with (
        mock.patch.dict(os.environ, {"TURNSTILE_SECRET_KEY": "test-secret"}),
        mock.patch("packages.api.auth.verify_turnstile_token") as mock_verify,
    ):
        mock_verify.return_value = True
        app = create_test_app()
        client = TestClient(app)
        response = client.get(
            "/protected",
            headers={"cf-turnstile-response": "valid-token"},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "success"}
        mock_verify.assert_called_once_with("valid-token", "test-secret")


def test_invalid_turnstile_token_returns_401() -> None:
    """Test that an invalid Turnstile token returns 401 Unauthorized."""
    with (
        mock.patch.dict(os.environ, {"TURNSTILE_SECRET_KEY": "test-secret"}),
        mock.patch("packages.api.auth.verify_turnstile_token") as mock_verify,
    ):
        mock_verify.return_value = False
        app = create_test_app()
        client = TestClient(app)
        response = client.get(
            "/protected",
            headers={"cf-turnstile-response": "invalid-token"},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "Turnstile verification failed"
        assert data["code"] == "AUTH_FAILED"
        assert "request_id" in data


def test_turnstile_without_secret_key_returns_401() -> None:
    """Test that Turnstile auth fails gracefully when secret key is not configured."""
    # Clear TURNSTILE_SECRET_KEY if it exists
    env = os.environ.copy()
    env.pop("TURNSTILE_SECRET_KEY", None)
    with mock.patch.dict(os.environ, env, clear=True):
        app = create_test_app()
        client = TestClient(app)
        response = client.get(
            "/protected",
            headers={"cf-turnstile-response": "some-token"},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "Turnstile verification failed"
        assert data["code"] == "AUTH_FAILED"
        assert "request_id" in data


def test_api_key_takes_precedence_over_turnstile() -> None:
    """Test that API key is checked before Turnstile when both are provided."""
    with (
        mock.patch.dict(os.environ, {"API_KEYS": "valid-key", "TURNSTILE_SECRET_KEY": "secret"}),
        mock.patch("packages.api.auth.verify_turnstile_token") as mock_verify,
    ):
        app = create_test_app()
        client = TestClient(app)
        response = client.get(
            "/protected",
            headers={
                "X-API-Key": "valid-key",
                "cf-turnstile-response": "some-token",
            },
        )
        assert response.status_code == 200
        # Turnstile should not be called when API key is valid
        mock_verify.assert_not_called()


def test_invalid_api_key_does_not_fall_through_to_turnstile() -> None:
    """Test that invalid API key returns 401 immediately without checking Turnstile."""
    with (
        mock.patch.dict(os.environ, {"API_KEYS": "valid-key", "TURNSTILE_SECRET_KEY": "secret"}),
        mock.patch("packages.api.auth.verify_turnstile_token") as mock_verify,
    ):
        mock_verify.return_value = True
        app = create_test_app()
        client = TestClient(app)
        response = client.get(
            "/protected",
            headers={
                "X-API-Key": "invalid-key",
                "cf-turnstile-response": "valid-token",
            },
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "Invalid or missing API key"
        assert data["code"] == "AUTH_FAILED"
        assert "request_id" in data
        # Turnstile should not be called when API key is present (even if invalid)
        mock_verify.assert_not_called()


# verify_turnstile_token function tests with mocked httpx


@pytest.mark.asyncio
async def test_verify_turnstile_token_success() -> None:
    """Test verify_turnstile_token returns True when Cloudflare returns success."""
    mock_response = httpx.Response(200, json={"success": True})

    with mock.patch("packages.api.auth.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock.AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await verify_turnstile_token("test-token", "test-secret")

        assert result is True
        mock_client.post.assert_called_once_with(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": "test-secret", "response": "test-token"},
        )


@pytest.mark.asyncio
async def test_verify_turnstile_token_failure() -> None:
    """Test verify_turnstile_token returns False when Cloudflare returns failure."""
    mock_response = httpx.Response(200, json={"success": False})

    with mock.patch("packages.api.auth.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock.AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await verify_turnstile_token("invalid-token", "test-secret")

        assert result is False


@pytest.mark.asyncio
async def test_verify_turnstile_token_api_error() -> None:
    """Test verify_turnstile_token returns False when Cloudflare API returns error status."""
    mock_response = httpx.Response(500)

    with mock.patch("packages.api.auth.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock.AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await verify_turnstile_token("test-token", "test-secret")

        assert result is False


@pytest.mark.asyncio
async def test_verify_turnstile_token_missing_success_field() -> None:
    """Test verify_turnstile_token returns False when response lacks success field."""
    mock_response = httpx.Response(200, json={"error": "something"})

    with mock.patch("packages.api.auth.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock.AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await verify_turnstile_token("test-token", "test-secret")

        assert result is False

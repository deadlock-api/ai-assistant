# Deadlock AI Assistant - Health endpoint tests

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from packages.api.app import app


def test_health_returns_200() -> None:
    """Test that /health returns 200 OK."""
    with patch("packages.api.health.redis_ping", new_callable=AsyncMock, return_value=False):
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200


def test_health_returns_healthy_status() -> None:
    """Test that /health returns status healthy."""
    with patch("packages.api.health.redis_ping", new_callable=AsyncMock, return_value=False):
        client = TestClient(app)
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"


def test_health_returns_version() -> None:
    """Test that /health includes version field."""
    with patch("packages.api.health.redis_ping", new_callable=AsyncMock, return_value=False):
        client = TestClient(app)
        response = client.get("/health")
        data = response.json()
        assert data["version"] == "1.0.0"


def test_health_includes_redis_status_connected() -> None:
    """Test that /health shows Redis as connected when available."""
    with patch("packages.api.health.redis_ping", new_callable=AsyncMock, return_value=True):
        client = TestClient(app)
        response = client.get("/health")
        data = response.json()
        assert data["services"]["redis"] == "connected"


def test_health_includes_redis_status_unavailable() -> None:
    """Test that /health shows Redis as unavailable when not connected."""
    with patch("packages.api.health.redis_ping", new_callable=AsyncMock, return_value=False):
        client = TestClient(app)
        response = client.get("/health")
        data = response.json()
        assert data["services"]["redis"] == "unavailable"

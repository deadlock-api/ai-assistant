"""Tests for Langfuse tracing integration."""

from __future__ import annotations

import pytest

from packages.ai_assistant.tracing import configure_tracing, flush_traces, is_tracing_enabled


class TestIsTracingEnabled:
    """Tests for is_tracing_enabled function."""

    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tracing is disabled when LANGFUSE_ENABLED is not set."""
        monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
        assert is_tracing_enabled() is False

    def test_disabled_when_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tracing is disabled when LANGFUSE_ENABLED is 'false'."""
        monkeypatch.setenv("LANGFUSE_ENABLED", "false")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        assert is_tracing_enabled() is False

    def test_disabled_without_public_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tracing is disabled when public key is missing."""
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        assert is_tracing_enabled() is False

    def test_disabled_without_secret_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tracing is disabled when secret key is missing."""
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        assert is_tracing_enabled() is False

    def test_enabled_with_all_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tracing is enabled when all required env vars are set."""
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        assert is_tracing_enabled() is True

    def test_enabled_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LANGFUSE_ENABLED is case-insensitive."""
        monkeypatch.setenv("LANGFUSE_ENABLED", "TRUE")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        assert is_tracing_enabled() is True


class TestConfigureTracing:
    """Tests for configure_tracing function."""

    def test_returns_false_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns False when tracing is disabled."""
        monkeypatch.setenv("LANGFUSE_ENABLED", "false")
        # Reset the global state
        import packages.ai_assistant.tracing as tracing_module

        tracing_module._tracing_configured = False

        result = configure_tracing()
        assert result is False

    def test_returns_true_when_already_configured(self) -> None:
        """Returns True immediately if already configured."""
        import packages.ai_assistant.tracing as tracing_module

        tracing_module._tracing_configured = True

        result = configure_tracing()
        assert result is True

        # Reset for other tests
        tracing_module._tracing_configured = False


class TestFlushTraces:
    """Tests for flush_traces function."""

    def test_noop_when_not_configured(self) -> None:
        """Does nothing when tracing is not configured."""
        import packages.ai_assistant.tracing as tracing_module

        tracing_module._tracing_configured = False

        # Should not raise
        flush_traces()

    def test_handles_import_error_gracefully(self) -> None:
        """Handles import errors gracefully when langfuse cannot be imported.

        The function should catch any exception and not propagate it.
        This tests the exception handling path in flush_traces.
        """
        import packages.ai_assistant.tracing as tracing_module

        tracing_module._tracing_configured = True

        # Even if langfuse import fails (which it does on Python 3.14 due to pydantic v1 issues),
        # flush_traces should handle it gracefully and not raise
        # This exercises the except block in flush_traces
        flush_traces()

        # Reset for other tests
        tracing_module._tracing_configured = False

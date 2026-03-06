"""Tests for the CLI configuration validation."""

from __future__ import annotations

import os
from unittest.mock import patch

from packages.cli.__main__ import validate_configuration


class TestValidateConfiguration:
    """Tests for the validate_configuration function."""

    def test_returns_none_when_anthropic_api_key_is_set(self) -> None:
        """Validation passes when ANTHROPIC_API_KEY is set."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            result = validate_configuration()
        assert result is None

    def test_returns_none_when_claude_code_oauth_token_is_set(self) -> None:
        """Validation passes when CLAUDE_CODE_OAUTH_TOKEN is set."""
        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "test-oauth-token"}, clear=True):
            result = validate_configuration()
        assert result is None

    def test_returns_none_when_both_auth_methods_are_set(self) -> None:
        """Validation passes when both authentication methods are set."""
        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "test-key", "CLAUDE_CODE_OAUTH_TOKEN": "test-token"},
            clear=True,
        ):
            result = validate_configuration()
        assert result is None

    def test_returns_none_when_anthropic_auth_token_is_set(self) -> None:
        """Validation passes when ANTHROPIC_AUTH_TOKEN is set (OpenRouter)."""
        with patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "or-test-token"}, clear=True):
            result = validate_configuration()
        assert result is None

    def test_returns_error_when_no_auth_is_set(self) -> None:
        """Validation fails with error message when no authentication is set."""
        with patch.dict(os.environ, {}, clear=True):
            result = validate_configuration()
        assert result is not None
        assert "ANTHROPIC_API_KEY" in result
        assert "CLAUDE_CODE_OAUTH_TOKEN" in result
        assert "ANTHROPIC_AUTH_TOKEN" in result

    def test_error_message_includes_all_environment_variable_names(self) -> None:
        """Error message mentions all supported environment variables."""
        with patch.dict(os.environ, {}, clear=True):
            result = validate_configuration()
        assert result is not None
        assert "ANTHROPIC_API_KEY" in result
        assert "CLAUDE_CODE_OAUTH_TOKEN" in result
        assert "ANTHROPIC_AUTH_TOKEN" in result

    def test_error_message_includes_export_instructions(self) -> None:
        """Error message includes instructions on how to set the variables."""
        with patch.dict(os.environ, {}, clear=True):
            result = validate_configuration()
        assert result is not None
        assert "export ANTHROPIC_API_KEY" in result
        assert "export CLAUDE_CODE_OAUTH_TOKEN" in result
        assert "export ANTHROPIC_AUTH_TOKEN" in result

    def test_returns_error_when_all_auth_methods_are_empty(self) -> None:
        """Validation fails when all authentication methods are set but empty."""
        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "", "CLAUDE_CODE_OAUTH_TOKEN": "", "ANTHROPIC_AUTH_TOKEN": ""},
            clear=True,
        ):
            result = validate_configuration()
        assert result is not None

    def test_passes_when_only_anthropic_api_key_is_valid(self) -> None:
        """Validation passes when only ANTHROPIC_API_KEY has a value."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "CLAUDE_CODE_OAUTH_TOKEN": ""}, clear=True):
            result = validate_configuration()
        assert result is None

    def test_passes_when_only_oauth_token_is_valid(self) -> None:
        """Validation passes when only CLAUDE_CODE_OAUTH_TOKEN has a value."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "CLAUDE_CODE_OAUTH_TOKEN": "test-token"}, clear=True):
            result = validate_configuration()
        assert result is None

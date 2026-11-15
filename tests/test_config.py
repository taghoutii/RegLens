"""Smoke tests for the settings module."""

import pytest

from reglens.config import Settings


def test_env_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test:6333")
    settings = Settings(_env_file=None)
    assert settings.qdrant_url == "http://qdrant.test:6333"


def test_secrets_are_hidden_when_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "super-secret-value")
    settings = Settings(_env_file=None)
    assert "super-secret-value" not in repr(settings)
    assert settings.mistral_api_key.get_secret_value() == "super-secret-value"

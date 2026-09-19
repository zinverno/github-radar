"""Configuration validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from github_radar.config import Settings, SettingsError


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/radar"
    )
    monkeypatch.setenv("DISCOVER_DEFAULT_LIMIT", "7")
    settings = Settings()
    assert settings.github_token == "ghp_test"
    assert settings.database_url.startswith("postgresql+asyncpg")
    assert settings.discover_default_limit == 7


def test_database_url_requires_asyncpg_scheme() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://u:p@localhost:5432/radar")


def test_require_github_token_missing() -> None:
    settings = Settings(github_token="", database_url="postgresql+asyncpg://u:p@localhost/a")
    with pytest.raises(SettingsError):
        settings.require_github_token()


def test_require_database_url_missing() -> None:
    settings = Settings(github_token="t", database_url="")
    with pytest.raises(SettingsError):
        settings.require_database_url()


def test_require_github_token_present() -> None:
    settings = Settings(github_token="t", database_url="postgresql+asyncpg://u:p@localhost/a")
    assert settings.require_github_token() == "t"


def test_defaults_sane() -> None:
    settings = Settings(github_token="t", database_url="postgresql+asyncpg://u:p@localhost/a")
    assert settings.rate_limit_pause_threshold == 50
    assert settings.discover_max_limit == 500
    assert settings.http_max_retries == 3
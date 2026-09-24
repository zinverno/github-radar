"""Application configuration loaded from the environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from github_radar.util import utcnow


class SettingsError(Exception):
    """Raised when the configuration is missing or invalid."""


class Settings(BaseSettings):
    """Runtime configuration.

    Every field maps to an environment variable of the same name written in
    SCREAMING_SNAKE_CASE (pydantic-settings default), e.g. ``github_token`` is
    read from ``GITHUB_TOKEN``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_assignment=True,
    )

    # Required for anything that talks to the GitHub API.
    github_token: str = ""

    # Required for anything that touches the database.
    database_url: str = ""

    # GitHub API.
    github_api_base_url: str = "https://api.github.com"
    github_api_version: str = "2022-11-28"

    # HTTP behaviour.
    http_timeout_seconds: float = 15.0
    http_max_retries: int = 3

    # Rate-limit strategy. When remaining quota for a resource drops to this
    # value (or below) the client refuses further requests.
    rate_limit_pause_threshold: int = 50

    # Discovery caps.
    discover_default_limit: int = 25
    discover_max_limit: int = 500
    contributors_limit_per_repo: int = 30
    profile_refresh_days: int = 7

    # Search pagination size (GitHub allows up to 100).
    search_per_page: int = 100

    # LLM provider (OpenAI-compatible chat completions API).
    ai_api_key: str = ""
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = ""
    ai_timeout_seconds: float = 60.0
    ai_max_output_tokens: int = 2048
    ai_temperature: float = 0.2
    ai_max_requests_per_run: int = 20
    ai_max_evidence_chars: int = 12000

    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if value and "postgresql+asyncpg://" not in value:
            raise ValueError(
                "DATABASE_URL must use the postgresql+asyncpg:// scheme, "
                f"got {value!r}"
            )
        return value

    @field_validator("http_max_retries")
    @classmethod
    def _validate_max_retries(cls, value: int) -> int:
        if value < 0 or value > 10:
            raise ValueError("http_max_retries must be between 0 and 10")
        return value

    @field_validator("rate_limit_pause_threshold")
    @classmethod
    def _validate_threshold(cls, value: int) -> int:
        if value < 0:
            raise ValueError("rate_limit_pause_threshold must be >= 0")
        return value

    @field_validator("ai_timeout_seconds")
    @classmethod
    def _validate_ai_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("ai_timeout_seconds must be > 0")
        return value

    @field_validator("ai_max_output_tokens")
    @classmethod
    def _validate_ai_max_output_tokens(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("ai_max_output_tokens must be > 0")
        return value

    @field_validator("ai_temperature")
    @classmethod
    def _validate_ai_temperature(cls, value: float) -> float:
        if not 0.0 <= value <= 2.0:
            raise ValueError("ai_temperature must be between 0 and 2")
        return value

    @field_validator("ai_max_requests_per_run")
    @classmethod
    def _validate_ai_max_requests_per_run(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("ai_max_requests_per_run must be > 0")
        return value

    @field_validator("ai_max_evidence_chars")
    @classmethod
    def _validate_ai_max_evidence_chars(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("ai_max_evidence_chars must be > 0")
        return value

    def require_ai_config(self) -> tuple[str, str, str]:
        """Return ``(base_url, api_key, model)`` or raise a clear error."""
        if not self.ai_api_key:
            raise SettingsError(
                "AI_API_KEY is not set. Configure your LLM provider in .env "
                "(see .env.example). AI commands need a working provider."
            )
        if not self.ai_model:
            raise SettingsError(
                "AI_MODEL is not set. Configure your LLM provider in .env "
                "(see .env.example)."
            )
        return self.ai_base_url.rstrip("/"), self.ai_api_key, self.ai_model

    def require_github_token(self) -> str:
        if not self.github_token:
            raise SettingsError(
                "GITHUB_TOKEN is not set. Create a token at "
                "https://github.com/settings/tokens and configure it in .env "
                "(see .env.example)."
            )
        return self.github_token

    def require_database_url(self) -> str:
        if not self.database_url:
            raise SettingsError(
                "DATABASE_URL is not set. Point it at a PostgreSQL database, "
                "e.g. postgresql+asyncpg://user:pass@localhost:5432/github_radar "
                "(see .env.example)."
            )
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    """Return the application settings, cached after first load."""
    return Settings()


def settings_loaded_at() -> str:
    """Expose when the cached settings snapshot was created (for CLI stats)."""
    return utcnow().isoformat()
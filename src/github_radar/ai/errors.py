"""Structured exceptions raised by the AI synthesis layer.

The hierarchy mirrors :mod:`github_radar.github.errors`: a base exception,
a configuration error, transport-level failures, capped-retry provider
failures, and validation failures. The CLI maps these onto clear messages
without ever leaking credentials.
"""

from __future__ import annotations


class AIError(Exception):
    """Base class for all AI synthesis errors."""


class AIConfigurationError(AIError):
    """The LLM provider is not configured or the configuration is invalid."""


class AINetworkError(AIError):
    """A transport-level failure (connection, DNS, timeout) to the provider."""


class AIProviderError(AIError):
    """The provider returned a non-retryable error response.

    ``status_code`` is the HTTP status (``None`` when retries were exhausted
    without a definitive response).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class AIRateLimitError(AIError):
    """The provider is rate-limiting us (429 or 5xx with Retry-After).

    Not retried blind: the caller is told the limit exists and that the user
    should retry later or reduce the request cap.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.status_code = status_code


class AIResponseValidationError(AIError):
    """The provider returned content that does not match the requested schema.

    Raised only after the single bounded repair attempt has also failed, so it
    always means "this artifact was NOT persisted and NOT presented as fact".
    """


class AIRequestLimitError(AIError):
    """The per-run model request cap (``ai_max_requests_per_run``) was hit."""


__all__ = [
    "AIConfigurationError",
    "AIError",
    "AINetworkError",
    "AIProviderError",
    "AIRateLimitError",
    "AIRequestLimitError",
    "AIResponseValidationError",
]
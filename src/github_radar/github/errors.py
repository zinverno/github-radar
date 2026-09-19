"""Structured exceptions raised by the GitHub API layer."""

from __future__ import annotations

from datetime import datetime


class GitHubError(Exception):
    """Base class for all GitHub API related errors."""


class GitHubConfigurationError(GitHubError):
    """Raised when GitHub client configuration is missing/invalid."""


class GitHubNetworkError(GitHubError):
    """A transport-level failure (connection, DNS, timeout)."""


class GitHubAPIError(GitHubError):
    """An error response returned by the GitHub API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        url: str,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.response_body = response_body


class GitHubAuthenticationError(GitHubAPIError):
    """HTTP 401 — bad or missing credentials."""


class GitHubForbiddenError(GitHubAPIError):
    """HTTP 403 — permission denied (not a rate-limit response)."""


_RATE_LIMIT_HINT = (
    "The API rejected the request because the request originates from a "
    "source that exceeds individual rate limits."
)


class GitHubRateLimitError(GitHubAPIError):
    """HTTP 403/429 caused by rate limiting."""


class GitHubNotFoundError(GitHubAPIError):
    """HTTP 404 — the requested resource does not exist."""


class GitHubValidationError(GitHubAPIError):
    """HTTP 422 — the request was well-formed but semantically invalid."""


class RateLimitExceeded(GitHubError):
    """The application refuses to continue because quota is too low.

    Raised either when the client observes an exhausted or dangerously low
    rate-limit resource for the current operation, or when GitHub answered
    with an explicit rate-limit response that cannot be retried within a
    short window.
    """

    def __init__(
        self,
        *,
        resource: str,
        remaining: int | None,
        reset_at: datetime | None,
        retry_after_seconds: float | None = None,
        message: str | None = None,
    ) -> None:
        self.resource = resource
        self.remaining = remaining
        self.reset_at = reset_at
        self.retry_after_seconds = retry_after_seconds
        if message is None:
            message = (
                f"Rate limit exceeded for resource {resource!r}"
                f" (remaining={remaining}, reset_at={reset_at})."
            )
        super().__init__(message)


# Small set of un-retryable HTTP status codes.
UNRETRYABLE_STATUS_CODES = frozenset({401, 403, 404, 422, 451})


def classify_error(
    *,
    status_code: int,
    url: str,
    response_body: str,
    is_rate_limit: bool,
) -> GitHubAPIError:
    """Build the most specific error type for a non-success status code."""
    if is_rate_limit:
        return GitHubRateLimitError(
            _RATE_LIMIT_HINT,
            status_code=status_code,
            url=url,
            response_body=response_body,
        )
    cls: type[GitHubAPIError]
    if status_code == 401:
        cls = GitHubAuthenticationError
    elif status_code == 403:
        cls = GitHubForbiddenError
    elif status_code == 404:
        cls = GitHubNotFoundError
    elif status_code == 422:
        cls = GitHubValidationError
    else:
        cls = GitHubAPIError
    return cls(
        _extract_message(response_body) or f"GitHub API error {status_code}",
        status_code=status_code,
        url=url,
        response_body=response_body,
    )


def _extract_message(response_body: str) -> str | None:
    if not response_body:
        return None
    try:
        import json

        data = json.loads(response_body)
    except (ValueError, TypeError):
        return response_body[:500]
    if isinstance(data, dict):
        msg = data.get("message")
        if isinstance(msg, str):
            return msg
    return response_body[:500]
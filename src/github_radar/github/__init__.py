"""GitHub API layer."""

from github_radar.github.client import GitHubClient, RateLimitState
from github_radar.github.errors import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubConfigurationError,
    GitHubError,
    GitHubForbiddenError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
    RateLimitExceeded,
)

__all__ = [
    "GitHubAPIError",
    "GitHubAuthenticationError",
    "GitHubClient",
    "GitHubConfigurationError",
    "GitHubError",
    "GitHubForbiddenError",
    "GitHubNetworkError",
    "GitHubNotFoundError",
    "GitHubRateLimitError",
    "GitHubValidationError",
    "RateLimitExceeded",
    "RateLimitState",
]
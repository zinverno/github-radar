"""GitHub API calls related to users/developers."""

from __future__ import annotations

import logging
from urllib.parse import quote

from github_radar.domain import Developer
from github_radar.github.client import GitHubClient
from github_radar.github.models import GithubUser

logger = logging.getLogger(__name__)


async def get_user_profile(client: GitHubClient, login: str) -> Developer:
    """Fetch ``GET /users/{login}`` and return a canonical Developer."""
    url = f"{client.base_url}/users/{quote(login)}"
    data = await client.get_json(url)
    user = GithubUser.model_validate(data)
    logger.debug("Fetched profile for %s", login)
    return user.to_domain()


__all__ = ["get_user_profile"]
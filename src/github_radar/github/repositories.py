"""GitHub API calls related to repositories."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import date
from urllib.parse import quote

from github_radar.domain import Contributor, Developer, Repository
from github_radar.github.client import GitHubClient
from github_radar.github.models import GithubContributor, GithubRepository

logger = logging.getLogger(__name__)


def _quote_slug(value: str) -> str:
    return quote(value, safe="")


async def search_repositories(
    client: GitHubClient,
    *,
    query: str,
    language: str | None = None,
    topic: str | None = None,
    min_stars: int | None = None,
    created_after: date | None = None,
    sort: str = "stars",
    order: str = "desc",
    limit: int | None = None,
    per_page: int = 100,
) -> AsyncIterator[Repository]:
    """Iterate ``GET /search/repositories`` results as domain Repositories."""
    terms = [query.strip()]
    if language:
        terms.append(f"language:{language}")
    if topic:
        terms.append(f"topic:{topic}")
    if min_stars is not None:
        terms.append(f"stars:>={min_stars}")
    if created_after is not None:
        terms.append(f"created:>={created_after.isoformat()}")

    q = " ".join(terms)
    logger.info("Searching GitHub repositories: q=%r (limit=%s)", q, limit)
    url = f"{client.base_url}/search/repositories"
    params = {"q": q, "sort": sort, "order": order}

    async for item in client.paginate(
        url, params=params, per_page=per_page, max_items=limit, items_key="items"
    ):
        repo = GithubRepository.model_validate(item)
        yield repo.to_domain()


async def get_repository(client: GitHubClient, owner: str, name: str) -> Repository:
    """Fetch ``GET /repos/{owner}/{name}`` (full metadata incl. topics)."""
    url = f"{client.base_url}/repos/{_quote_slug(owner)}/{_quote_slug(name)}"
    data = await client.get_json(url)
    repo = GithubRepository.model_validate(data)
    logger.debug("Fetched repository %s/%s", owner, name)
    return repo.to_domain()


async def get_contributors(
    client: GitHubClient,
    owner: str,
    name: str,
    *,
    limit: int = 30,
) -> list[Contributor]:
    """Iterate ``GET /repos/{owner}/{name}/contributors``.

    GitHub orders contributors by descending cumulative contribution count and
    caps the page size at 100. ``limit`` bounds the number of rows we keep.
    """
    url = f"{client.base_url}/repos/{_quote_slug(owner)}/{_quote_slug(name)}/contributors"
    result: list[Contributor] = []
    async for item in client.paginate(url, per_page=100, max_items=limit):
        entry = GithubContributor.model_validate(item)
        result.append(
            Contributor(
                developer=Developer(
                    github_id=entry.github_id,
                    login=entry.login,
                    avatar_url=entry.avatar_url,
                    html_url=entry.html_url,
                ),
                contributions=entry.contributions,
            )
        )
    logger.debug(
        "Fetched %d contributors for %s/%s", len(result), owner, name
    )
    return result


__all__ = [
    "get_contributors",
    "get_repository",
    "search_repositories",
]
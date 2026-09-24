"""GitHub API calls related to repositories."""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator
from datetime import date
from urllib.parse import quote

from github_radar.domain import Contributor, Developer, Repository
from github_radar.github.client import GitHubClient
from github_radar.github.errors import GitHubNotFoundError
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


async def get_readme(client: GitHubClient, owner: str, name: str) -> str:
    """Fetch ``GET /repos/{owner}/{name}/readme`` as plain text.

    Returns an empty string when the repository has no README (the API answers
    404) so the AI textual-evidence layer can treat "no README" as an absent
    piece of evidence, never an error.
    """
    url = f"{client.base_url}/repos/{_quote_slug(owner)}/{_quote_slug(name)}/readme"
    try:
        data = await client.get_json(url)
    except GitHubNotFoundError:
        return ""
    if not isinstance(data, dict) or not isinstance(data.get("content"), str):
        return ""
    try:
        decoded = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""
    logger.debug("Fetched README for %s/%s (%d chars)", owner, name, len(decoded))
    return decoded


async def get_releases(
    client: GitHubClient,
    owner: str,
    name: str,
    *,
    limit: int = 5,
) -> list[dict[str, object]]:
    """Fetch the ``limit`` most recent non-draft releases' metadata.

    Each dict carries ``tag_name``, ``name``, ``published_at`` and ``body``
    (body may be empty). Draft releases are excluded server-side.
    """
    url = f"{client.base_url}/repos/{_quote_slug(owner)}/{_quote_slug(name)}/releases"
    result: list[dict[str, object]] = []
    async for item in client.paginate(url, per_page=limit, max_items=limit):
        result.append(
            {
                "tag_name": item.get("tag_name"),
                "name": item.get("name"),
                "published_at": item.get("published_at"),
                "body": item.get("body") or "",
            }
        )
    logger.debug("Fetched %d releases for %s/%s", len(result), owner, name)
    return result


async def get_commits(
    client: GitHubClient,
    owner: str,
    name: str,
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Fetch the ``limit`` most recent commits' ``sha``/``message``/``date``."""
    url = f"{client.base_url}/repos/{_quote_slug(owner)}/{_quote_slug(name)}/commits"
    result: list[dict[str, object]] = []
    async for item in client.paginate(url, per_page=limit, max_items=limit):
        commit = item.get("commit") or {}
        author = commit.get("author") or {}
        result.append(
            {
                "sha": (item.get("sha") or "")[:12],
                "message": (commit.get("message") or "").strip() or "—",
                "date": author.get("date"),
            }
        )
    logger.debug("Fetched %d commits for %s/%s", len(result), owner, name)
    return result


__all__ = [
    "get_commits",
    "get_contributors",
    "get_readme",
    "get_releases",
    "get_repository",
    "search_repositories",
]
"""GitHub endpoint function tests (search, detail, contributors, users)."""

from __future__ import annotations

from datetime import date

import httpx

from github_radar.github.client import GitHubClient
from github_radar.github.repositories import (
    get_contributors,
    get_repository,
    search_repositories,
)
from github_radar.github.users import get_user_profile
from tests.conftest import (
    BASE_URL,
    contributors_payload,
    repo_detail_payload,
    search_item_payload,
    user_payload,
)


async def test_search_repositories_builds_qualifiers(
    settings, api_mock
) -> None:
    route = api_mock.get(f"{BASE_URL}/search/repositories").mock(
        return_value=httpx.Response(
            200, json={"total_count": 1, "items": [search_item_payload(github_id=1)]}
        )
    )
    async with GitHubClient(settings) as client:
        results = [
            repo
            async for repo in search_repositories(
                client,
                query="mcp",
                language="python",
                topic="ai-agents",
                min_stars=10,
                created_after=date(2024, 1, 1),
                limit=5,
            )
        ]
    assert results[0].full_name == "octo/mcp-servers"
    request = route.calls.last.request
    q = request.url.params["q"]
    assert "language:python" in q
    assert "topic:ai-agents" in q
    assert "stars:>=10" in q
    assert "created:>=2024-01-01" in q


async def test_get_repository_parses_detail(settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/repos/octo/mcp-servers").mock(
        return_value=httpx.Response(
            200, json=repo_detail_payload(github_id=1, watchers=43)
        )
    )
    async with GitHubClient(settings) as client:
        repo = await get_repository(client, "octo", "mcp-servers")
    assert repo.full_name == "octo/mcp-servers"
    assert repo.watchers == 43


async def test_get_contributors_returns_cumulative_counts(settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/repos/octo/repo/contributors").mock(
        return_value=httpx.Response(
            200, json=contributors_payload([("alice", 101, 42), ("bob", 102, 3)])
        )
    )
    async with GitHubClient(settings) as client:
        contributors = await get_contributors(client, "octo", "repo", limit=30)
    assert len(contributors) == 2
    assert contributors[0].contributions == 42
    assert contributors[0].developer.login == "alice"
    assert contributors[0].developer.github_id == 101


async def test_get_user_profile(settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/users/octo").mock(
        return_value=httpx.Response(200, json=user_payload())
    )
    async with GitHubClient(settings) as client:
        profile = await get_user_profile(client, "octo")
    assert profile.login == "octo"
    assert profile.followers == 1000
    assert profile.public_email == "octo@example.com"
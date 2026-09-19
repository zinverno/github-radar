"""Shared fixtures: mocked GitHub payloads and a respx router."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import respx

from github_radar.config import Settings

BASE_URL = "https://api.github.com"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        github_token="test-token",
        database_url="postgresql+asyncpg://u:p@localhost:5432/radar",
        github_api_base_url=BASE_URL,
        http_max_retries=2,
        rate_limit_pause_threshold=0,
    )


@pytest.fixture
def api_mock() -> Iterator[respx.MockRouter]:
    """A started respx router, reset after each test."""
    mock = respx.mock
    mock.start()
    yield mock
    mock.reset()
    mock.stop()


def search_item_payload(
    *,
    github_id: int,
    owner_login: str = "octo",
    name: str = "mcp-servers",
    stars: int = 100,
    forks: int = 12,
    topics: tuple[str, ...] = (),
) -> dict[str, Any]:
    full_name = f"{owner_login}/{name}"
    return {
        "id": github_id,
        "node_id": f"node-{github_id}",
        "name": name,
        "full_name": full_name,
        "private": False,
        "owner": {
            "login": owner_login,
            "id": 9000 + github_id,
            "avatar_url": "https://avatars.example.com/a.png",
            "html_url": f"https://github.com/{owner_login}",
            "type": "User",
        },
        "html_url": f"https://github.com/{full_name}",
        "description": "A mock repository",
        "fork": False,
        "url": f"{BASE_URL}/repos/{full_name}",
        "created_at": "2024-01-05T10:00:00Z",
        "updated_at": "2024-06-01T10:00:00Z",
        "pushed_at": "2024-06-02T10:00:00Z",
        "homepage": None,
        "size": 4096,
        "stargazers_count": stars,
        "watchers_count": stars,
        "language": "python",
        "forks_count": forks,
        "open_issues_count": 3,
        "default_branch": "main",
        "topics": list(topics),
    }


def repo_detail_payload(
    *,
    github_id: int,
    owner_login: str = "octo",
    name: str = "mcp-servers",
    stars: int = 100,
    forks: int = 12,
    watchers: int | None = 43,
    topics: tuple[str, ...] = (),
    archived: bool = False,
    visibility: str = "public",
) -> dict[str, Any]:
    payload = search_item_payload(
        github_id=github_id,
        owner_login=owner_login,
        name=name,
        stars=stars,
        forks=forks,
        topics=topics,
    )
    payload.update(
        {
            "subscribers_count": watchers,
            "archived": archived,
            "disabled": False,
            "visibility": visibility,
            "is_template": False,
            "has_wiki": True,
        }
    )
    return payload


def search_response_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total_count": len(items),
        "incomplete_results": False,
        "items": items,
    }


def user_payload(
    *,
    login: str = "octo",
    github_id: int = 42,
    email: str | None = "octo@example.com",
) -> dict[str, Any]:
    return {
        "login": login,
        "id": github_id,
        "node_id": f"user-{github_id}",
        "avatar_url": "https://avatars.example.com/octo.png",
        "html_url": f"https://github.com/{login}",
        "name": "Octo Cat",
        "company": "Acme",
        "blog": "https://blog.example.com",
        "location": "San Francisco",
        "email": email,
        "bio": "I build things",
        "twitter_username": "octocat",
        "followers": 1000,
        "following": 50,
        "public_repos": 12,
        "created_at": "2011-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }


def contributors_payload(rows: list[tuple[str, int, int]]) -> list[dict[str, Any]]:
    return [
        {
            "login": login,
            "id": github_id,
            "node_id": f"user-{github_id}",
            "avatar_url": f"https://avatars.example.com/{login}.png",
            "html_url": f"https://github.com/{login}",
            "contributions": contributions,
        }
        for login, github_id, contributions in rows
    ]
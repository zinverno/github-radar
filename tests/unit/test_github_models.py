"""GitHub JSON payload → domain model parsing tests."""

from __future__ import annotations

from github_radar.github.models import (
    GithubContributor,
    GithubRepository,
    GithubSearchResponse,
    GithubUser,
)
from tests.conftest import (
    contributors_payload,
    repo_detail_payload,
    search_item_payload,
    search_response_payload,
    user_payload,
)


def test_search_item_parses_to_domain() -> None:
    payload = search_item_payload(
        github_id=7,
        stars=100,
        forks=12,
        topics=("mcp", "ai"),
    )
    repo = GithubRepository.model_validate(payload).to_domain()

    assert repo.github_id == 7
    assert repo.full_name == "octo/mcp-servers"
    assert repo.owner_login == "octo"
    assert repo.primary_language == "python"
    assert repo.stars == 100
    assert repo.forks == 12
    assert repo.topics == ("mcp", "ai")
    assert repo.created_at is not None
    assert repo.created_at.year == 2024
    # Search payloads carry watchers == stargazers; keep it as fallback.
    assert repo.watchers == 100
    assert repo.is_fork is False


def test_detail_parses_watchers_from_subscribers() -> None:
    payload = repo_detail_payload(github_id=7, stars=100, watchers=43)
    repo = GithubRepository.model_validate(payload).to_domain()
    assert repo.watchers == 43
    assert repo.visibility == "public"
    assert repo.is_template is False


def test_search_response_parses_items() -> None:
    payload = search_response_payload([search_item_payload(github_id=1)])
    parsed = GithubSearchResponse.model_validate(payload)
    assert parsed.total_count == 1
    assert parsed.items[0].github_id == 1


def test_user_parses_to_domain() -> None:
    user = GithubUser.model_validate(user_payload()).to_domain()
    assert user.login == "octo"
    assert user.github_id == 42
    assert user.public_email == "octo@example.com"
    assert user.followers == 1000
    assert user.twitter_username == "octocat"
    assert user.created_at is not None


def test_user_without_public_email() -> None:
    user = GithubUser.model_validate(user_payload(email=None)).to_domain()
    assert user.public_email is None


def test_contributors_parse_cumulative_counts() -> None:
    payload = contributors_payload([("alice", 101, 42), ("bob", 102, 3)])
    parsed = [GithubContributor.model_validate(item) for item in payload]
    assert parsed[0].login == "alice"
    assert parsed[0].contributions == 42
    assert parsed[1].contributions == 3
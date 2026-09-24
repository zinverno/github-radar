"""Textual evidence layer: TTL cache, counters, truncation, error fallback."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta

import httpx

from github_radar.ai.safety import UNTRUSTED_END, UNTRUSTED_START, wrap_untrusted
from github_radar.ai.textual import (
    MAX_COMMIT_MESSAGE_CHARS,
    MAX_README_CHARS,
    MAX_RELEASE_BODY_CHARS,
    TextualEvidence,
)
from github_radar.github.client import GitHubClient
from tests.conftest import BASE_URL


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: int) -> None:
        self.now += timedelta(**kwargs)


def _readme_response(text: str) -> httpx.Response:
    return httpx.Response(
        200, json={"content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    )


def test_readme_is_cached_until_ttl(settings, api_mock) -> None:
    route = api_mock.get(f"{BASE_URL}/repos/octo/repo/readme").mock(
        return_value=_readme_response("# hello world\n")
    )
    clock = FakeClock(datetime(2024, 5, 1, tzinfo=UTC))

    async def run() -> dict[str, str | int | dict[str, int]]:
        async with GitHubClient(settings) as client:
            evidence = TextualEvidence(client, ttl_seconds=3600.0, now=clock)
            first = await evidence.readme("octo", "repo")
            second = await evidence.readme("octo", "repo")
            return {
                "first": first,
                "second": second,
                "counters": evidence.counters(),
                "api_calls": len(route.calls),
            }

    result = asyncio.run(run())
    assert result["first"] == "# hello world"
    assert result["second"] == "# hello world"
    assert result["counters"] == {
        "github_evidence_requests": 2,
        "evidence_cache_hits": 1,
        "evidence_cache_misses": 1,
    }
    assert result["api_calls"] == 1


def test_readme_expired_ttl_fetches_again(settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/repos/octo/repo/readme").mock(
        return_value=_readme_response("again")
    )
    clock = FakeClock(datetime(2024, 5, 1, tzinfo=UTC))

    async def run() -> dict[str, int]:
        async with GitHubClient(settings) as client:
            evidence = TextualEvidence(client, ttl_seconds=3600.0, now=clock)
            await evidence.readme("octo", "repo")
            clock.advance(seconds=7200)
            await evidence.readme("octo", "repo")
            return evidence.counters()

    counters = asyncio.run(run())
    assert counters["github_evidence_requests"] == 2
    assert counters["evidence_cache_hits"] == 0
    assert counters["evidence_cache_misses"] == 2


def test_readme_truncated_to_max(settings, api_mock) -> None:
    text = "x" * (MAX_README_CHARS + 200)
    api_mock.get(f"{BASE_URL}/repos/octo/repo/readme").mock(
        return_value=_readme_response(text)
    )

    async def run() -> str:
        async with GitHubClient(settings) as client:
            evidence = TextualEvidence(client)
            return await evidence.readme("octo", "repo")

    value = asyncio.run(run())
    assert len(value) <= MAX_README_CHARS + 15
    assert value.endswith("…[truncated]")


def test_gh_error_falls_back_to_empty(settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/repos/octo/repo/readme").mock(
        return_value=httpx.Response(503, json={"message": "overloaded"})
    )

    async def run() -> str:
        async with GitHubClient(settings) as client:
            evidence = TextualEvidence(client)
            return await evidence.readme("octo", "repo")

    assert asyncio.run(run()) == ""


def test_releases_and_commits_rendered_with_bounds(settings, api_mock) -> None:
    big_body = "note " + "z" * (MAX_RELEASE_BODY_CHARS + 100)
    api_mock.get(f"{BASE_URL}/repos/octo/repo/releases").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "tag_name": "v2",
                    "name": "v2",
                    "published_at": "2024-05-01T00:00:00Z",
                    "body": big_body,
                }
            ],
        )
    )
    long_message = "fix " + "m" * (MAX_COMMIT_MESSAGE_CHARS + 50)
    api_mock.get(f"{BASE_URL}/repos/octo/repo/commits").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "sha": "a" * 40,
                    "commit": {
                        "author": {"date": "2024-05-02T01:00:00Z"},
                        "message": long_message,
                    },
                }
            ],
        )
    )

    async def run() -> tuple[str, str]:
        async with GitHubClient(settings) as client:
            evidence = TextualEvidence(client)
            releases = await evidence.releases("octo", "repo")
            commits = await evidence.commits("octo", "repo")
            return releases, commits

    releases, commits = asyncio.run(run())
    assert "v2" in releases
    assert "…[truncated]" in releases
    assert "2024-05-01" in releases
    assert commits.startswith("2024-05-02")
    assert "…[truncated]" in commits


def test_repo_text_is_wrapped_as_untrusted_data() -> None:
    wrapped = wrap_untrusted("README", "content")
    assert UNTRUSTED_START in wrapped
    assert UNTRUSTED_END in wrapped
    assert wrapped.endswith("\n</untrusted-data>")
"""The attempt budget cannot be bypassed by retries, pages or enrichment."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from github_radar.config import Settings
from github_radar.config.settings import GitHubRequestLimits
from github_radar.github.budget import RequestBudget, RequestBudgetExceeded
from github_radar.github.client import GitHubClient
from github_radar.github.errors import RateLimitExceeded

BASE = "https://api.github.com"


@pytest.mark.parametrize(
    ("path", "scope"),
    [
        ("/search/repositories", "search"),
        ("/repos/a/b", "repository_details"),
        ("/repos/a/b/contributors", "contributors"),
        ("/users/a", "profiles"),
        ("/rate_limit", "total"),
        ("/repos/a/b/readme", "total"),
    ],
)
async def test_each_cap_refuses_before_transport(settings: Settings, path: str, scope: str) -> None:
    budget = RequestBudget(GitHubRequestLimits.model_validate({scope: 1}))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    async with GitHubClient(
        settings, budget=budget, transport=httpx.MockTransport(handler)
    ) as client:
        await client.get_json(BASE + path)
        with pytest.raises(RequestBudgetExceeded) as error:
            await client.get_json(BASE + path)
    assert error.value.scope == scope
    assert len(calls) == 1
    assert budget.used[scope] == budget.used["total"] == 1


@pytest.mark.parametrize("failure", [503, 429, "transport"])
async def test_retries_consume_budget(settings: Settings, failure: int | str, monkeypatch) -> None:
    budget = RequestBudget(GitHubRequestLimits(total=1))
    calls = []

    async def no_sleep(_delay: float) -> None:
        pass

    monkeypatch.setattr("github_radar.github.client.asyncio.sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if failure == "transport":
            raise httpx.ConnectError("test", request=request)
        assert isinstance(failure, int)
        return httpx.Response(failure, headers={"retry-after": "0.01"})

    async with GitHubClient(
        settings, budget=budget, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(RequestBudgetExceeded):
            await client.get_json(BASE + "/repos/a/b")
    assert len(calls) == budget.used["total"] == 1


async def test_pagination_cannot_exceed_budget(settings: Settings) -> None:
    budget = RequestBudget(GitHubRequestLimits(search=1))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"items": [{"id": 1}, {"id": 2}]},
            headers={
                "link": f'<{BASE}/search/repositories?page=2>; rel="next"',
            },
        )

    async with GitHubClient(
        settings, budget=budget, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(RequestBudgetExceeded):
            _ = [
                row
                async for row in client.paginate(
                    BASE + "/search/repositories",
                    per_page=2,
                    items_key="items",
                )
            ]
    assert len(calls) == 1


async def test_zero_budget_and_shared_clients(settings: Settings) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    budget = RequestBudget(GitHubRequestLimits(total=1, profiles=0))
    async with (
        GitHubClient(settings, budget=budget, transport=httpx.MockTransport(handler)) as first,
        GitHubClient(settings, budget=budget, transport=httpx.MockTransport(handler)) as second,
    ):
        with pytest.raises(RequestBudgetExceeded, match="profiles"):
            await first.get_json(BASE + "/users/a")
        assert budget.used["total"] == 0
        results = await asyncio.gather(
            first.get_json(BASE + "/repos/a/b"),
            second.get_json(BASE + "/repos/c/d"),
            return_exceptions=True,
        )
    assert len(calls) == 1
    assert sum(isinstance(r, RequestBudgetExceeded) for r in results) == 1


async def test_search_threshold_is_independent_and_rechecked_on_retry(settings: Settings) -> None:
    settings.rate_limit_pause_threshold = 50
    settings.search_rate_limit_pause_threshold = 2
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200 if len(calls) == 1 else 503,
            json={},
            headers={
                "x-ratelimit-remaining": "29" if len(calls) == 1 else "2",
                "x-ratelimit-reset": "9999999999",
            },
        )

    async with GitHubClient(settings, transport=httpx.MockTransport(handler)) as client:
        await client.get_json(BASE + "/search/repositories")
        with pytest.raises(RateLimitExceeded):
            await client.get_json(BASE + "/search/repositories")
        assert client.budget.used["total"] == 2
    assert len(calls) == 2


async def test_custom_api_base_path_keeps_enrichment_budget(settings: Settings) -> None:
    settings.github_api_base_url = BASE + "/api/v3"
    settings.github_request_budget = GitHubRequestLimits(repository_details=0)

    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("Zero enrichment cap must prevent HTTP")

    async with GitHubClient(settings, transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(RequestBudgetExceeded, match="repository_details"):
            await client.get_json(client.base_url + "/repos/a/b")


def test_budget_environment_and_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REQUEST_BUDGET", '{"total": 7, "profiles": 0}')
    settings = Settings()
    assert settings.github_request_budget.total == 7
    assert settings.github_request_budget.profiles == 0
    for value in (-1, True, 1.5, "5"):
        with pytest.raises(ValueError):
            GitHubRequestLimits.model_validate({"total": value})


async def test_core_repository_named_search_and_header_resources(settings, api_mock):
    api_mock.get(BASE + "/repos/search/repo").mock(
        return_value=httpx.Response(
            200,
            json={},
            headers={"x-ratelimit-resource": "core", "x-ratelimit-remaining": "4000"},
        )
    )
    api_mock.get(BASE + "/search/repositories").mock(
        return_value=httpx.Response(
            200,
            json={},
            headers={"x-ratelimit-resource": "search", "x-ratelimit-remaining": "29"},
        )
    )
    async with GitHubClient(settings) as client:
        await client.get_json(BASE + "/repos/search/repo")
        await client.get_json(BASE + "/search/repositories")
        assert client.rate_limits["core"].remaining == 4000
        assert client.rate_limits["search"].remaining == 29
        assert client.budget.used["repository_details"] == client.budget.used["search"] == 1


async def test_external_pagination_link_never_sends_credentials(settings, api_mock):
    api_mock.get(BASE + "/repos/a/b/contributors").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1}],
            headers={
                "link": '<https://elsewhere.example/repos/a/b/contributors?page=2>; rel="next"',
            },
        )
    )
    from github_radar.github.errors import GitHubConfigurationError

    async with GitHubClient(settings) as client:
        with pytest.raises(GitHubConfigurationError):
            _ = [
                item async for item in client.paginate(BASE + "/repos/a/b/contributors", per_page=1)
            ]
        assert client.budget.used["total"] == 1


async def test_reservation_failure_prevents_http(settings, api_mock):
    route = api_mock.get(BASE + "/repos/a/b").mock(return_value=httpx.Response(200, json={}))

    async def failed(used):
        raise RuntimeError("durable receipt unavailable")

    async with GitHubClient(settings) as client:
        client.before_attempt = failed
        with pytest.raises(RuntimeError):
            await client.get_json(BASE + "/repos/a/b")
        assert route.call_count == 0
        assert client.budget.used["total"] == 1

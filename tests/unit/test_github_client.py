"""GitHub client behaviour tests (mocked HTTP)."""

from __future__ import annotations

import httpx
import pytest

from github_radar.config import Settings
from github_radar.github import (
    GitHubAuthenticationError,
    GitHubClient,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubValidationError,
    RateLimitExceeded,
)
from tests.conftest import BASE_URL


def make_client(settings: Settings) -> GitHubClient:
    return GitHubClient(settings)


# ---------------------------------------------------------------------------
# Success + auth + rate-limit tracking
# ---------------------------------------------------------------------------

async def test_get_json_sends_bearer_auth(settings: Settings, api_mock) -> None:
    route = api_mock.get(f"{BASE_URL}/users/octo").mock(
        return_value=httpx.Response(200, json={"login": "octo", "id": 1})
    )
    async with make_client(settings) as client:
        data = await client.get_json(f"{BASE_URL}/users/octo")
    assert data["login"] == "octo"
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer test-token"


async def test_rate_limit_headers_are_recorded(settings: Settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/repos/a/b").mock(
        return_value=httpx.Response(
            200,
            json={"id": 1},
            headers={
                "x-ratelimit-limit": "5000",
                "x-ratelimit-remaining": "4999",
                "x-ratelimit-reset": "2000000000",
            },
        )
    )
    async with make_client(settings) as client:
        await client.get_json(f"{BASE_URL}/repos/a/b")
        core = client.rate_limits["core"]
        assert core.limit == 5000
        assert core.remaining == 4999
        assert core.reset_at is not None


async def test_resource_classified_as_search(settings: Settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/search/repositories").mock(
        return_value=httpx.Response(
            200,
            json={"total_count": 0, "items": []},
            headers={
                "x-ratelimit-resource": "search",
                "x-ratelimit-remaining": "29",
            },
        )
    )
    async with make_client(settings) as client:
        await client.get_json(f"{BASE_URL}/search/repositories", params={"q": "x"})
        assert "search" in client.rate_limits
        assert client.rate_limits["search"].remaining == 29


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

async def test_pagination_follows_link_header(settings: Settings, api_mock) -> None:
    page2_items = [{"id": 3}, {"id": 4}]
    api_mock.get(f"{BASE_URL}/items").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[{"id": 1}, {"id": 2}],
                headers={"link": f'<{BASE_URL}/items?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=page2_items),
        ]
    )
    async with make_client(settings) as client:
        fetched = [item async for item in client.paginate(f"{BASE_URL}/items", per_page=2)]
    assert [item["id"] for item in fetched] == [1, 2, 3, 4]


async def test_pagination_stops_on_short_page(settings: Settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/items").mock(
        return_value=httpx.Response(200, json=[{"id": 1}])
    )
    async with make_client(settings) as client:
        fetched = [item async for item in client.paginate(f"{BASE_URL}/items", per_page=2)]
    assert len(fetched) == 1


async def test_pagination_respects_max_items(settings: Settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/items").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[{"id": i} for i in range(100)],
                headers={"link": f'<{BASE_URL}/items?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[{"id": i} for i in range(100, 200)]),
        ]
    )
    async with make_client(settings) as client:
        fetched = [
            item
            async for item in client.paginate(f"{BASE_URL}/items", per_page=100, max_items=150)
        ]
    assert len(fetched) == 150


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

async def test_404_raises_not_found(settings: Settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/repos/a/b").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    async with make_client(settings) as client:
        with pytest.raises(GitHubNotFoundError):
            await client.get_json(f"{BASE_URL}/repos/a/b")


async def test_401_raises_authentication(settings: Settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/repos/a/b").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )
    async with make_client(settings) as client:
        with pytest.raises(GitHubAuthenticationError):
            await client.get_json(f"{BASE_URL}/repos/a/b")


async def test_422_raises_validation(settings: Settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/repos/a/b").mock(
        return_value=httpx.Response(
            422, json={"message": "Validation Failed", "errors": []}
        )
    )
    async with make_client(settings) as client:
        with pytest.raises(GitHubValidationError):
            await client.get_json(f"{BASE_URL}/repos/a/b")


async def test_403_with_zero_remaining_raises_rate_limit(
    settings: Settings, api_mock
) -> None:
    api_mock.get(f"{BASE_URL}/repos/a/b").mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded for ..."},
            headers={"x-ratelimit-remaining": "0"},
        )
    )
    async with make_client(settings) as client:
        with pytest.raises(RateLimitExceeded):
            await client.get_json(f"{BASE_URL}/repos/a/b")


async def test_403_without_rate_limit_raises_forbidden(
    settings: Settings, api_mock
) -> None:
    api_mock.get(f"{BASE_URL}/repos/a/b").mock(
        return_value=httpx.Response(
            403,
            json={"message": "You are not allowed to access this."},
            headers={"x-ratelimit-remaining": "4999"},
        )
    )
    async with make_client(settings) as client:
        with pytest.raises(GitHubForbiddenError):
            await client.get_json(f"{BASE_URL}/repos/a/b")


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------

async def test_429_retried_then_success(settings: Settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/repos/a/b").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0.05"}),
            httpx.Response(200, json={"id": 1}),
        ]
    )
    async with make_client(settings) as client:
        data = await client.get_json(f"{BASE_URL}/repos/a/b")
    assert data["id"] == 1
    assert len(api_mock.calls) == 2


async def test_5xx_retried_then_success(settings: Settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/repos/a/b").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"id": 2})]
    )
    async with make_client(settings) as client:
        data = await client.get_json(f"{BASE_URL}/repos/a/b")
    assert data["id"] == 2


async def test_transport_error_retried(settings: Settings, api_mock) -> None:
    route = api_mock.get(f"{BASE_URL}/repos/a/b").mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json={"id": 3}),
        ]
    )
    async with make_client(settings) as client:
        data = await client.get_json(f"{BASE_URL}/repos/a/b")
    assert data["id"] == 3
    assert len(route.calls) == 2


async def test_repeated_transport_errors_raise_network_error(
    settings: Settings, api_mock
) -> None:
    api_mock.get(f"{BASE_URL}/repos/a/b").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.ConnectError("boom")]
    )
    from github_radar.github import GitHubNetworkError

    async with make_client(settings) as client:
        with pytest.raises(GitHubNetworkError):
            await client.get_json(f"{BASE_URL}/repos/a/b")


# ---------------------------------------------------------------------------
# Rate-limit pause threshold
# ---------------------------------------------------------------------------

async def test_pause_threshold_refuses_further_requests() -> None:
    settings = Settings(
        github_token="t",
        database_url="postgresql+asyncpg://u:p@localhost/a",
        rate_limit_pause_threshold=50,
        http_max_retries=1,
    )
    import respx

    with respx.mock:
        route = respx.get(f"{BASE_URL}/repos/a/b").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"id": 1},
                    headers={"x-ratelimit-remaining": "20", "x-ratelimit-reset": "9999999999"},
                ),
                httpx.Response(200, json={"id": 2}),
            ]
        )
        async with GitHubClient(settings) as client:
            await client.get_json(f"{BASE_URL}/repos/a/b")
            with pytest.raises(RateLimitExceeded):
                await client.get_json(f"{BASE_URL}/repos/a/b")
        assert len(route.calls) == 1


async def test_get_rate_limits_parses_body(settings: Settings, api_mock) -> None:
    api_mock.get(f"{BASE_URL}/rate_limit").mock(
        return_value=httpx.Response(
            200,
            json={
                "resources": {
                    "core": {"limit": 5000, "remaining": 4000, "used": 1000, "reset": 2000000000},
                    "search": {"limit": 30, "remaining": 25, "used": 5, "reset": 2000000000},
                }
            },
        )
    )
    async with make_client(settings) as client:
        states = await client.get_rate_limits()
    assert states["core"].limit == 5000
    assert states["core"].remaining == 4000
    assert states["search"].limit == 30
    assert states["core"].reset_at is not None
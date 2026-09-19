"""Asynchronous GitHub REST API client.

Responsibilities:

* authentication (``Authorization: Bearer`` header);
* per-resource rate-limit tracking (``core``, ``search``) from response headers;
* refusing to fire further requests when remaining quota is dangerously low;
* bounded retries for transient failures (429 with short ``Retry-After``,
  5xx and transport errors) and fail-fast behaviour for 4xx errors;
* page-based pagination honouring the ``Link`` header.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from github_radar.config import Settings, SettingsError
from github_radar.github.errors import (
    GitHubAPIError,
    GitHubConfigurationError,
    GitHubError,
    GitHubNetworkError,
    RateLimitExceeded,
    classify_error,
)

logger = logging.getLogger(__name__)

# Header names used for rate-limit discovery.
HDR_RESOURCE = "x-ratelimit-resource"
HDR_LIMIT = "x-ratelimit-limit"
HDR_REMAINING = "x-ratelimit-remaining"
HDR_RESET = "x-ratelimit-reset"
HDR_RETRY_AFTER = "retry-after"

# Hard cap on seconds we are willing to sleep waiting for a 429 to pass.
_MAX_RETRY_AFTER_SECONDS = 120.0

_BACKOFF_BASE_SECONDS = 0.5

_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class RateLimitState:
    """The most recently observed rate-limit status for one resource."""

    __slots__ = ("resource", "limit", "remaining", "reset_at")

    def __init__(
        self,
        resource: str,
        limit: int | None = None,
        remaining: int | None = None,
        reset_at: datetime | None = None,
    ) -> None:
        self.resource = resource
        self.limit = limit
        self.remaining = remaining
        self.reset_at = reset_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "limit": self.limit,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
            "reset_at_iso": self.reset_at.isoformat() if self.reset_at else None,
        }


@dataclass(frozen=True)
class _FailureDecision:
    """What the client should do after a non-success response."""

    error: GitHubError | None = None
    retry_delay: float | None = None


def _should_retry(error: _FailureDecision | None) -> bool:
    return error is None or error.error is None


class GitHubClient:
    """Thin, well-behaved HTTP client for the GitHub REST API."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = settings.github_api_base_url.rstrip("/")
        self._pause_threshold = settings.rate_limit_pause_threshold
        self._max_retries = settings.http_max_retries
        self._timeout = settings.http_timeout_seconds

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": settings.github_api_version,
            "User-Agent": "github-radar/0.1",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        self._transport = transport
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self._timeout),
            transport=transport,
        )
        # Per-resource rate-limit state: {"core": ...}, {"search": ...}
        self._limits: dict[str, RateLimitState] = {}

    def require_auth(self) -> None:
        """Fail fast (and loudly) when no token is configured."""
        try:
            self.settings.require_github_token()
        except SettingsError as exc:
            raise GitHubConfigurationError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def rate_limits(self) -> dict[str, RateLimitState]:
        """Snapshot of per-resource rate-limit state."""
        return {name: state for name, state in self._limits.items()}

    def _resource_for_url(self, url: str) -> str:
        path = urlsplit(url).path
        # GitHub reports the search endpoints under the "search" bucket.
        return "search" if "/search/" in path else "core"

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = await self.request("GET", url, params=params, headers=headers)
        return response.json()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Perform a request, applying rate-limit and retry rules.

        Raises :class:`GitHubAPIError` subclasses on API errors and
        :class:`RateLimitExceeded` when quota is exhausted or too low.
        """
        resource = self._resource_for_url(url)
        self._check_pause_threshold(resource)

        last_transport_error: httpx.TransportError | None = None

        for attempt in range(max(1, self._max_retries)):
            try:
                response = await self._client.request(
                    method, url, params=params, json=json, headers=headers
                )
            except httpx.TransportError as exc:
                last_transport_error = exc
                await self._backoff(attempt)
                continue

            self._record_rate_limits(response, resource)

            if response.status_code < 300:
                return response

            decision = self._classify_failure(response, resource)
            if decision is None:
                # Transient 5xx: retry with exponential backoff.
                await self._backoff(attempt)
                continue
            if decision.error is not None:
                raise decision.error
            await asyncio.sleep(decision.retry_delay or self._backoff_seconds(attempt))
            # fall through and retry

        raise GitHubNetworkError(
            f"Request to {url} failed after {max(1, self._max_retries)} attempts"
            f" due to transport errors or repeated 5xx responses"
            f" (last error: {last_transport_error})"
        )

    async def paginate(
        self,
        url: str,
        *,
        per_page: int = 100,
        params: dict[str, Any] | None = None,
        max_items: int | None = None,
        items_key: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield item dicts from a paginated endpoint (page based).

        Plain list endpoints (contributors, ...) return a JSON array. Search
        endpoints return an object whose array lives under ``items_key``
        (e.g. ``"items"``). Pagination follows the ``Link`` header and stops
        when it is absent or a page comes back short.
        """
        page = 1
        fetched = 0
        next_url: str | None = url
        while True:
            if max_items is not None and fetched >= max_items:
                return
            query = dict(params or {})
            query["per_page"] = per_page
            query["page"] = page
            assert next_url is not None
            response = await self.request("GET", next_url, params=query)

            body = response.json()
            if items_key is None:
                items = body
            else:
                payload = body.get(items_key) if isinstance(body, dict) else None
                if not isinstance(payload, list):
                    raise GitHubAPIError(
                        f"Expected a JSON array under key {items_key!r} from a "
                        "paginated endpoint",
                        status_code=response.status_code,
                        url=url,
                        response_body=response.text,
                    )
                items = payload
            if not isinstance(items, list):
                raise GitHubAPIError(
                    "Expected a JSON array from a paginated endpoint",
                    status_code=response.status_code,
                    url=url,
                    response_body=response.text,
                )
            for item in items:
                if max_items is not None and fetched >= max_items:
                    return
                yield item
                fetched += 1

            header_next = _next_link(response.headers.get("link") or "")
            if header_next is None or not items or len(items) < per_page:
                return
            next_url = header_next
            page += 1

    async def get_rate_limits(self) -> dict[str, RateLimitState]:
        """Query the dedicated rate-limit endpoint (``GET /rate_limit``)."""
        data = await self.get_json(f"{self.base_url}/rate_limit")
        resources = data.get("resources") or {}
        states: dict[str, RateLimitState] = {}
        for name, payload in resources.items():
            states[name] = RateLimitState(
                resource=name,
                limit=payload.get("limit"),
                remaining=payload.get("remaining"),
                reset_at=datetime.fromtimestamp(
                    payload.get("reset", 0), tz=UTC
                ),
            )
        return states

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_pause_threshold(self, resource: str) -> None:
        state = self._limits.get(resource)
        if state is None or state.remaining is None:
            return
        now = datetime.now(UTC)
        if state.remaining <= self._pause_threshold and (
            state.reset_at is None or state.reset_at > now
        ):
            raise RateLimitExceeded(
                resource=resource,
                remaining=state.remaining,
                reset_at=state.reset_at,
                message=(
                    f"Remaining {resource} quota ({state.remaining}) is at or "
                    f"below the pause threshold ({self._pause_threshold}); "
                    "refusing to continue until the window resets."
                ),
            )

    def _record_rate_limits(self, response: httpx.Response, resource: str) -> None:
        headers = response.headers
        remaining = _parse_int_header(headers.get(HDR_REMAINING))
        if remaining is None:
            return
        reset = _parse_int_header(headers.get(HDR_RESET))
        reset_at = datetime.fromtimestamp(reset, tz=UTC) if reset else None
        limit = _parse_int_header(headers.get(HDR_LIMIT))
        current = self._limits.get(resource)
        if current is None:
            current = RateLimitState(resource)
            self._limits[resource] = current
        current.limit = limit
        current.remaining = remaining
        current.reset_at = reset_at

    def _classify_failure(
        self, response: httpx.Response, resource: str
    ) -> _FailureDecision | None:
        """Return what to do with a non-success response.

        ``None`` means "retry with default backoff". A :class:`_FailureDecision`
        either carries an error to raise or an explicit retry delay.
        """
        status = response.status_code

        if status == 429:
            retry_after = _parse_float_header(response.headers.get(HDR_RETRY_AFTER))
            seconds = _seconds_until_reset(response)
            wait = min(retry_after or seconds, _MAX_RETRY_AFTER_SECONDS)
            if wait <= 0:
                return _FailureDecision(error=self._rate_limit_exceeded(
                    response, resource, retry_after
                ))
            logger.warning(
                "GitHub 429 on %s; retrying in %.1fs", response.request.url, wait
            )
            return _FailureDecision(retry_delay=wait)

        if status == 403 and _is_rate_limit_response(response):
            return _FailureDecision(
                error=self._rate_limit_exceeded(response, resource, None)
            )

        if status in {500, 502, 503, 504}:
            logger.warning("GitHub %s on %s; retrying", status, response.request.url)
            return None

        return _FailureDecision(
            error=classify_error(
                status_code=status,
                url=str(response.request.url),
                response_body=response.text,
                is_rate_limit=False,
            )
        )

    def _rate_limit_exceeded(
        self,
        response: httpx.Response,
        resource: str,
        retry_after: float | None,
    ) -> RateLimitExceeded:
        reset = _parse_int_header(response.headers.get(HDR_RESET))
        reset_at = datetime.fromtimestamp(reset, tz=UTC) if reset else None
        remaining = _parse_int_header(response.headers.get(HDR_REMAINING))
        logger.warning(
            "GitHub rate limit exceeded for %s (remaining=%s); pausing.",
            resource,
            remaining,
        )
        return RateLimitExceeded(
            resource=resource,
            remaining=remaining,
            reset_at=reset_at,
            retry_after_seconds=retry_after,
        )

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(self._backoff_seconds(attempt))

    def _backoff_seconds(self, attempt: int) -> float:
        return float(_BACKOFF_BASE_SECONDS * (2**attempt))


def _parse_int_header(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_float_header(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _seconds_until_reset(response: httpx.Response) -> float:
    reset = _parse_int_header(response.headers.get(HDR_RESET))
    if reset is None or reset <= 0:
        return 0.0
    now = int(datetime.now(UTC).timestamp())
    return max(0.0, float(reset - now))


def _is_rate_limit_response(response: httpx.Response) -> bool:
    remaining = _parse_int_header(response.headers.get(HDR_REMAINING))
    if remaining is not None and remaining == 0:
        return True
    body = response.text or ""
    return "rate limit exceeded" in body.lower()


def _next_link(link_header: str) -> str | None:
    for match in _LINK_NEXT_RE.finditer(link_header):
        return match.group(1)
    return None


__all__ = ["GitHubClient", "RateLimitState"]
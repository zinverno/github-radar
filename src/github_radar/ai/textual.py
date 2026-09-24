"""Bounded textual evidence from the GitHub API, with a TTL cache.

The AI layer re-reads README / release / commit text only when the analytics
need it and only once per TTL window — a plain in-memory cache with three
counters the ``ai status`` command reports:

``github_evidence_requests``
    git hub calls actually scheduled (misses + hits),
``evidence_cache_hits`` / ``evidence_cache_misses``
    the split between served-from-cache and fetched from GitHub.

All text is normalized and truncated here, before it ever reaches a prompt.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from github_radar.ai.safety import normalize_excerpt
from github_radar.github.client import GitHubClient
from github_radar.github.errors import GitHubError
from github_radar.github.repositories import (
    get_commits,
    get_readme,
    get_releases,
)

logger = logging.getLogger(__name__)

TEXTUAL_TTL_SECONDS = 3600.0
MAX_README_CHARS = 9000
MAX_RELEASE_BODY_CHARS = 2000
MAX_COMMIT_MESSAGE_CHARS = 400
MAX_RELEASES = 5
MAX_COMMITS = 20


@dataclass(frozen=True)
class _CacheEntry:
    fetched_at: datetime
    value: str


class TextualEvidence:
    """TTL-cached, bounded text fetches for one GitHub client."""

    def __init__(
        self,
        client: GitHubClient,
        *,
        ttl_seconds: float = TEXTUAL_TTL_SECONDS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._ttl = timedelta(seconds=ttl_seconds)
        self._now = now or (lambda: datetime.now(UTC))
        self._cache: dict[tuple[str, str, str], _CacheEntry] = {}
        self.requests = 0
        self.hits = 0
        self.misses = 0

    def counters(self) -> dict[str, int]:
        """The ``ai status`` counters (requests/hits/misses)."""
        return {
            "github_evidence_requests": self.requests,
            "evidence_cache_hits": self.hits,
            "evidence_cache_misses": self.misses,
        }

    async def readme(self, owner: str, name: str) -> str:
        return await self._cached("readme", owner, name, self._fetch_readme)

    async def releases(self, owner: str, name: str) -> str:
        return await self._cached("releases", owner, name, self._fetch_releases)

    async def commits(self, owner: str, name: str) -> str:
        return await self._cached("commits", owner, name, self._fetch_commits)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _cached(
        self,
        kind: str,
        owner: str,
        name: str,
        fetch: Callable[[str, str], Awaitable[str]],
    ) -> str:
        """Serve from cache within the TTL, otherwise fetch once."""
        self.requests += 1
        key = (kind, owner, name)
        entry = self._cache.get(key)
        if entry is not None and (self._now() - entry.fetched_at) < self._ttl:
            self.hits += 1
            return entry.value
        self.misses += 1
        try:
            value = await fetch(owner, name)
        except GitHubError as exc:
            logger.warning(
                "Textual evidence fetch failed for %s/%s (%s): %s; using empty.",
                owner,
                name,
                kind,
                exc,
            )
            value = ""
        self._cache[key] = _CacheEntry(fetched_at=self._now(), value=value)
        return value

    async def _fetch_readme(self, owner: str, name: str) -> str:
        text = await get_readme(self._client, owner, name)
        return normalize_excerpt(text, max_chars=MAX_README_CHARS)

    async def _fetch_releases(self, owner: str, name: str) -> str:
        releases = await get_releases(
            self._client, owner, name, limit=MAX_RELEASES
        )
        lines: list[str] = []
        for release in releases:
            tag = str(release.get("tag_name") or release.get("name") or "?")
            published = str(release.get("published_at") or "unknown date")
            body = normalize_excerpt(
                str(release.get("body") or ""), max_chars=MAX_RELEASE_BODY_CHARS
            )
            lines.append(f"release {tag} (published {published}):\n{body}")
        return "\n\n".join(lines) or ""

    async def _fetch_commits(self, owner: str, name: str) -> str:
        commits = await get_commits(self._client, owner, name, limit=MAX_COMMITS)
        lines: list[str] = []
        for commit in commits:
            sha = str(commit.get("sha") or "?")
            message = normalize_excerpt(
                str(commit.get("message") or ""),
                max_chars=MAX_COMMIT_MESSAGE_CHARS,
            )
            date_value = commit.get("date")
            when = (
                str(date_value.split("T")[0]) if isinstance(date_value, str) else "?"
            )
            lines.append(f"{when} {sha}: {message}")
        return "\n".join(lines) or ""


__all__ = [
    "MAX_COMMITS",
    "MAX_README_CHARS",
    "MAX_RELEASES",
    "TEXTUAL_TTL_SECONDS",
    "TextualEvidence",
]
"""Canonical domain models shared by the API and storage layers.

These are intentionally decoupled from both the GitHub JSON payloads and the
database schema. The GitHub layer produces them, the storage layer consumes
them, and analytics/CLI read them.

Counters (stars, forks, ...) are only carried as *current observations* so the
service layer can build snapshots; they are never stored on the repository row
itself — the newest snapshot is the source of truth for current counters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def now_utc() -> datetime:
    import datetime as dt

    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True, slots=True)
class Topic:
    """A single normalized topic name."""

    name: str


@dataclass(frozen=True, slots=True)
class Developer:
    """A GitHub user/developer as observed through the public API."""

    github_id: int
    login: str
    name: str | None = None
    avatar_url: str | None = None
    html_url: str | None = None
    bio: str | None = None
    company: str | None = None
    location: str | None = None
    blog: str | None = None
    public_email: str | None = None
    twitter_username: str | None = None
    followers: int | None = None
    following: int | None = None
    public_repos: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Contributor:
    """A developer plus their cumulative contribution count for one repo.

    GitHub's ``/contributors`` endpoint reports *cumulative* contribution
    counts over the whole history of the repository, not recent activity.
    Do not interpret ``contributions`` as "commits in the last 7 days".
    """

    developer: Developer
    contributions: int


@dataclass(frozen=True, slots=True)
class DeveloperSnapshot:
    """An append-only observation of a developer's public profile counters.

    ``captured_at`` marks when we fetched the profile, not any GitHub-supplied
    timestamp. Counters are the *observed* values at that instant; profile
    history is the raw source of truth for follower/repo-count deltas. The
    current :class:`Developer` row may hold the latest values for convenience,
    but historical rows are never overwritten.

    Only public, non-inferred counters are stored here.
    """

    developer_id: int
    captured_at: datetime
    followers: int | None
    following: int | None
    public_repos: int | None


@dataclass(frozen=True, slots=True)
class ContributorSnapshot:
    """An append-only observation of a developer/repository contribution link.

    The ``contributions`` value is GitHub's *cumulative* contribution count for
    the developer over the whole history of the repository at ``captured_at``.
    A change between two snapshots is a genuine positive/negative delta over the
    window; a single snapshot is never "recent activity".
    """

    repository_id: int
    developer_id: int
    captured_at: datetime
    contributions: int


@dataclass(frozen=True, slots=True)
class PublicContactMethods:
    """The public contact channels a developer has exposed on GitHub.

    Built **only** from fields the GitHub profile API returns on purpose:
    their GitHub profile URL, the public email field, the blog/website field and
    the twitter username. Nothing is scraped, inferred or extrapolated here, and
    private contact data never enters this structure.
    """

    github: str | None
    public_email: str | None
    website: str | None
    twitter: str | None

    @property
    def available(self) -> tuple[str, ...]:
        """Names of the contact channels that are actually exposed."""
        return tuple(
            name
            for name, value in (
                ("github", self.github),
                ("public_email", self.public_email),
                ("website", self.website),
                ("twitter", self.twitter),
            )
            if value is not None and value.strip()
        )

    @classmethod
    def from_developer(cls, developer: Developer) -> PublicContactMethods:
        github = developer.html_url
        if github is None or not github.strip():
            github = f"https://github.com/{developer.login}"
        return cls(
            github=github,
            public_email=developer.public_email,
            website=developer.blog,
            twitter=developer.twitter_username,
        )


@dataclass(frozen=True, slots=True)
class Repository:
    """A repository observed through the GitHub API."""

    github_id: int
    node_id: str | None
    owner_login: str
    owner_github_id: int | None
    name: str
    full_name: str
    description: str | None
    html_url: str | None
    homepage: str | None
    default_branch: str | None
    primary_language: str | None
    is_fork: bool
    is_archived: bool
    is_disabled: bool
    is_template: bool | None
    visibility: str | None
    created_at: datetime | None
    updated_at: datetime | None
    pushed_at: datetime | None
    # Current observations (used to build the first snapshot).
    stars: int
    forks: int
    watchers: int | None
    open_issues: int | None
    size_kb: int | None
    topics: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    """An append-only observation of a repository's counters.

    ``captured_at`` marks when we fetched the state, not the repository's own
    timestamps.
    """

    captured_at: datetime
    stars: int
    forks: int
    watchers: int | None
    open_issues: int | None
    size_kb: int | None
    pushed_at: datetime | None

    def same_counters(self, other: RepositorySnapshot) -> bool:
        return (
            self.stars == other.stars
            and self.forks == other.forks
            and self.watchers == other.watchers
            and self.open_issues == other.open_issues
            and self.size_kb == other.size_kb
            and self.pushed_at == other.pushed_at
        )

    @classmethod
    def from_repository(
        cls, repo: Repository, captured_at: datetime
    ) -> RepositorySnapshot:
        return cls(
            captured_at=captured_at,
            stars=repo.stars,
            forks=repo.forks,
            watchers=repo.watchers,
            open_issues=repo.open_issues,
            size_kb=repo.size_kb,
            pushed_at=repo.pushed_at,
        )


__all__ = [
    "Contributor",
    "ContributorSnapshot",
    "Developer",
    "DeveloperSnapshot",
    "PublicContactMethods",
    "Repository",
    "RepositorySnapshot",
    "Topic",
    "now_utc",
]
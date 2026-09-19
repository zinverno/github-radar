"""Pydantic models mirroring the GitHub REST API JSON payloads.

Only the fields we actually use are modelled. Every model exposes a
``to_domain()`` method producing a canonical :mod:`github_radar.domain`
object.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from github_radar.domain import Developer, Repository


class GithubOwner(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    github_id: int = Field(alias="id")
    login: str
    avatar_url: str | None = None
    html_url: str | None = None
    type: str | None = None


class GithubRepository(BaseModel):
    """A repository object, as returned by REST ``/repos/*`` and search."""

    model_config = ConfigDict(populate_by_name=True)

    github_id: int = Field(alias="id")
    node_id: str | None = None
    owner: GithubOwner
    name: str
    full_name: str
    description: str | None = None
    html_url: str | None = None
    homepage: str | None = None
    default_branch: str | None = None
    language: str | None = None
    fork: bool = False
    archived: bool = False
    disabled: bool = False
    is_template: bool | None = None
    visibility: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    pushed_at: datetime | None = None
    stargazers_count: int = 0
    forks_count: int = 0
    watchers_count: int | None = None
    subscribers_count: int | None = None
    open_issues_count: int | None = None
    size: int | None = None
    topics: list[str] = Field(default_factory=list)

    def to_domain(self) -> Repository:
        # On search results GitHub reports watchers == stargazers; subscribers
        # (true watchers) are only present on the detail endpoint.
        watchers = self.subscribers_count
        if watchers is None and self.watchers_count is not None:
            watchers = self.watchers_count
        return Repository(
            github_id=self.github_id,
            node_id=self.node_id,
            owner_login=self.owner.login,
            owner_github_id=self.owner.github_id,
            name=self.name,
            full_name=self.full_name,
            description=self.description,
            html_url=self.html_url,
            homepage=self.homepage,
            default_branch=self.default_branch,
            primary_language=self.language,
            is_fork=self.fork,
            is_archived=self.archived,
            is_disabled=self.disabled,
            is_template=self.is_template,
            visibility=self.visibility,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pushed_at=self.pushed_at,
            stars=self.stargazers_count,
            forks=self.forks_count,
            watchers=watchers,
            open_issues=self.open_issues_count,
            size_kb=self.size,
            topics=tuple(self.topics),
        )


class GithubSearchResponse(BaseModel):
    """Root object of ``GET /search/repositories``."""

    model_config = ConfigDict(populate_by_name=True)

    total_count: int
    incomplete_results: bool = False
    items: list[GithubRepository] = Field(default_factory=list)


class GithubContributor(BaseModel):
    """An entry of ``GET /repos/{owner}/{repo}/contributors``."""

    model_config = ConfigDict(populate_by_name=True)

    github_id: int = Field(alias="id")
    login: str
    avatar_url: str | None = None
    html_url: str | None = None
    contributions: int = 0


class GithubUser(BaseModel):
    """A GitHub user profile (``GET /users/{login}``)."""

    model_config = ConfigDict(populate_by_name=True)

    github_id: int = Field(alias="id")
    login: str
    name: str | None = None
    avatar_url: str | None = None
    html_url: str | None = None
    bio: str | None = None
    company: str | None = None
    location: str | None = None
    blog: str | None = None
    email: str | None = None
    twitter_username: str | None = None
    followers: int | None = None
    following: int | None = None
    public_repos: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_domain(self) -> Developer:
        return Developer(
            github_id=self.github_id,
            login=self.login,
            name=self.name,
            avatar_url=self.avatar_url,
            html_url=self.html_url,
            bio=self.bio,
            company=self.company,
            location=self.location,
            blog=self.blog,
            public_email=self.email,
            twitter_username=self.twitter_username,
            followers=self.followers,
            following=self.following,
            public_repos=self.public_repos,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class GithubRateLimitResource(BaseModel):
    """A single resource inside ``GET /rate_limit``."""

    limit: int
    used: int
    remaining: int
    reset: int

    @property
    def reset_at(self) -> datetime:
        return datetime.fromtimestamp(self.reset, tz=__import__("datetime").UTC)


class GithubRateLimitResponse(BaseModel):
    """Body of ``GET /rate_limit``."""

    resources: dict[str, GithubRateLimitResource]


def parse_search(data: dict[str, Any]) -> GithubSearchResponse:
    return GithubSearchResponse.model_validate(data)


__all__ = [
    "GithubContributor",
    "GithubOwner",
    "GithubRateLimitResponse",
    "GithubRateLimitResource",
    "GithubRepository",
    "GithubSearchResponse",
    "GithubUser",
    "parse_search",
]
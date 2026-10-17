"""Request-scoped dependencies shared by the API routers.

``create_app`` stores the settings, the async engine and the session factory on
``app.state``; these dependencies expose them to route handlers, so routers stay
free of any global state and tests can swap the engine/session freely.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.ai.cache import SqlArtifactStore
from github_radar.ai.openai_compatible import make_provider
from github_radar.ai.provider import AIProvider
from github_radar.ai.service import RunBudget
from github_radar.config import Settings
from github_radar.github.client import GitHubClient
from github_radar.storage import repositories as storage
from github_radar.util import utcnow


def get_settings(request: Request) -> Settings:
    """The application settings snapshot (from ``create_app``)."""
    return cast(Settings, request.app.state.settings)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A request-scoped database session (committed/closed on completion).

    Explicit AI generation and authenticated workspace edits commit themselves;
    all other routes are read-only.
    """
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


async def reference_now(session: AsyncSession) -> datetime:
    """The deterministic analytics anchor: newest observation, else wall clock.

    Mirrors the CLI so API results are reproducible between runs.
    """
    anchor = await storage.newest_observation_at(session)
    return anchor or utcnow()


def get_ai_provider(settings: Settings = Depends(get_settings)) -> AIProvider:
    """The configured LLM provider (raises when AI is not configured).

    Routes depend on this so tests can override it via
    ``app.dependency_overrides`` without touching settings.
    """
    return make_provider(settings)


def get_artifact_store(session: AsyncSession = Depends(get_session)) -> SqlArtifactStore:
    """The SQL-backed model-result cache."""
    return SqlArtifactStore(session)


def get_run_budget(settings: Settings = Depends(get_settings)) -> RunBudget:
    """One bounded per-request model-call allowance."""
    return RunBudget(total=settings.ai_max_requests_per_run)


def get_github_client(settings: Settings = Depends(get_settings)) -> GitHubClient:
    """A GitHub client used for optional textual evidence.

    Constructed without requiring a token; callers must check
    ``settings.github_token`` before using it.
    """
    return GitHubClient(settings)


__all__ = [
    "get_ai_provider",
    "get_artifact_store",
    "get_github_client",
    "get_run_budget",
    "get_session",
    "get_settings",
    "reference_now",
]

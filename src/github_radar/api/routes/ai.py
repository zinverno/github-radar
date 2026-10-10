"""AI synthesis endpoints.

Reads never touch the model: only ``POST /ai/generate`` consumes budget and
calls the LLM provider. Everything else mirrors the cached, deterministic
Phase 5 layer and is safe to call from a dashboard repeatedly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.ai.cache import SqlArtifactStore
from github_radar.ai.confidence import (
    bridge_level,
    developer_level,
    ecosystem_level,
    repo_level,
    topic_aggregate_level,
)
from github_radar.ai.evidence import (
    ContributorDigest,
    RepositoryDigest,
    TopicRepoDigest,
    bridge_evidence,
    developer_evidence,
    ecosystem_evidence,
    repository_evidence,
    topic_evidence,
)
from github_radar.ai.models import ArtifactType, ConfidenceLevel, EvidenceBundle
from github_radar.ai.prompts import PROMPT_VERSIONS
from github_radar.ai.provider import AIProvider
from github_radar.ai.service import RunBudget, SynthesisRequest, synthesize
from github_radar.ai.textual import TextualEvidence
from github_radar.analytics import RepositoryReport, compute_metrics, compute_momentum
from github_radar.analytics.topics import aggregate_topics
from github_radar.analytics.trends import classify_trend
from github_radar.api.dataset import (
    TrackedRepository,
    collect_repositories,
    developer_dataset_and_reports,
    ecosystem_graph,
    trend_for,
)
from github_radar.api.deps import (
    get_ai_provider,
    get_artifact_store,
    get_github_client,
    get_run_budget,
    get_session,
    get_settings,
    reference_now,
)
from github_radar.api.errors import BadRequestError, NotFoundError, UnavailableError
from github_radar.api.pagination import MAX_LIMIT
from github_radar.api.serializers import serialize_artifact
from github_radar.api.window import parse_window_days
from github_radar.config import Settings
from github_radar.graph.reports import (
    build_cross_topic_bridge_report,
    build_developer_report,
    build_ecosystem_report,
    build_topic_report,
)
from github_radar.storage import ai_artifacts
from github_radar.storage import repositories as storage
from github_radar.storage.ai_artifacts import serialize_evidence
from github_radar.util import utcnow

router = APIRouter()

_ENTITY_TYPES = ("repository", "topic", "developer", "ecosystem", "bridge")

_DEFAULT_ARTIFACT_TYPE = {
    "repository": "repository-summary",
    "topic": "topic-summary",
    "developer": "developer-summary",
    "ecosystem": "ecosystem-summary",
    "bridge": "bridge-summary",
}


@router.get("/ai/status", summary="AI provider configuration and cache state")
async def ai_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Model configuration, per-run limits and how many artifacts are cached."""
    try:
        base_url = settings.require_ai_config()[0]
        configured = True
    except Exception:
        base_url = ""
        configured = False

    from urllib.parse import urlparse

    stats = await SqlArtifactStore(session).stats()
    return {
        "configured": configured,
        "base_url_host": urlparse(base_url).netloc or base_url if configured else None,
        "model": settings.ai_model or None,
        "per_run_request_cap": settings.ai_max_requests_per_run,
        "max_output_tokens": settings.ai_max_output_tokens,
        "max_evidence_chars": settings.ai_max_evidence_chars,
        "cached_artifacts": sum(stats.values()),
        "cached_by_type": dict(sorted(stats.items())),
        "prompt_versions": dict(sorted(PROMPT_VERSIONS.items())),
    }


def _row_to_artifact(row: object) -> dict[str, object]:
    from github_radar.ai.models import AIArtifact
    from github_radar.storage.models import AIArtifactRow

    assert isinstance(row, AIArtifactRow)
    artifact = AIArtifact.model_validate_json(row.result_json)
    return serialize_artifact(artifact)


@router.get("/ai/artifacts", summary="List the most recent cached artifacts")
async def ai_artifacts_list(
    session: Annotated[AsyncSession, Depends(get_session)],
    entity_type: Annotated[str | None, Query()] = None,
    artifact_type: Annotated[str | None, Query()] = None,
    window: Annotated[str | None, Query(pattern=r"^(1|7|30)d?$")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 25,
) -> dict[str, object]:
    """Recent synthesis results, newest first (read-only)."""
    if entity_type is not None and entity_type not in _ENTITY_TYPES:
        raise BadRequestError(
            f"entity_type must be one of {', '.join(_ENTITY_TYPES)}, got {entity_type!r}.",
            details={"entity_type": entity_type},
        )
    window_days = parse_window_days(window) if window is not None else None
    rows = await ai_artifacts.list_artifacts(
        session,
        entity_type=entity_type,
        artifact_type=artifact_type,
        window_days=window_days,
        limit=limit,
    )
    return {
        "items": [_row_to_artifact(row) for row in rows],
        "limit": limit,
        "total": len(rows),
        "truncated": len(rows) >= limit,
    }


@router.get(
    "/ai/artifacts/{entity_type}/{entity_key}",
    summary="Read cached artifacts for one entity (never generates)",
)
async def ai_entity_artifacts(
    entity_type: str,
    entity_key: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    artifact_type: Annotated[str | None, Query()] = None,
    window: Annotated[str | None, Query(pattern=r"^(1|7|30)d?$")] = None,
) -> dict[str, object]:
    """Every cached summary for one entity, newest first.

    This endpoint is strictly read-only: no model call is ever made here.
    ``POST /ai/generate`` is the only way to spend provider budget.
    """
    if entity_type not in _ENTITY_TYPES:
        raise BadRequestError(
            f"entity_type must be one of {', '.join(_ENTITY_TYPES)}, got {entity_type!r}.",
            details={"entity_type": entity_type},
        )
    window_days = parse_window_days(window) if window is not None else None
    rows = await ai_artifacts.list_artifacts(
        session,
        entity_type=entity_type,
        entity_key=entity_key,
        artifact_type=artifact_type,
        window_days=window_days,
        limit=10,
    )
    if not rows and artifact_type is None and window is not None:
        rows = await ai_artifacts.list_artifacts(
            session,
            entity_type=entity_type,
            entity_key=entity_key,
            limit=10,
        )
    if not rows:
        raise NotFoundError(
            f"No cached artifact for {entity_type} {entity_key!r}.",
            details={"entity_type": entity_type, "entity_key": entity_key},
        )
    return {
        "entity_type": entity_type,
        "entity_key": entity_key,
        "items": [_row_to_artifact(row) for row in rows],
        "prompt_versions": dict(sorted(PROMPT_VERSIONS.items())),
    }


@router.post("/ai/generate", summary="Synthesize (or replay) one cached artifact")
async def ai_generate(
    entity_type: Annotated[str, Query()],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    provider: Annotated[AIProvider, Depends(get_ai_provider)],
    store: Annotated[SqlArtifactStore, Depends(get_artifact_store)],
    budget: Annotated[RunBudget, Depends(get_run_budget)],
    client: Annotated[object, Depends(get_github_client)],
    entity_key: Annotated[str | None, Query()] = None,
    artifact_type: Annotated[str | None, Query()] = None,
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    force: Annotated[bool, Query()] = False,
    topic_a: Annotated[str | None, Query()] = None,
    topic_b: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    """Build evidence, call (or replay) the model and persist the artifact.

    The only endpoint that can spend provider budget; all reads use the cache.
    """
    if entity_type not in _ENTITY_TYPES:
        raise BadRequestError(
            f"entity_type must be one of {', '.join(_ENTITY_TYPES)}, got {entity_type!r}.",
            details={"entity_type": entity_type},
        )
    if artifact_type is not None and artifact_type not in PROMPT_VERSIONS:
        raise BadRequestError(
            "artifact_type must be one of "
            f"{', '.join(sorted(PROMPT_VERSIONS))}, got {artifact_type!r}.",
            details={"artifact_type": artifact_type},
        )
    window_days = parse_window_days(window)
    if not settings.ai_api_key or not settings.ai_model:
        raise UnavailableError(
            "AI is not configured: set AI_API_KEY and AI_MODEL (see .env.example).",
            details={"entity_type": entity_type},
        )

    bundle, confidence = await _build_evidence(
        session,
        settings=settings,
        entity_type=entity_type,
        entity_key=entity_key,
        window_days=window_days,
        topic_a=topic_a,
        topic_b=topic_b,
        client=client,
    )
    chosen_type = artifact_type or _DEFAULT_ARTIFACT_TYPE[entity_type]
    request = SynthesisRequest(
        entity_type=bundle.entity_type,
        entity_key=bundle.entity_key,
        artifact_type=cast(ArtifactType, chosen_type),
        window_days=window_days,
        bundle=bundle,
        confidence=cast(ConfidenceLevel, confidence),
        bundle_json=serialize_evidence(bundle),
        provider=provider,
        store=store,
        budget=budget,
        generated_at=utcnow(),
        force=force,
        max_evidence_chars=settings.ai_max_evidence_chars,
    )
    outcome = await synthesize(request)
    await session.commit()
    return {
        "cache_hit": outcome.cache_hit,
        "generated_at": outcome.artifact.generated_at.isoformat(),
        "artifact": serialize_artifact(outcome.artifact),
    }


async def _build_evidence(
    session: AsyncSession,
    *,
    settings: Settings,
    entity_type: str,
    entity_key: str | None,
    window_days: int,
    topic_a: str | None,
    topic_b: str | None,
    client: object,
) -> tuple[EvidenceBundle, str]:
    """Assemble the deterministic evidence bundle and its confidence level."""
    if entity_type == "repository":
        return await _repository_bundle(session, settings, entity_key, window_days, client)
    if entity_type == "topic":
        return await _topic_bundle(session, entity_key, window_days)
    if entity_type == "developer":
        return await _developer_bundle(session, entity_key, window_days)
    if entity_type == "ecosystem":
        return await _ecosystem_bundle(session, window_days)
    return await _bridge_bundle(session, topic_a, topic_b, window_days)


def _require_key(entity_key: str | None, what: str) -> str:
    if not entity_key:
        raise BadRequestError(f"entity_key is required for {what} entities.", details={})
    return entity_key


async def _repository_bundle(
    session: AsyncSession,
    settings: Settings,
    entity_key: str | None,
    window_days: int,
    client: object,
) -> tuple[EvidenceBundle, str]:
    full_name = _require_key(entity_key, "repository").strip("/")
    row = await storage.get_repository_by_full_name(session, full_name)
    if row is None:
        raise NotFoundError(f"{full_name} is not tracked.", details={"entity_key": full_name})

    topics = tuple(
        topic.name for topic in await storage.topics_for_repository(session, row.id)
    )
    contributor_rows = await storage.contributors_for_repository(session, row.id)
    contributors = tuple(
        ContributorDigest(login=dev.login, contributions=contribs)
        for dev, contribs, _last_seen in contributor_rows
    )
    snapshots = await storage.snapshots_for_repository(session, row.id)
    latest = await storage.latest_snapshot(session, row.id)
    metrics = compute_metrics([snapshot.to_domain() for snapshot in snapshots])
    reference_now = latest.captured_at if latest is not None else None
    momentum = (
        compute_momentum(metrics, reference_now=reference_now)
        if reference_now is not None
        else None
    )
    trend_obj = (
        classify_trend(metrics, momentum, reference_now=reference_now)
        if reference_now is not None
        else None
    )

    textual = _textual_evidence(client, settings)
    readme = releases = commits = ""
    if textual is not None:
        try:
            readme = await textual.readme(row.owner_login, row.name)
            releases = await textual.releases(row.owner_login, row.name)
            commits = await textual.commits(row.owner_login, row.name)
        except Exception:
            readme = releases = commits = ""

    digest = RepositoryDigest(
        full_name=row.full_name,
        owner_login=row.owner_login,
        description=row.description,
        homepage=row.homepage,
        primary_language=row.primary_language,
        default_branch=row.default_branch,
        visibility=row.visibility,
        is_fork=row.is_fork,
        is_archived=row.is_archived,
        is_disabled=row.is_disabled,
        is_template=row.is_template,
        github_created_at=row.created_at,
        github_updated_at=row.updated_at,
        pushed_at=row.pushed_at,
        topics=topics,
        contributors=contributors,
        latest_captured_at=reference_now,
        snapshot_count=len(snapshots),
        first_snapshot_at=snapshots[0].captured_at if snapshots else None,
        metrics=metrics,
        momentum=momentum,
        trend=trend_obj,
        textual={"readme": readme, "releases": releases, "commits": commits},
    )
    bundle = repository_evidence(digest, window_days=window_days)
    return bundle, repo_level(metrics, window_days)


def _textual_evidence(client: object, settings: Settings) -> TextualEvidence | None:
    if not settings.github_token:
        return None
    from github_radar.github.client import GitHubClient

    assert isinstance(client, GitHubClient)
    return TextualEvidence(client)


async def _topic_bundle(
    session: AsyncSession,
    entity_key: str | None,
    window_days: int,
) -> tuple[EvidenceBundle, str]:
    topic = storage.normalize_topic(_require_key(entity_key, "topic"))
    tracked = await collect_repositories(session)
    by_topic: dict[str, list[RepositoryReport]] = {}
    for item in tracked:
        for tag in item.topics:
            by_topic.setdefault(storage.normalize_topic(tag), []).append(item.report)
    if topic not in by_topic:
        raise NotFoundError(f"No tracked topic named {topic!r}.", details={"entity_key": topic})
    aggregate = next(
        aggregate
        for aggregate in aggregate_topics(by_topic)
        if aggregate.name == topic
    )

    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    node = graph.topic_by_name(topic)
    graph_report = build_topic_report(graph, node.topic_id) if node is not None else None

    digests = [
        TopicRepoDigest(
            full_name=item.report.full_name,
            stars=item.report.metrics.latest_counts.get("stars"),
            momentum_score=(
                item.report.momentum.score if item.report.momentum is not None else None
            ),
            trend_class=_repo_trend_short(item, reference),
            confidence_level=repo_level(item.report.metrics, window_days),
        )
        for item in tracked
        if topic in {storage.normalize_topic(tag) for tag in item.topics}
    ]
    bundle = topic_evidence(topic, aggregate, digests, graph_report, window_days=window_days)
    return bundle, topic_aggregate_level(aggregate)


def _repo_trend_short(item: TrackedRepository, reference: datetime) -> str | None:
    if item.report.momentum is None:
        return None
    trend = trend_for(item.report, reference)
    return f"{trend.trend} ({trend.label})" if trend is not None else None


async def _developer_bundle(
    session: AsyncSession,
    entity_key: str | None,
    window_days: int,
) -> tuple[EvidenceBundle, str]:
    login = storage.normalize_topic(_require_key(entity_key, "developer"))
    _dataset, reports = await developer_dataset_and_reports(
        session, window_days=window_days
    )
    report = next((r for r in reports if r.login.lower() == login), None)
    if report is None:
        raise NotFoundError(
            f"No tracked developer named {login!r}.", details={"entity_key": login}
        )
    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    node = graph.developer_by_login(report.login)
    graph_report = (
        build_developer_report(
            graph,
            node.developer_id,
            reference_now=reference,
            window_days=window_days,
        )
        if node is not None
        else None
    )
    bundle = developer_evidence(report, graph_report, window_days=window_days)
    return bundle, developer_level(report)


async def _ecosystem_bundle(
    session: AsyncSession,
    window_days: int,
) -> tuple[EvidenceBundle, str]:
    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    report = build_ecosystem_report(
        graph,
        reference_now=reference,
        window_days=window_days,
    )
    bundle = ecosystem_evidence(report, window_days=window_days)
    return bundle, ecosystem_level(report)


async def _bridge_bundle(
    session: AsyncSession,
    topic_a: str | None,
    topic_b: str | None,
    window_days: int,
) -> tuple[EvidenceBundle, str]:
    if not topic_a or not topic_b:
        raise BadRequestError(
            "topic_a and topic_b are required for bridge entities.",
            details={"topic_a": topic_a, "topic_b": topic_b},
        )
    name_a = storage.normalize_topic(topic_a)
    name_b = storage.normalize_topic(topic_b)
    if name_a == name_b:
        raise BadRequestError(
            "topic_a and topic_b must differ.", details={"topic_a": topic_a, "topic_b": topic_b}
        )
    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    for name in (name_a, name_b):
        if graph.topic_by_name(name) is None:
            raise NotFoundError(f"No tracked topic named {name!r}.", details={"name": name})
    report = build_cross_topic_bridge_report(
        graph,
        name_a,
        name_b,
        reference_now=reference,
        window_days=window_days,
    )
    bundle = bridge_evidence(report, window_days=window_days)
    return bundle, bridge_level(report)


__all__ = ["router"]
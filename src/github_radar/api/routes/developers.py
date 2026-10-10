"""Developer listing and detail endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.api.dataset import developer_dataset_and_reports, ecosystem_graph
from github_radar.api.deps import get_session, reference_now
from github_radar.api.errors import NotFoundError
from github_radar.api.pagination import page, parse_pagination
from github_radar.api.serializers import (
    iso,
    serialize_activity,
    serialize_co_contributor,
    serialize_developer_bridge,
    serialize_developer_reach,
    serialize_ecosystem,
    serialize_emerging,
    serialize_profile,
    serialize_topic_relevance,
)
from github_radar.api.window import parse_window_days
from github_radar.graph.reports import build_developer_report
from github_radar.services.intelligence import DeveloperReport
from github_radar.storage import repositories as storage

router = APIRouter()

_SORT_FIELDS = ("relevance", "activity", "ecosystem", "emerging", "followers")

VALID_SORT_HINT = "one of relevance, activity, ecosystem, emerging, followers"


def _normalize_sort(sort: str) -> str:
    if sort not in _SORT_FIELDS:
        from github_radar.api.errors import BadRequestError

        raise BadRequestError(
            f"sort must be {VALID_SORT_HINT}, got {sort!r}.",
            details={"sort": sort, "allowed": list(_SORT_FIELDS)},
        )
    return sort


def _relevance_value(report: DeveloperReport, topic: str | None) -> float | None:
    if topic is not None:
        relevance = report.relevance_for(topic)
        return relevance.score if relevance is not None else None
    scores = [relevance.score for relevance in report.topic_relevances]
    return max(scores) if scores else None


def _activity_score(report: DeveloperReport) -> float:
    activity = report.activity
    if activity is not None and activity.available:
        return activity.score
    return -1.0


def _ecosystem_score(report: DeveloperReport) -> float:
    return report.ecosystem.score if report.ecosystem is not None else -1.0


def _passes_filters(
    report: DeveloperReport,
    *,
    topic: str | None,
    language: str | None,
    repository: str | None,
    min_followers: int | None,
    min_confidence: float | None,
    has_public_contact: bool,
) -> bool:
    if topic is not None and report.relevance_for(topic) is None:
        return False
    if language is not None:
        langs = {lang.lower() for lang in report.languages if lang is not None}
        if language.lower() not in langs:
            return False
    if repository is not None:
        wanted = repository.lower()
        if not any(wanted in name.lower() for name in report.associated_repositories):
            return False
    if min_followers is not None:
        if report.followers is None or report.followers < min_followers:
            return False
    if min_confidence is not None:
        if report.emerging.confidence_score < min_confidence:
            return False
    if has_public_contact and not report.public_contacts.available:
        return False
    return True


def _summary(report: DeveloperReport, *, topic: str | None) -> dict[str, object]:
    return {
        "login": report.login,
        "name": report.name,
        "followers": report.followers,
        "first_seen_at": iso(report.first_seen_at),
        "associated_repositories": report.associations,
        "languages": sorted(lang for lang in report.languages if lang is not None),
        "relevance": _relevance_value(report, topic),
        "activity": serialize_activity(report.activity),
        "ecosystem": serialize_ecosystem(report.ecosystem),
        "emerging": serialize_emerging(report.emerging),
        "best_topic": _best_topic(report),
    }


def _best_topic(report: DeveloperReport) -> dict[str, object] | None:
    candidates = [
        relevance
        for relevance in report.topic_relevances
        if relevance.score is not None
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda relevance: relevance.score or 0.0)
    return {
        "topic": best.topic,
        "score": best.score,
        "confidence": {
            "level": best.confidence_level,
            "score": best.confidence_score,
        },
    }


@router.get("/developers", summary="List tracked developers with intelligence scores")
async def list_developers(
    session: Annotated[AsyncSession, Depends(get_session)],
    topic: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "relevance",
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    repository: Annotated[str | None, Query()] = None,
    language: Annotated[str | None, Query()] = None,
    min_followers: Annotated[int | None, Query(ge=0)] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    has_public_contact: Annotated[bool, Query()] = False,
    limit: Annotated[int | None, Query()] = None,
    offset: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Filter + sort tracked developers, mirroring the ``developers`` CLI."""
    _normalize_sort(sort)
    parsed_limit, parsed_offset = parse_pagination(limit, offset)
    window_days = parse_window_days(window)
    _topic = storage.normalize_topic(topic) if topic else None

    _dataset, reports = await developer_dataset_and_reports(
        session, window_days=window_days
    )
    kept = [
        report
        for report in reports
        if _passes_filters(
            report,
            topic=_topic,
            language=language,
            repository=repository,
            min_followers=min_followers,
            min_confidence=min_confidence,
            has_public_contact=has_public_contact,
        )
    ]
    if sort == "followers":
        kept.sort(key=lambda r: (r.followers is not None, r.followers), reverse=True)
    elif sort == "activity":
        kept.sort(key=lambda r: -_activity_score(r))
    elif sort == "ecosystem":
        kept.sort(key=lambda r: -_ecosystem_score(r))
    elif sort == "emerging":
        kept.sort(key=lambda r: -r.emerging.score)
    else:
        kept.sort(
            key=lambda r: -(_relevance_value(r, _topic) or -1.0)
        )

    total = len(kept)
    windowed = kept[parsed_offset : parsed_offset + parsed_limit]
    return page(
        [_summary(report, topic=_topic) for report in windowed],
        limit=parsed_limit,
        offset=parsed_offset,
        total=total,
    )


@router.get("/developers/emerging", summary="List emerging developers")
async def emerging_developers(
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    min_score: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    limit: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Developers classified as EMERGING, ranked by emerging score."""
    parsed_limit, parsed_offset = parse_pagination(limit, offset=0)
    window_days = parse_window_days(window)
    _dataset, reports = await developer_dataset_and_reports(
        session, window_days=window_days
    )
    emerging = [
        report
        for report in reports
        if report.emerging.label == "EMERGING"
        and (min_score is None or report.emerging.score >= min_score)
    ]
    emerging.sort(key=lambda r: r.emerging.score, reverse=True)
    total = len(emerging)
    return page(
        [_summary(report, topic=None) for report in emerging[:parsed_limit]],
        limit=parsed_limit,
        offset=parsed_offset,
        total=total,
    )


@router.get("/developers/high-activity", summary="List high-confidence active developers")
async def high_activity_developers(
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    limit: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Developers whose activity classification is HIGH with HIGH confidence."""
    parsed_limit, parsed_offset = parse_pagination(limit, offset=0)
    window_days = parse_window_days(window)
    _dataset, reports = await developer_dataset_and_reports(
        session, window_days=window_days
    )
    active = [
        report
        for report in reports
        if report.activity is not None
        and report.activity.available
        and report.activity.confidence_level == "HIGH"
    ]
    active.sort(key=lambda r: r.activity.score if r.activity else 0.0, reverse=True)
    total = len(active)
    return page(
        [_summary(report, topic=None) for report in active[:parsed_limit]],
        limit=parsed_limit,
        offset=parsed_offset,
        total=total,
    )


@router.get("/developers/{login}", summary="Full detail for one tracked developer")
async def developer_detail(
    login: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    co_contributor_limit: Annotated[int, Query(ge=1)] = 5,
) -> dict[str, object]:
    """Profile, activity, ecosystem, emerging classification and graph signals."""
    window_days = parse_window_days(window)
    if co_contributor_limit > 20:
        co_contributor_limit = 20
    wanted = storage.normalize_topic(login)
    _dataset, reports = await developer_dataset_and_reports(
        session, window_days=window_days
    )
    report = next((r for r in reports if r.login.lower() == wanted), None)
    if report is None:
        raise NotFoundError(
            f"No tracked developer named {login!r}.",
            details={"login": login},
        )

    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    graph_report = None
    node = graph.developer_by_login(report.login)
    if node is not None:
        graph_report = build_developer_report(
            graph,
            node.developer_id,
            reference_now=reference,
            window_days=window_days,
            co_contributor_limit=co_contributor_limit,
        )

    profile = report.profile
    return {
        **{
            "login": report.login,
            "name": report.name,
            "followers": report.followers,
            "first_seen_at": iso(report.first_seen_at),
            "associated_repositories": report.associations,
            "languages": sorted(lang for lang in report.languages if lang is not None),
        },
        "public_contacts": {
            "available": list(report.public_contacts.available),
            "github": (
                report.public_contacts.github if report.public_contacts.available else None
            ),
            "public_email": (
                report.public_contacts.public_email
                if report.public_contacts.available
                else None
            ),
            "website": (
                report.public_contacts.website if report.public_contacts.available else None
            ),
            "twitter": (
                report.public_contacts.twitter if report.public_contacts.available else None
            ),
        },
        "profile_deltas": serialize_profile(profile),
        "activity": serialize_activity(report.activity),
        "ecosystem": serialize_ecosystem(report.ecosystem),
        "emerging": serialize_emerging(report.emerging),
        "topic_relevances": [
            serialize_topic_relevance(relevance)
            for relevance in sorted(
                report.topic_relevances,
                key=lambda r: r.score or 0.0,
                reverse=True,
            )[:20]
        ],
        "graph": (
            {
                "reach": serialize_developer_reach(graph_report.reach),
                "centrality": graph_report.centrality,
                "co_contributors": [
                    serialize_co_contributor(relationship)
                    for relationship in graph_report.co_contributors
                ],
                "bridge": (
                    serialize_developer_bridge(graph_report.bridge)
                    if graph_report.bridge is not None
                    else None
                ),
            }
            if graph_report is not None
            else None
        ),
    }


__all__ = ["router"]
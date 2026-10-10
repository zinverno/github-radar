"""Bridge intelligence endpoints: who connects which topic ecosystems."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.api.dataset import (
    ecosystem_graph,
    resolve_repository,
)
from github_radar.api.deps import get_session, reference_now
from github_radar.api.errors import NotFoundError
from github_radar.api.pagination import page, parse_pagination
from github_radar.api.serializers import (
    serialize_cross_topic_bridge,
    serialize_developer_bridge,
    serialize_repository_bridge,
)
from github_radar.api.window import parse_window_days
from github_radar.graph.bridges import (
    compute_developer_bridge,
    compute_repository_bridge,
    developer_bridges,
    repository_bridges,
)
from github_radar.graph.reports import build_cross_topic_bridge_report
from github_radar.storage import repositories as storage

router = APIRouter()


@router.get("/bridges", summary="Rank developers who bridge topic ecosystems")
async def developer_bridges_list(
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    topic: Annotated[str | None, Query()] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    limit: Annotated[int | None, Query()] = None,
    offset: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Bounded, explainable developer bridge rankings (see ``bridges develop``)."""
    parsed_limit, parsed_offset = parse_pagination(limit, offset)
    window_days = parse_window_days(window)
    _topic = storage.normalize_topic(topic) if topic else None
    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    if _topic is not None and graph.topic_by_name(_topic) is None:
        raise NotFoundError(f"No tracked topic named {_topic!r}.", details={"topic": topic})

    bridges = developer_bridges(
        graph,
        reference_now=reference,
        window_days=window_days,
        topic=_topic,
        limit=parsed_offset + parsed_limit,
        min_confidence=min_confidence,
    )
    selected = bridges[parsed_offset : parsed_offset + parsed_limit]
    return page(
        [serialize_developer_bridge(bridge) for bridge in selected],
        limit=parsed_limit,
        offset=parsed_offset,
        total=len(bridges),
    )


@router.get("/bridges/repos", summary="Rank repositories as cross-ecosystem bridges")
async def repository_bridges_list(
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    limit: Annotated[int | None, Query()] = None,
    offset: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Repository bridge rankings (see ``bridges repos``)."""
    parsed_limit, parsed_offset = parse_pagination(limit, offset)
    window_days = parse_window_days(window)
    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    bridges = repository_bridges(
        graph,
        reference_now=reference,
        window_days=window_days,
        limit=parsed_offset + parsed_limit,
        min_confidence=min_confidence,
    )
    selected = bridges[parsed_offset : parsed_offset + parsed_limit]
    return page(
        [serialize_repository_bridge(bridge) for bridge in selected],
        limit=parsed_limit,
        offset=parsed_offset,
        total=len(bridges),
    )


@router.get("/bridges/developers/{login}", summary="One developer's bridge explanation")
async def developer_bridge_detail(
    login: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
) -> dict[str, object]:
    """Full, per-component bridge breakdown for one developer."""
    window_days = parse_window_days(window)
    wanted = storage.normalize_topic(login)
    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    node = graph.developer_by_login(wanted)
    if node is None:
        raise NotFoundError(
            f"No tracked developer named {login!r} with bridge evidence.",
            details={"login": login},
        )
    bridge = compute_developer_bridge(
        graph,
        node.developer_id,
        reference_now=reference,
        window_days=window_days,
    )
    return serialize_developer_bridge(bridge)


@router.get("/bridges/repositories/{ref:path}", summary="One repository's bridge explanation")
async def repository_bridge_detail(
    ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
) -> dict[str, object]:
    """The people behind this repository and how they cross ecosystems."""
    window_days = parse_window_days(window)
    row = await resolve_repository(session, ref)
    if row is None:
        raise NotFoundError(f"Repository {ref!r} is not tracked.", details={"ref": ref})
    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    node = graph.repository_by_full_name(row.full_name)
    if node is None:
        raise NotFoundError(
            f"Repository {ref!r} has no bridge signal in the graph.",
            details={"ref": ref, "full_name": row.full_name},
        )
    bridge = compute_repository_bridge(
        graph,
        node.repository_id,
        reference_now=reference,
        window_days=window_days,
    )
    return serialize_repository_bridge(bridge)


@router.get("/bridges/cross-topic", summary="Developers bridging two topic ecosystems")
async def cross_topic_bridges(
    topic_a: Annotated[str, Query()],
    topic_b: Annotated[str, Query()],
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    limit: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Aggregate + top bridges between two topics (see ``bridges cross-topic``)."""
    parsed_limit, _ = parse_pagination(limit, offset=0)
    window_days = parse_window_days(window)
    name_a = storage.normalize_topic(topic_a)
    name_b = storage.normalize_topic(topic_b)
    if name_a == name_b:
        from github_radar.api.errors import BadRequestError

        raise BadRequestError(
            "topic_a and topic_b must differ.",
            details={"topic_a": topic_a, "topic_b": topic_b},
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
        limit=parsed_limit,
    )
    return {
        "topic_a": report.topic_a,
        "topic_b": report.topic_b,
        "bridge_count": report.bridge_count,
        "distinct_bridging_developers": report.distinct_bridging_developers,
        "average_bridge_score": report.average_bridge_score,
        "shared_tracked_repositories": list(report.shared_tracked_repositories),
        "top_bridges": [
            serialize_cross_topic_bridge(bridge) for bridge in report.top_bridges
        ],
    }


__all__ = ["router"]
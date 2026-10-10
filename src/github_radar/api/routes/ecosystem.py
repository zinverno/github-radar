"""Ecosystem graph report, and raw node/edge projections for visualization."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.api.dataset import ecosystem_graph
from github_radar.api.deps import get_session, reference_now
from github_radar.api.pagination import parse_pagination
from github_radar.api.serializers import (
    serialize_ecosystem_report,
    serialize_graph_edges,
    serialize_graph_nodes,
)
from github_radar.api.window import parse_window_days
from github_radar.graph.metrics import (
    developer_centrality,
    repository_centrality,
    topic_centrality,
)
from github_radar.graph.reports import build_ecosystem_report

router = APIRouter()


def _centrality_scores(
    graph: object,
) -> dict[tuple[str, int], tuple[int, float]]:
    """``(node_type, node_id) → (degree, weighted_degree)`` for every node."""
    from github_radar.graph.model import EcosystemGraph

    assert isinstance(graph, EcosystemGraph)
    scores: dict[tuple[str, int], tuple[int, float]] = {}
    for series in (
        developer_centrality(graph),
        repository_centrality(graph),
        topic_centrality(graph),
    ):
        for row in series:
            scores[(row.node_type, row.node_id)] = (row.degree, row.weighted_degree)
    return scores


@router.get("/ecosystem", summary="Aggregate ecosystem graph report")
async def ecosystem_report(
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    centrality_limit: Annotated[int, Query(ge=1)] = 8,
    bridge_limit: Annotated[int, Query(ge=1)] = 5,
) -> dict[str, object]:
    """Sizes, edge counts, connected components, hubs and top bridges."""
    window_days = parse_window_days(window)
    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    report = build_ecosystem_report(
        graph,
        reference_now=reference,
        window_days=window_days,
        centrality_limit=centrality_limit,
        bridge_limit=bridge_limit,
    )
    return serialize_ecosystem_report(report)


@router.get("/ecosystem/nodes", summary="Project the graph nodes (paged)")
async def ecosystem_nodes(
    session: Annotated[AsyncSession, Depends(get_session)],
    node_type: Annotated[
        str | None, Query(pattern=r"^(DEVELOPER|REPOSITORY|TOPIC)$")
    ] = None,
    limit: Annotated[int | None, Query()] = None,
    offset: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Every graph node with its type, label and centrality, paginated.

    Nodes are ordered by the deterministic centrality scores
    (weighted degree descending, then degree, then label), so a view can
    prioritise the most-connected nodes without extra computation.
    """
    parsed_limit, parsed_offset = parse_pagination(limit, offset)
    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    scores = _centrality_scores(graph)

    if node_type is None:
        return serialize_graph_nodes(
            graph,
            limit=parsed_limit,
            offset=parsed_offset,
            scores=scores,
        )

    grouped = _filtered_nodes(graph, node_type, scores=scores)
    total = len(grouped)
    selected = grouped[parsed_offset : parsed_offset + parsed_limit]
    return {
        "items": [
            {
                "node_type": node_type,
                "node_id": node_id,
                "label": label,
                "degree": degree,
                "weighted_degree": weighted_degree,
            }
            for node_type, node_id, label, degree, weighted_degree in selected
        ],
        "limit": parsed_limit,
        "offset": parsed_offset,
        "total": total,
        "returned": len(selected),
        "truncated": parsed_offset + len(selected) < total,
        "node_type": node_type,
    }


def _filtered_nodes(
    graph: object, node_type: str, *, scores: dict[tuple[str, int], tuple[int, float]]
) -> list[tuple[str, int, str, int, float]]:
    from github_radar.graph.model import EcosystemGraph

    assert isinstance(graph, EcosystemGraph)
    rows: list[tuple[str, int, str, int, float]] = []
    for node in graph.nodes():
        if node.node_type == node_type:
            degree, weighted_degree = scores.get(
                (node.node_type, node.node_id), (0, 0.0)
            )
            rows.append((node.node_type, node.node_id, node.label, degree, weighted_degree))
    rows.sort(key=lambda row: (-row[4], -row[3], row[2].lower()))
    return rows


@router.get("/ecosystem/edges", summary="Project the graph edges (paged)")
async def ecosystem_edges(
    session: Annotated[AsyncSession, Depends(get_session)],
    edge_type: Annotated[
        str | None, Query(pattern=r"^(owns|contributes_to|tagged_with)$")
    ] = None,
    limit: Annotated[int | None, Query()] = None,
    offset: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Every graph edge, paginated; contribution edges carry their counts."""
    parsed_limit, parsed_offset = parse_pagination(limit, offset)
    reference = await reference_now(session)
    graph = await ecosystem_graph(session, reference)
    from github_radar.graph.model import EcosystemGraph

    assert isinstance(graph, EcosystemGraph)
    _EDGE_TYPE_TOKENS = {
        "owns": "OWNS",
        "contributes_to": "CONTRIBUTES_TO",
        "tagged_with": "TAGGED_WITH",
    }

    if edge_type is None:
        return serialize_graph_edges(graph, limit=parsed_limit, offset=parsed_offset)

    wanted = _EDGE_TYPE_TOKENS[edge_type]
    all_edges = list(graph.edges())
    filtered = [edge for edge in all_edges if edge.edge_type == wanted]
    total = len(filtered)
    selected = filtered[parsed_offset : parsed_offset + parsed_limit]
    rows = [
        {
            "edge_type": edge_type,
            "source": list(edge.source),
            "target": list(edge.target),
        }
        for edge in selected
    ]
    return {
        "items": rows,
        "limit": parsed_limit,
        "offset": parsed_offset,
        "total": total,
        "returned": len(selected),
        "truncated": parsed_offset + len(selected) < total,
        "edge_type": edge_type,
    }


__all__ = ["router"]
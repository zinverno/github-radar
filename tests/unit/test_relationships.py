"""Sanity tests for developer co-contribution relationships.

Guards the invariant that every result produced by
:func:`find_co_contributors` references at least one real shared tracked
repository, and that results are deterministic and bounded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from github_radar.domain import ContributorSnapshot
from github_radar.graph.confidence import GraphConfidence
from github_radar.graph.model import DeveloperNode, EcosystemGraph, RepositoryNode
from github_radar.graph.relationships import find_co_contributors

REFERENCE = datetime(2026, 2, 1, tzinfo=UTC)


class GraphBuilder:
    def __init__(self) -> None:
        self.graph = EcosystemGraph()

    def add_repo(self, repository_id: int, full_name: str) -> None:
        self.graph.add_repository(
            RepositoryNode(repository_id=repository_id, full_name=full_name)
        )

    def add_dev(self, developer_id: int, login: str) -> None:
        self.graph.add_developer(DeveloperNode(developer_id, login))

    def add_contrib(self, developer_id: int, repository_id: int) -> None:
        self.graph.add_contribution(
            developer_id,
            repository_id,
            contributions=30,
            share=0.5,
            observations=(
                ContributorSnapshot(
                    repository_id=repository_id,
                    developer_id=developer_id,
                    captured_at=REFERENCE - timedelta(days=2),
                    contributions=20,
                ),
                ContributorSnapshot(
                    repository_id=repository_id,
                    developer_id=developer_id,
                    captured_at=REFERENCE - timedelta(days=1),
                    contributions=30,
                ),
            ),
        )


def test_co_contributors_share_a_real_repository() -> None:
    b = GraphBuilder()
    b.add_dev(1, "alice")
    b.add_dev(2, "bob")
    b.add_dev(3, "carol")
    b.add_repo(10, "acme/radar")
    b.add_repo(11, "acme/radar-extra")
    b.add_contrib(1, 10)
    b.add_contrib(2, 10)
    b.add_contrib(3, 10)
    b.add_contrib(1, 11)

    relationships = find_co_contributors(
        b.graph, 1, reference_now=REFERENCE, window_days=7
    )
    assert {item.login for item in relationships} == {"bob", "carol"}
    for item in relationships:
        assert item.shared_repository_count >= 1
        assert all(evidence.repository_id == 10 for evidence in item.shared_repositories)


def test_co_contributor_invariant_every_result_has_shared_repo() -> None:
    b = GraphBuilder()
    b.add_dev(4, "dave")
    b.add_dev(5, "erin")
    b.add_repo(12, "acme/one")
    b.add_repo(13, "acme/two")
    b.add_contrib(4, 12)
    b.add_contrib(5, 13)  # different repository: erin never co-contributes

    relationships = find_co_contributors(
        b.graph, 4, reference_now=REFERENCE, window_days=7
    )
    assert relationships == ()


def test_co_contributors_deterministic_and_bounded() -> None:
    b = GraphBuilder()
    b.add_dev(6, "fay")
    b.add_dev(7, "greg")
    b.add_repo(14, "acme/shared")
    b.add_contrib(6, 14)
    b.add_contrib(7, 14)

    first = find_co_contributors(b.graph, 6, reference_now=REFERENCE, window_days=7)
    second = find_co_contributors(b.graph, 6, reference_now=REFERENCE, window_days=7)
    assert first == second
    assert all(isinstance(item.confidence, GraphConfidence) for item in first)
    assert all(0.0 <= item.strength <= 1.0 for item in first)
    # no self-relationship
    assert all(item.developer_id != 6 for item in first)
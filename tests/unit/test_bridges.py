"""Regression tests for the bridge phase protections.

Every protection pattern from the phase contract is pinned here:

1. A developer with real evidence across multiple topics bridges effectively.
2. One repository carrying many arbitrary tags is not an automatic bridge.
3. A single tiny contribution is never treated as a bridge.
4. Cross-topic bridges require evidence in *both* ecosystems.
5. Followers never enter the score.
6. Scores stay bounded and results are deterministic and sorted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from github_radar.analytics.momentum import MomentumScore
from github_radar.domain import ContributorSnapshot
from github_radar.graph.bridges import (
    BRIDGE_MIN_LIFETIME,
    SMALL_SAMPLE_CAP,
    compute_developer_bridge,
    compute_repository_bridge,
    cross_topic_bridges,
    developer_bridges,
    repository_bridges,
)
from github_radar.graph.model import DeveloperNode, EcosystemGraph, RepositoryNode, TopicNode

REFERENCE = datetime(2026, 2, 1, tzinfo=UTC)


class GraphBuilder:
    """Builds an in-memory :class:`EcosystemGraph` for bridge scenarios."""

    def __init__(self) -> None:
        self.graph = EcosystemGraph()
        self._topic_ids: dict[str, int] = {}
        self._next_topic = 1

    def topic_id(self, name: str) -> int:
        if name not in self._topic_ids:
            self._topic_ids[name] = self._next_topic
            self._next_topic += 1
            if self.graph.topic_by_name(name) is None:
                self.graph.add_topic(TopicNode(self._topic_ids[name], name))
        return self._topic_ids[name]

    def add_repo(
        self,
        repository_id: int,
        full_name: str,
        *,
        topics: tuple[str, ...] = (),
        owner: int | None = None,
        momentum: float = 1.0,
    ) -> None:
        node = RepositoryNode(
            repository_id=repository_id,
            full_name=full_name,
            primary_language="python",
            momentum=MomentumScore(
                score=momentum,
                stars_growth_pct=10.0,
                forks_growth_pct=5.0,
                recency=1.0,
                components={
                    "stars_growth": 0.5,
                    "forks_growth": 0.15,
                    "recency": 1.0,
                },
            ),
            owner_id=owner,
        )
        self.graph.add_repository(node)
        if owner is not None:
            self.graph.add_ownership(owner, repository_id)
        for name in topics:
            self.graph.add_repo_topic(repository_id, self.topic_id(name))

    def add_dev(self, developer_id: int, login: str, *, followers: int = 0) -> None:
        self.graph.add_developer(
            DeveloperNode(developer_id, login, followers=followers)
        )

    def add_contrib(
        self,
        developer_id: int,
        repository_id: int,
        contributions: int,
        *,
        observations: int = 0,
    ) -> None:
        total = 0
        for dev in self.graph.associated_developers(repository_id):
            evidence = self.graph.contribution_evidence(dev, repository_id)
            total += evidence.contributions if evidence is not None else 0
        total += contributions
        share = contributions / total if total > 0 else 0.0
        obs = tuple(
            ContributorSnapshot(
                repository_id=repository_id,
                developer_id=developer_id,
                captured_at=REFERENCE - timedelta(days=n + 1),
                contributions=n + 1,
            )
            for n in range(observations)
        )
        self.graph.add_contribution(
            developer_id,
            repository_id,
            contributions=contributions,
            share=share,
            observations=obs,
        )


def _real_bridge_builder() -> GraphBuilder:
    """Developer with genuine evidence across mcp and browser-agents."""
    b = GraphBuilder()
    b.add_dev(1, "multi-topic", followers=5)
    b.add_repo(11, "acme/mcp-core", topics=("mcp",))
    b.add_repo(12, "acme/mcp-extra", topics=("mcp",))
    b.add_repo(13, "acme/agents-sdk", topics=("browser-agents",))
    b.add_repo(14, "acme/agents-extra", topics=("browser-agents",))
    for repo_id in (11, 12, 13, 14):
        b.add_contrib(1, repo_id, 50)
    return b


def _tag_spam_builder() -> GraphBuilder:
    """Developer whose membership count is a fluke of many arbitrary tags."""
    b = GraphBuilder()
    b.add_dev(2, "tag-spammer", followers=1000)
    tags = (
        "mcp",
        "ai",
        "agents",
        "browser",
        "web",
        "automation",
        "tools",
        "cli",
        "go",
        "rust",
        "python",
        "typescript",
        "javascript",
        "devops",
        "cloud",
        "llm",
        "gpt",
        "token",
        "sdk",
        "api",
        "syntax",
        "hypertext",
        "traversal",
        "parser",
        "runtime",
    )
    b.add_repo(21, "spam/servers", topics=tags, momentum=1.0)
    b.add_contrib(2, 21, BRIDGE_MIN_LIFETIME - 1)
    return b


def test_multi_topic_developer_bridges_effectively() -> None:
    b = _real_bridge_builder()
    bridge = compute_developer_bridge(b.graph, 1, reference_now=REFERENCE, window_days=7)
    assert bridge.bridge_score > SMALL_SAMPLE_CAP
    assert bridge.meaningful_topic_memberships == 2
    assert bridge.distinct_supporting_repositories == 4
    assert bridge.small_sample is False
    assert bridge.missing_components == ("recent_activity",)
    assert {evidence.topic for evidence in bridge.topic_evidence} == {
        "mcp",
        "browser-agents",
    }
    assert set(bridge.repository_evidence) == {
        "acme/mcp-core",
        "acme/mcp-extra",
        "acme/agents-sdk",
        "acme/agents-extra",
    }


def test_many_tags_on_one_repository_is_not_an_automatic_bridge() -> None:
    spam_graph = _tag_spam_builder().graph
    real = _real_bridge_builder()
    spammer = compute_developer_bridge(
        spam_graph, 2, reference_now=REFERENCE, window_days=7
    )
    bridger = compute_developer_bridge(
        real.graph, 1, reference_now=REFERENCE, window_days=7
    )
    assert spammer.meaningful_topic_memberships > bridger.meaningful_topic_memberships
    assert spammer.small_sample is True
    assert spammer.bridge_score <= SMALL_SAMPLE_CAP
    assert spammer.bridge_score < bridger.bridge_score


def test_tiny_contribution_is_capped() -> None:
    b = GraphBuilder()
    b.add_dev(3, "small")
    b.add_repo(31, "x/one", topics=("alpha", "beta"), momentum=1.0)
    b.add_contrib(3, 31, BRIDGE_MIN_LIFETIME - 1)
    bridge = compute_developer_bridge(b.graph, 3, reference_now=REFERENCE, window_days=7)
    assert bridge.small_sample is True
    assert bridge.bridge_score <= SMALL_SAMPLE_CAP


def test_single_topic_membership_is_never_a_bridge() -> None:
    b = GraphBuilder()
    b.add_dev(4, "single-topic")
    b.add_repo(41, "x/a", topics=("mcp",), momentum=1.0)
    b.add_repo(42, "x/b", topics=("mcp",), momentum=1.0)
    b.add_repo(43, "x/c", topics=("mcp",), momentum=1.0)
    for repo_id in (41, 42, 43):
        b.add_contrib(4, repo_id, 50)
    bridge = compute_developer_bridge(b.graph, 4, reference_now=REFERENCE, window_days=7)
    assert bridge.meaningful_topic_memberships == 1
    assert bridge.bridge_score == 0.0


def test_cross_topic_requires_evidence_on_both_sides() -> None:
    b = GraphBuilder()
    b.add_dev(5, "both")
    b.add_dev(6, "only-mcp")
    b.add_repo(51, "y/mcp-repo", topics=("mcp",), momentum=1.0)
    b.add_repo(52, "y/agent-repo", topics=("browser-agents",), momentum=1.0)
    b.add_contrib(5, 51, 60)
    b.add_contrib(5, 52, 60)
    b.add_contrib(6, 51, 60)

    bridges = cross_topic_bridges(
        b.graph, "mcp", "browser-agents", reference_now=REFERENCE, window_days=7
    )
    logins = [bridge.login for bridge in bridges]
    assert "both" in logins
    assert "only-mcp" not in logins

    bridge = next(item for item in bridges if item.login == "both")
    assert bridge.topic_a.topic == "mcp"
    assert bridge.topic_b.topic == "browser-agents"
    assert bridge.topic_a.repository_count == 1
    assert bridge.topic_b.repository_count == 1
    assert bridge.small_sample is False
    assert "mcp" in bridge.topic_a.repositories[0].lower()
    assert "agent" in bridge.topic_b.repositories[0].lower()


def test_cross_topic_tiny_contribution_is_capped() -> None:
    b = GraphBuilder()
    b.add_dev(7, "tiny-both")
    b.add_repo(71, "z/mcp", topics=("mcp",), momentum=1.0)
    b.add_repo(72, "z/agents", topics=("browser-agents",), momentum=1.0)
    b.add_contrib(7, 71, 1)
    b.add_contrib(7, 72, 1)
    bridges = cross_topic_bridges(
        b.graph, "mcp", "browser-agents", reference_now=REFERENCE, window_days=7
    )
    assert bridges
    assert bridges[0].bridge_score <= SMALL_SAMPLE_CAP
    assert bridges[0].small_sample is True


def test_followers_never_enter_the_score() -> None:
    low = GraphBuilder()
    low.add_dev(8, "humble", followers=0)
    low.add_repo(81, "h/mcp", topics=("mcp",), momentum=1.0)
    low.add_repo(82, "h/agents", topics=("browser-agents",), momentum=1.0)
    for repo_id in (81, 82):
        low.add_contrib(8, repo_id, 60)

    famous = GraphBuilder()
    famous.add_dev(9, "famous", followers=2_000_000)
    famous.add_repo(91, "f/mcp", topics=("mcp",), momentum=1.0)
    famous.add_repo(92, "f/agents", topics=("browser-agents",), momentum=1.0)
    for repo_id in (91, 92):
        famous.add_contrib(9, repo_id, 60)

    humble = compute_developer_bridge(
        low.graph, 8, reference_now=REFERENCE, window_days=7
    )
    star = compute_developer_bridge(
        famous.graph, 9, reference_now=REFERENCE, window_days=7
    )
    assert humble.components == star.components
    assert humble.bridge_score == star.bridge_score


def test_followers_without_contribution_are_not_a_bridge() -> None:
    b = GraphBuilder()
    b.add_dev(10, "celebrity", followers=5_000_000)
    b.add_repo(101, "other/repo", topics=("mcp", "browser-agents"), momentum=1.0)
    bridge = compute_developer_bridge(
        b.graph, 10, reference_now=REFERENCE, window_days=7
    )
    assert bridge.bridge_score == 0.0
    assert bridge.meaningful_topic_memberships == 0


def test_scores_are_bounded_and_ranked_deterministically() -> None:
    b = _real_bridge_builder()
    first = developer_bridges(b.graph, reference_now=REFERENCE, window_days=7)
    second = developer_bridges(b.graph, reference_now=REFERENCE, window_days=7)
    assert first == second
    logs = [item.bridge_score for item in first]
    assert logs == sorted(logs, reverse=True)
    assert all(0.0 <= item.bridge_score <= 1.0 for item in first)


def test_developer_bridges_topic_filter() -> None:
    b = GraphBuilder()
    b.add_dev(11, "mcp-only")
    b.add_repo(111, "mcp/repo-a", topics=("mcp",), momentum=1.0)
    b.add_repo(112, "mcp/repo-b", topics=("mcp",), momentum=1.0)
    b.add_contrib(11, 111, 60)
    b.add_contrib(11, 112, 60)
    featured = developer_bridges(
        b.graph, reference_now=REFERENCE, window_days=7, topic="mcp"
    )
    assert [item.login for item in featured] == ["mcp-only"]
    absent = developer_bridges(
        b.graph, reference_now=REFERENCE, window_days=7, topic="browser-agents"
    )
    assert absent == ()


def test_repository_many_tags_does_not_outrank_contributor_ecosystem() -> None:
    b = GraphBuilder()
    tags = tuple(f"tag-{n}" for n in range(25))
    b.add_repo(200, "spam/tag-city", topics=tags, momentum=1.0)
    b.add_repo(201, "real/core", topics=("mcp", "browser-agents"), momentum=1.0)
    for dev in range(20, 40):
        b.add_dev(dev, f"contributor-{dev}")
        b.add_contrib(dev, 201, 30)
        if dev % 2 == 0:
            b.add_repo(300 + dev, f"other/repo-{dev}", topics=("mcp",))
            b.add_contrib(dev, 300 + dev, 30)

    spam = compute_repository_bridge(
        b.graph, 200, reference_now=REFERENCE, window_days=7
    )
    real = compute_repository_bridge(
        b.graph, 201, reference_now=REFERENCE, window_days=7
    )
    assert spam.small_sample is True
    assert spam.bridge_score <= SMALL_SAMPLE_CAP
    assert set(spam.missing_components) >= {"cross_repo_share", "cross_topic_share"}
    assert real.bridge_score > spam.bridge_score
    assert real.cross_repository_contributors > 0
    assert real.cross_topic_contributors > 0


def test_repository_bridge_is_bounded_and_ranked() -> None:
    b = _real_bridge_builder()
    all_scores = repository_bridges(b.graph, reference_now=REFERENCE, window_days=7)
    assert all(0.0 <= item.bridge_score <= 1.0 for item in all_scores)
    scores = [item.bridge_score for item in all_scores]
    assert scores == sorted(scores, reverse=True)


def test_missing_recent_activity_is_missing_not_zero() -> None:
    b = GraphBuilder()
    b.add_dev(12, "no-observations")
    b.add_repo(121, "n/mcp", topics=("mcp",), momentum=1.0)
    b.add_repo(122, "n/agents", topics=("browser-agents",), momentum=1.0)
    b.add_contrib(12, 121, 60)
    b.add_contrib(12, 122, 60)
    bridge = compute_developer_bridge(
        b.graph, 12, reference_now=REFERENCE, window_days=7
    )
    assert "recent_activity" in bridge.missing_components
    assert bridge.components["recent_activity"] == 0.0
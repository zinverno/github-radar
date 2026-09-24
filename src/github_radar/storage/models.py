"""SQLAlchemy 2.x ORM models mapping the PostgreSQL schema.

Convention: table rows are named with a ``Row`` suffix to keep them clearly
distinct from the domain objects in :mod:`github_radar.domain`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from github_radar.domain import ContributorSnapshot, DeveloperSnapshot, RepositorySnapshot


class Base(DeclarativeBase):
    pass


class DeveloperRow(Base):
    __tablename__ = "developers"
    __table_args__ = (
        Index("ix_developers_github_id", "github_id", unique=True),
        Index("ix_developers_login", "login"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    login: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    html_url: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    blog: Mapped[str | None] = mapped_column(Text)
    public_email: Mapped[str | None] = mapped_column(Text)
    twitter_username: Mapped[str | None] = mapped_column(Text)
    followers: Mapped[int | None] = mapped_column(Integer)
    following: Mapped[int | None] = mapped_column(Integer)
    public_repos: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    profile_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepositoryRow(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        Index("ix_repositories_github_id", "github_id", unique=True),
        Index("ix_repositories_full_name", "full_name", unique=True),
        Index("ix_repositories_primary_language", "primary_language"),
        Index("ix_repositories_owner_login", "owner_login"),
        Index("ix_repositories_last_seen_at", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    node_id: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("developers.id", ondelete="SET NULL")
    )
    owner_login: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    html_url: Mapped[str | None] = mapped_column(Text)
    homepage: Mapped[str | None] = mapped_column(Text)
    default_branch: Mapped[str | None] = mapped_column(Text)
    primary_language: Mapped[str | None] = mapped_column(Text)
    is_fork: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_template: Mapped[bool | None] = mapped_column(Boolean)
    visibility: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    owner: Mapped[DeveloperRow | None] = relationship(foreign_keys=[owner_id])
    topics: Mapped[list[TopicRow]] = relationship(
        secondary="repository_topics", back_populates="repositories", lazy="selectin"
    )
    contributors: Mapped[list[ContributorRow]] = relationship(
        back_populates="repository", lazy="selectin"
    )
    snapshots: Mapped[list[SnapshotRow]] = relationship(
        back_populates="repository", order_by="SnapshotRow.captured_at"
    )


class TopicRow(Base):
    __tablename__ = "topics"
    __table_args__ = (Index("ix_topics_name", "name", unique=True),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    repositories: Mapped[list[RepositoryRow]] = relationship(
        secondary="repository_topics", back_populates="topics"
    )


class RepositoryTopicRow(Base):
    __tablename__ = "repository_topics"
    __table_args__ = (
        UniqueConstraint("repository_id", "topic_id", name="uq_repository_topics"),
    )

    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )


class ContributorRow(Base):
    __tablename__ = "repository_contributors"
    __table_args__ = (
        UniqueConstraint(
            "repository_id", "developer_id", name="uq_repository_contributors"
        ),
        Index("ix_repository_contributors_repo_contributions",
              "repository_id", "contributions"),
    )

    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )
    developer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("developers.id", ondelete="CASCADE"), primary_key=True
    )
    contributions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    repository: Mapped[RepositoryRow] = relationship(back_populates="contributors")
    developer: Mapped[DeveloperRow] = relationship(lazy="joined")


class DeveloperSnapshotRow(Base):
    __tablename__ = "developer_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "developer_id",
            "captured_at",
            name="uq_developer_snapshots_dev_captured",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    developer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("developers.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    followers: Mapped[int | None] = mapped_column(Integer)
    following: Mapped[int | None] = mapped_column(Integer)
    public_repos: Mapped[int | None] = mapped_column(Integer)

    developer: Mapped[DeveloperRow] = relationship(lazy="joined")

    def to_domain(self) -> DeveloperSnapshot:
        return DeveloperSnapshot(
            developer_id=self.developer_id,
            captured_at=self.captured_at,
            followers=self.followers,
            following=self.following,
            public_repos=self.public_repos,
        )


class ContributorSnapshotRow(Base):
    __tablename__ = "repository_contributor_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "developer_id",
            "captured_at",
            name="uq_contributor_snapshots_repo_dev_captured",
        ),
        Index(
            "ix_contributor_snapshots_developer_captured",
            "developer_id",
            "captured_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    developer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("developers.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    contributions: Mapped[int] = mapped_column(Integer, nullable=False)

    repository: Mapped[RepositoryRow] = relationship(lazy="joined")
    developer: Mapped[DeveloperRow] = relationship(lazy="joined")

    def to_domain(self) -> ContributorSnapshot:
        return ContributorSnapshot(
            repository_id=self.repository_id,
            developer_id=self.developer_id,
            captured_at=self.captured_at,
            contributions=self.contributions,
        )


class SnapshotRow(Base):
    __tablename__ = "repository_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "captured_at",
            name="uq_repository_snapshots_repo_captured",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    forks: Mapped[int] = mapped_column(Integer, nullable=False)
    watchers: Mapped[int | None] = mapped_column(Integer)
    open_issues: Mapped[int | None] = mapped_column(Integer)
    size_kb: Mapped[int | None] = mapped_column(Integer)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    repository: Mapped[RepositoryRow] = relationship(back_populates="snapshots")

    def to_domain(self) -> RepositorySnapshot:
        return RepositorySnapshot(
            captured_at=self.captured_at,
            stars=self.stars,
            forks=self.forks,
            watchers=self.watchers,
            open_issues=self.open_issues,
            size_kb=self.size_kb,
            pushed_at=self.pushed_at,
        )


class AIArtifactRow(Base):
    """A persisted AI synthesis artifact (the Phase 5 model-result cache).

    One row per (entity, artifact type, window, evidence fingerprint, prompt
    version, model) — see the ``uq_ai_artifacts_cache_key`` constraint. The
    fingerprint makes the cache key *data-sensitive*: as soon as the relevant
    evidence changes the key changes, so an old summary can never masquerade
    as current. ``result_json``/``evidence_json`` are immutable snapshot blobs.
    """

    __tablename__ = "ai_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_key",
            "artifact_type",
            "window_days",
            "source_fingerprint",
            "prompt_version",
            "model",
            name="uq_ai_artifacts_cache_key",
        ),
        Index("ix_ai_artifacts_entity", "entity_type", "entity_key"),
        Index("ix_ai_artifacts_artifact_type", "artifact_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_key: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_type: Mapped[str] = mapped_column(Text, nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)


__all__ = [
    "AIArtifactRow",
    "Base",
    "ContributorRow",
    "ContributorSnapshotRow",
    "DeveloperRow",
    "DeveloperSnapshotRow",
    "RepositoryRow",
    "RepositoryTopicRow",
    "SnapshotRow",
    "TopicRow",
]
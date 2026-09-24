"""The model-result cache (the ``ai_artifacts`` table plus an in-memory twin).

Why a cache at all: model calls cost money and are non-deterministic, but the
*evidence* they summarize is deterministic and fingerprint-addressable. An
artifact is therefore keyed by (entity, artifact type, window, evidence
fingerprint, prompt version, model) and replayed when the evidence has not
changed. ``--force`` bypasses this cache on the CLI.

Two store implementations share one protocol:

* :class:`SqlArtifactStore` — production, backed by PostgreSQL;
* :class:`MemoryArtifactStore` — tests and any tooling that wants a fast,
  in-process cache.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.ai.models import AIArtifact, ArtifactKey
from github_radar.storage.ai_artifacts import (
    StoredArtifact,
)
from github_radar.storage.ai_artifacts import (
    artifact_stats as sql_artifact_stats,
)
from github_radar.storage.ai_artifacts import (
    get_artifact as sql_get_artifact,
)
from github_radar.storage.ai_artifacts import (
    save_artifact as sql_save_artifact,
)

logger = logging.getLogger(__name__)


class ArtifactStore(Protocol):
    """Find-or-save semantics for persisted AI artifacts."""

    async def get(self, key: ArtifactKey) -> StoredArtifact | None: ...

    async def save(self, key: ArtifactKey, artifact: AIArtifact, evidence_json: str) -> None: ...

    async def stats(self) -> dict[str, int]: ...


@dataclass(frozen=True)
class SqlArtifactStore:
    """PostgreSQL-backed store over an :class:`AsyncSession`."""

    session: AsyncSession

    async def get(self, key: ArtifactKey) -> StoredArtifact | None:
        return await sql_get_artifact(self.session, key)

    async def save(
        self, key: ArtifactKey, artifact: AIArtifact, evidence_json: str
    ) -> None:
        await sql_save_artifact(self.session, key, artifact, evidence_json)

    async def stats(self) -> dict[str, int]:
        return await sql_artifact_stats(self.session)


@dataclass(frozen=True)
class MemoryArtifactStore:
    """In-process store used by tests (kept separate from any database)."""

    _artifacts: dict[ArtifactKey, StoredArtifact] = field(default_factory=dict)

    async def get(self, key: ArtifactKey) -> StoredArtifact | None:
        return self._artifacts.get(key)

    async def save(
        self, key: ArtifactKey, artifact: AIArtifact, evidence_json: str
    ) -> None:
        self._artifacts[key] = StoredArtifact(
            artifact=artifact, evidence_json=evidence_json
        )

    async def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in self._artifacts:
            counts[key.artifact_type] = counts.get(key.artifact_type, 0) + 1
        return counts


__all__ = [
    "ArtifactStore",
    "MemoryArtifactStore",
    "SqlArtifactStore",
]
"""Persistence operations for AI synthesis artifacts.

These functions translate :class:`~github_radar.ai.models.AIArtifact` objects
to/from the ``ai_artifacts`` ORM row. Following the project convention they
operate on a caller-supplied :class:`sqlalchemy.ext.asyncio.AsyncSession`;
committing is the caller's job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.ai.models import AIArtifact, ArtifactKey, EvidenceBundle
from github_radar.storage.models import AIArtifactRow


@dataclass(frozen=True)
class StoredArtifact:
    """An artifact plus the evidence snapshot it was generated from."""

    artifact: AIArtifact
    evidence_json: str


def _select_by_key(
    key: ArtifactKey,
) -> Select[tuple[AIArtifactRow]]:
    return select(AIArtifactRow).where(
        AIArtifactRow.entity_type == key.entity_type,
        AIArtifactRow.entity_key == key.entity_key,
        AIArtifactRow.artifact_type == key.artifact_type,
        AIArtifactRow.window_days == key.window_days,
        AIArtifactRow.source_fingerprint == key.source_fingerprint,
        AIArtifactRow.prompt_version == key.prompt_version,
        AIArtifactRow.model == key.model,
    )


async def get_artifact(
    session: AsyncSession, key: ArtifactKey
) -> StoredArtifact | None:
    """Return the stored artifact for ``key``, or ``None``."""
    row = await session.scalar(_select_by_key(key))
    if row is None:
        return None
    return StoredArtifact(
        artifact=AIArtifact.model_validate_json(row.result_json),
        evidence_json=row.evidence_json,
    )


async def save_artifact(
    session: AsyncSession,
    key: ArtifactKey,
    artifact: AIArtifact,
    evidence_json: str,
) -> None:
    """Insert (or replace) the artifact identified by ``key``.

    The unique constraint makes this idempotent with respect to the cache key:
    re-storing the same evidence fingerprint/model overwrites, never duplicates.
    """
    existing = await session.scalar(_select_by_key(key))
    row: AIArtifactRow
    if existing is None:
        row = AIArtifactRow(
            entity_type=key.entity_type,
            entity_key=key.entity_key,
            artifact_type=key.artifact_type,
            window_days=key.window_days,
            source_fingerprint=key.source_fingerprint,
            prompt_version=key.prompt_version,
            provider=artifact.provider,
            model=key.model,
            generated_at=artifact.generated_at,
            result_json=artifact.model_dump_json(),
            evidence_json=evidence_json,
            input_tokens=artifact.input_tokens,
            output_tokens=artifact.output_tokens,
        )
        session.add(row)
    else:
        existing.result_json = artifact.model_dump_json()
        existing.evidence_json = evidence_json
        existing.provider = artifact.provider
        existing.generated_at = artifact.generated_at
        existing.input_tokens = artifact.input_tokens
        existing.output_tokens = artifact.output_tokens
    await session.flush()


async def artifact_stats(session: AsyncSession) -> dict[str, int]:
    """Count persisted artifacts per artifact type (for ``ai status``)."""
    rows = await session.execute(
        select(AIArtifactRow.artifact_type, func.count(AIArtifactRow.id)).group_by(
            AIArtifactRow.artifact_type
        )
    )
    return {name: count for name, count in rows}


def serialize_evidence(bundle: EvidenceBundle) -> str:
    """Stable JSON snapshot of an evidence bundle for storage/display.

    Output is ``{entity_type, entity_key, window_days, fingerprint,
    generated_at, items:[{id, kind, source, observed_at, payload}]}``.
    """
    return json.dumps(
        {
            "entity_type": bundle.entity_type,
            "entity_key": bundle.entity_key,
            "window_days": bundle.window_days,
            "fingerprint": bundle.fingerprint,
            "generated_at": bundle.generated_at.isoformat(timespec="seconds"),
            "items": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "source": item.source,
                    "observed_at": (
                        item.observed_at.isoformat(timespec="seconds")
                        if item.observed_at is not None
                        else None
                    ),
                    "payload": item.payload,
                }
                for item in bundle.items
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = [
    "StoredArtifact",
    "artifact_stats",
    "get_artifact",
    "save_artifact",
    "serialize_evidence",
]
"""Data shapes for the AI synthesis layer.

Two families of types live here:

* **Evidence** — deterministic, allowlisted facts (plus bounded, untrusted
  textual excerpts) assembled into a bundle. The bundle's canonical
  fingerprint is the cache key: it changes only when the *relevant* evidence
  changes, never because of cosmetic reordering.

* **Structured output** — the schema the provider must produce
  (:class:`LLMSummary`) and the persisted artifact that wraps it with
  deterministic metadata (:class:`AIArtifact`).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

EntityType = Literal["repository", "topic", "developer", "ecosystem", "bridge"]

ArtifactType = Literal[
    "repository-summary",
    "repository-trend",
    "topic-summary",
    "developer-summary",
    "ecosystem-summary",
    "bridge-summary",
]

# Coarse deterministic confidence levels (matching analytics/confidence.py).
ConfidenceLevel = Literal["LOW", "MEDIUM", "HIGH"]

# Max allowed key points / unknowns in structured output (bounds the prompt).
MAX_KEY_POINTS = 8
MAX_UNKNOWNS = 8


@dataclass(frozen=True)
class ArtifactKey:
    """The unique identity of one cached synthesis artifact.

    Combines the entity, artifact type, window, the deterministic evidence
    fingerprint, the prompt version and the model. Any of those changing —
    including the evidence (via its fingerprint) — yields a different key, so
    stale summaries are never served as current.
    """

    entity_type: EntityType
    entity_key: str
    artifact_type: ArtifactType
    window_days: int
    source_fingerprint: str
    prompt_version: str
    model: str
    provider: str = "openai-compatible"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceItem:
    """One allowlisted fact (or bounded untrusted excerpt) sent to the model.

    ``payload`` is a rendered, human-readable string. ``meta`` carries
    structured key/value pairs that participate in the fingerprint but are not
    necessarily rendered — keep it to strings.
    """

    id: str
    kind: str
    source: str
    payload: str
    observed_at: datetime | None = None
    meta: tuple[tuple[str, str], ...] = ()

    def as_canonical(self) -> dict[str, object]:
        """Canonical form used for fingerprinting (stable field order)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "payload": self.payload,
            "meta": sorted(self.meta),
        }


@dataclass(frozen=True)
class EvidenceBundle:
    """A stable, fingerprint-addressable bundle of evidence for one entity."""

    entity_type: EntityType
    entity_key: str
    window_days: int
    generated_at: datetime
    fingerprint: str
    items: tuple[EvidenceItem, ...]

    @property
    def item_ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.items)

    def item_text(self, item_id: str) -> str | None:
        for item in self.items:
            if item.id == item_id:
                return item.payload
        return None


def fingerprint_bundle(items: Sequence[EvidenceItem]) -> str:
    """Deterministic sha256 fingerprint of ``items``.

    Canonicalization: items are sorted by their stable ``id`` and each item is
    reduced to a fixed field order via :meth:`EvidenceItem.as_canonical`
    (``meta`` pairs sorted). Reordering the bundle therefore never changes the
    fingerprint; adding, removing, or altering any relevant evidence does.
    """
    canonical = [item.as_canonical() for item in sorted(items, key=lambda i: i.id)]
    digest = hashlib.sha256()
    digest.update(json.dumps(canonical, sort_keys=False, default=repr).encode("utf-8"))
    return digest.hexdigest()


def _canonical_order(item: EvidenceItem) -> tuple[object, ...]:
    """Explicit, content-only sort key for stable id assignment.

    The caller-supplied ``id`` must never influence id/order assignment (builders
    hand in blank ids). Only the rendered content and structured metadata decide
    the canonical sequence, so rebuilds of the same evidence in any order assign
    the same ``E1..En`` ids and the same fingerprint.
    """
    return (item.kind, item.source, item.payload, tuple(sorted(item.meta)))


def build_bundle(
    *,
    entity_type: EntityType,
    entity_key: str,
    window_days: int,
    items: Sequence[EvidenceItem],
    generated_at: datetime | None = None,
) -> EvidenceBundle:
    """Assign stable ``E1..En`` ids and fingerprint a bundle.

    Items are first placed in a canonical content order (see
    :func:`_canonical_order`), then ids follow that order — ``kind``/``source``/
    ``payload`` never change the id sequence, only the factual content does,
    and reordering the evidence never changes ids or the fingerprint.
    """
    ordered = sorted(items, key=_canonical_order)
    assigned: list[EvidenceItem] = [
        _replaced(item, id=f"E{index}")
        for index, item in enumerate(ordered, start=1)
    ]
    return EvidenceBundle(
        entity_type=entity_type,
        entity_key=entity_key,
        window_days=window_days,
        generated_at=generated_at or datetime.now(UTC),
        fingerprint=fingerprint_bundle(assigned),
        items=tuple(assigned),
    )


def _replaced(item: EvidenceItem, *, id: str) -> EvidenceItem:
    if item.id == id:
        return item
    return EvidenceItem(
        id=id,
        kind=item.kind,
        source=item.source,
        payload=item.payload,
        observed_at=item.observed_at,
        meta=item.meta,
    )


# ---------------------------------------------------------------------------
# Structured LLM output
# ---------------------------------------------------------------------------


class KeyPoint(BaseModel):
    """One asserted key point, grounded in explicit evidence ids."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class LLMSummary(BaseModel):
    """The JSON object the provider must return (nothing more is accepted)."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    key_points: list[KeyPoint] = Field(min_length=1, max_length=MAX_KEY_POINTS)
    unknowns: list[str] = Field(default_factory=list, max_length=MAX_UNKNOWNS)


# ---------------------------------------------------------------------------
# Persisted artifact
# ---------------------------------------------------------------------------


class AIArtifact(BaseModel):
    """A persisted synthesis result with deterministic metadata."""

    model_config = ConfigDict(extra="forbid")

    entity_type: EntityType
    entity_key: str
    artifact_type: ArtifactType
    window_days: int
    prompt_version: str
    provider: str
    model: str
    generated_at: datetime
    source_fingerprint: str
    headline: str
    summary: str
    key_points: list[KeyPoint]
    unknowns: list[str]
    confidence: ConfidenceLevel
    input_tokens: int | None = None
    output_tokens: int | None = None


__all__ = [
    "AIArtifact",
    "ArtifactKey",
    "ArtifactType",
    "ConfidenceLevel",
    "EntityType",
    "EvidenceBundle",
    "EvidenceItem",
    "KeyPoint",
    "LLMSummary",
    "MAX_KEY_POINTS",
    "MAX_UNKNOWNS",
    "build_bundle",
    "fingerprint_bundle",
]
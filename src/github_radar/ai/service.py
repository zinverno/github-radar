"""Synthesis orchestration: evidence → cache lookup → model call → persist.

The service enforces the Phase 5 guarantees:

* **cache-first** — a stored artifact whose cache key (evidence fingerprint,
  prompt version, model, ...) still matches is served without a model call;
  ``force`` skips the lookup;
* **bounded spend** — every synthesis consumes one unit of a per-run
  :class:`RunBudget`; when exhausted the run stops with ``AIRequestLimitError``;
* **fail-safe persistence** — only a *validated* artifact is ever stored; a
  provider failure, a validation failure or a repair failure leaves the store
  untouched and raises so the CLI can say exactly what went wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from github_radar.ai.cache import ArtifactStore
from github_radar.ai.errors import AIRequestLimitError
from github_radar.ai.models import (
    AIArtifact,
    ArtifactKey,
    ArtifactType,
    ConfidenceLevel,
    EntityType,
    EvidenceBundle,
    LLMSummary,
)
from github_radar.ai.prompts import PROMPT_VERSIONS, build_system_prompt, build_user_prompt
from github_radar.ai.provider import AIProvider, ProviderResult

logger = logging.getLogger(__name__)


class RunBudget:
    """Tracks the per-run model-call allowance.

    ``total`` of ``None`` means unlimited (tests); a finite total is exhausted
    once all units are consumed, after which every further call raises
    :class:`AIRequestLimitError`.
    """

    def __init__(self, total: int | None) -> None:
        if total is not None and total < 0:
            raise ValueError("run budget must not be negative")
        self.total = total
        self.remaining: int | None = total

    def consume(self) -> None:
        """Reserve one model call, raising when the allowance is exhausted."""
        if self.total is None:
            self.remaining = None
            return
        assert self.remaining is not None
        if self.remaining <= 0:
            raise AIRequestLimitError(
                f"model-call allowance for this run is exhausted (limit {self.total})"
            )
        self.remaining -= 1


@dataclass(frozen=True)
class SynthesisRequest:
    """Everything needed to produce and persist one artifact."""

    entity_type: EntityType
    entity_key: str
    artifact_type: ArtifactType
    window_days: int
    bundle: EvidenceBundle
    confidence: ConfidenceLevel
    bundle_json: str
    provider: AIProvider
    store: ArtifactStore
    budget: RunBudget
    generated_at: datetime
    force: bool = False
    max_evidence_chars: int = 12_000

    def cache_key(self) -> ArtifactKey:
        """The store identity of this artifact (data-sensitive)."""
        version = PROMPT_VERSIONS.get(self.artifact_type)
        if version is None:
            raise ValueError(f"No prompt version registered for {self.artifact_type}")
        return ArtifactKey(
            entity_type=self.entity_type,
            entity_key=self.entity_key,
            artifact_type=self.artifact_type,
            window_days=self.window_days,
            source_fingerprint=self.bundle.fingerprint,
            prompt_version=version,
            model=self.provider.model,
            provider=self.provider.name,
        )


@dataclass(frozen=True)
class SynthesisResult:
    """The produced (or replayed) artifact plus how it was obtained."""

    artifact: AIArtifact
    cache_hit: bool


async def synthesize(request: SynthesisRequest) -> SynthesisResult:
    """Produce (or replay) the artifact described by ``request``."""
    key = request.cache_key()
    if not request.force:
        stored = await request.store.get(key)
        if stored is not None:
            logger.debug(
                "AI cache hit for %s %s", request.entity_type, request.entity_key
            )
            return SynthesisResult(stored.artifact, cache_hit=True)

    request.budget.consume()
    result = await _request_summary(request)
    artifact = _artifact_from(request, key, result)
    await request.store.save(key, artifact, request.bundle_json)
    logger.info(
        "AI synthesis stored for %s %s (prompt %s, model %s)",
        request.entity_type,
        request.entity_key,
        key.prompt_version,
        key.model,
    )
    return SynthesisResult(artifact, cache_hit=False)


async def _request_summary(
    request: SynthesisRequest,
) -> ProviderResult[LLMSummary]:
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(
        request.bundle,
        artifact_type=request.artifact_type,
        max_evidence_chars=request.max_evidence_chars,
    )
    return await request.provider.generate_structured(
        LLMSummary,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


def _artifact_from(
    request: SynthesisRequest,
    key: ArtifactKey,
    result: ProviderResult[LLMSummary],
) -> AIArtifact:
    summary = result.content
    return AIArtifact(
        entity_type=request.entity_type,
        entity_key=request.entity_key,
        artifact_type=request.artifact_type,
        window_days=request.window_days,
        prompt_version=key.prompt_version,
        provider=key.provider,
        model=key.model,
        generated_at=request.generated_at,
        source_fingerprint=request.bundle.fingerprint,
        headline=summary.headline,
        summary=summary.summary,
        key_points=summary.key_points,
        unknowns=summary.unknowns,
        confidence=request.confidence,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


__all__ = [
    "RunBudget",
    "SynthesisRequest",
    "SynthesisResult",
    "synthesize",
]
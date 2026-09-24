"""Synthesis service: cache-first, force, budget, fail-safe persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from github_radar.ai.cache import MemoryArtifactStore
from github_radar.ai.errors import AIRequestLimitError
from github_radar.ai.models import (
    AIArtifact,
    EvidenceBundle,
    EvidenceItem,
    build_bundle,
)
from github_radar.ai.provider import ProviderResult, SchemaT
from github_radar.ai.service import RunBudget, SynthesisRequest, synthesize

REFERENCE_NOW = datetime(2024, 5, 8, tzinfo=UTC)


def _item(value: int) -> EvidenceItem:
    return EvidenceItem(
        id="",
        kind="measurement",
        source="test",
        payload=f"fact {value}",
        observed_at=REFERENCE_NOW,
    )


def _bundle(stars: int) -> EvidenceBundle:
    return build_bundle(
        entity_type="repository",
        entity_key="acme/widget",
        window_days=7,
        items=[_item(stars)],
    )


class FakeProvider:
    """Minimal protocol-compatible provider recording every call.

    Generic over the requested schema: it re-validates a fixed payload against
    ``schema``, mirroring the production ``AIProvider`` contract. Set
    ``failure`` to simulate a provider error after call recording.
    """

    calls = 0
    name = "fake"
    model = "fake-model"

    def __init__(
        self,
        tokens: tuple[int | None, int | None] = (12, 30),
        failure: Exception | None = None,
    ) -> None:
        self.calls = 0
        self._tokens = tokens
        self._failure = failure

    async def generate_structured(
        self,
        schema: type[SchemaT],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> ProviderResult[SchemaT]:
        self.calls += 1
        if self._failure is not None:
            raise self._failure
        content = schema.model_validate(
            {
                "headline": "grew",
                "summary": "The repository grew within the tracked dataset.",
                "key_points": [{"text": "covered by evidence", "evidence_ids": ["E1"]}],
                "unknowns": ["not measured"],
            }
        )
        return ProviderResult(
            content=content,
            input_tokens=self._tokens[0],
            output_tokens=self._tokens[1],
            model=self.model,
        )

    async def close(self) -> None:
        return None


class _RequestBuilder:
    def __init__(self) -> None:
        self.store = MemoryArtifactStore()

    def build(
        self,
        *,
        bundle: EvidenceBundle,
        provider: FakeProvider | None = None,
        budget: int | None = 10,
    ) -> SynthesisRequest:
        return SynthesisRequest(
            entity_type="repository",
            entity_key="acme/widget",
            artifact_type="repository-summary",
            window_days=7,
            bundle=bundle,
            confidence="HIGH",
            bundle_json=json.dumps({"items": "snapshot"}),
            provider=provider or FakeProvider(),
            store=self.store,
            budget=RunBudget(total=budget),
            generated_at=REFERENCE_NOW,
            force=False,
        )


async def test_first_run_persists_and_second_run_is_cache_hit() -> None:
    b = _RequestBuilder()
    bundle = _bundle(stars=100)
    provider = FakeProvider()
    req1 = b.build(bundle=bundle, provider=provider)
    req2 = b.build(bundle=bundle, provider=FakeProvider())

    first = await synthesize(req1)
    second = await synthesize(req2)

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.artifact.source_fingerprint == first.artifact.source_fingerprint
    assert provider.calls == 1  # the model was invoked exactly once
    assert (await b.store.stats()) == {"repository-summary": 1}
    await provider.close()


async def test_force_bypasses_cache() -> None:
    b = _RequestBuilder()
    bundle = _bundle(stars=100)
    first = await synthesize(b.build(bundle=bundle))
    req = b.build(bundle=bundle, provider=FakeProvider())
    forced = await synthesize(
        SynthesisRequest(
            entity_type=req.entity_type,
            entity_key=req.entity_key,
            artifact_type=req.artifact_type,
            window_days=req.window_days,
            bundle=req.bundle,
            confidence=req.confidence,
            bundle_json=req.bundle_json,
            provider=req.provider,
            store=req.store,
            budget=req.budget,
            generated_at=req.generated_at,
            force=True,
        )
    )
    assert forced.cache_hit is False
    assert forced.artifact.key_points == first.artifact.key_points
    assert len(b.store._artifacts) == 1


async def test_changed_evidence_changes_key_and_misses_cache() -> None:
    b = _RequestBuilder()
    old = await synthesize(b.build(bundle=_bundle(stars=100)))
    new = await synthesize(b.build(bundle=_bundle(stars=101)))
    assert new.cache_hit is False
    assert new.artifact.source_fingerprint != old.artifact.source_fingerprint
    assert len(b.store._artifacts) == 2


async def test_zero_budget_raises_without_calling_provider() -> None:
    b = _RequestBuilder()
    provider = FakeProvider()
    req = b.build(bundle=_bundle(stars=100), budget=0, provider=provider)
    with pytest.raises(AIRequestLimitError):
        await synthesize(req)
    assert provider.calls == 0
    assert (await b.store.stats()) == {}


async def test_unlimited_budget_never_raises() -> None:
    budget = RunBudget(total=None)
    for _ in range(50):
        budget.consume()
    assert budget.remaining is None


async def test_provider_failure_is_not_persisted_and_raises() -> None:
    b = _RequestBuilder()
    failing = FakeProvider(failure=RuntimeError("provider exploded"))
    req = b.build(bundle=_bundle(stars=100), provider=failing)
    with pytest.raises(RuntimeError):
        await synthesize(req)
    assert (await b.store.stats()) == {}
    assert (await b.store.get(req.cache_key())) is None


async def test_tokens_flow_into_artifact() -> None:
    b = _RequestBuilder()
    result = await synthesize(b.build(bundle=_bundle(stars=100)))
    assert result.artifact.input_tokens == 12
    assert result.artifact.output_tokens == 30


async def test_artifact_round_trips_via_json() -> None:
    b = _RequestBuilder()
    result = await synthesize(b.build(bundle=_bundle(stars=100)))
    restored = AIArtifact.model_validate_json(result.artifact.model_dump_json())
    assert restored == result.artifact


async def test_run_budget_rejects_negative_total() -> None:
    with pytest.raises(ValueError):
        RunBudget(total=-1)
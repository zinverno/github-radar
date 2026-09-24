"""AI data shapes: fingerprinting, stable ids, structured output schemas."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from github_radar.ai.models import (
    AIArtifact,
    ArtifactKey,
    EvidenceBundle,
    EvidenceItem,
    KeyPoint,
    LLMSummary,
    build_bundle,
    fingerprint_bundle,
)


def item(seed: int) -> EvidenceItem:
    return EvidenceItem(
        id="",
        kind="measurement",
        source="test",
        payload=f"fact number {seed}",
        observed_at=datetime(2024, 5, 1, tzinfo=UTC),
        meta=(("key", str(seed)),),
    )


def bundle() -> EvidenceBundle:
    return build_bundle(
        entity_type="repository",
        entity_key="acme/widget",
        window_days=7,
        items=[item(1), item(2), item(3)],
    )


def test_bundle_assigns_stable_ids_and_serializes() -> None:
    b = bundle()
    assert [it.id for it in b.items] == ["E1", "E2", "E3"]
    assert b.item_ids == frozenset({"E1", "E2", "E3"})
    assert b.generated_at.tzinfo is not None


def test_fingerprint_bundle_is_order_sensitive_raw_signature() -> None:
    # fingerprint_bundle hashes the payload sequence in the given order; the
    # ordering guarantee lives in build_bundle (which sorts + re-ids).
    assert fingerprint_bundle([item(1), item(2), item(3)]) == fingerprint_bundle(
        [item(1), item(2), item(3)]
    )
    assert fingerprint_bundle([item(1), item(2), item(3)]) != fingerprint_bundle(
        [item(3), item(1), item(2)]
    )


def test_build_bundle_normalizes_order_for_fingerprint() -> None:
    a = build_bundle(
        entity_type="repository",
        entity_key="acme/widget",
        window_days=7,
        items=[item(1), item(2)],
    )
    b = build_bundle(
        entity_type="repository",
        entity_key="acme/widget",
        window_days=7,
        items=[item(2), item(1)],
    )
    assert a.fingerprint == b.fingerprint
    assert [it.id for it in a.items] == [it.id for it in b.items]


def test_fingerprint_changes_when_fact_changes() -> None:
    assert fingerprint_bundle([item(1)]) != fingerprint_bundle([item(4)])


def test_rebuild_after_rename_is_identical() -> None:
    # The bundle builder sorts and re-ids, so feeding the SAME facts must yield
    # the same fingerprint regardless of the caller's own ids/order.
    def with_ids(ids: tuple[str, str]) -> EvidenceBundle:
        return build_bundle(
            entity_type="repository",
            entity_key="acme/widget",
            window_days=7,
            items=[
                EvidenceItem(
                    id=ids[0],
                    kind="measurement",
                    source="test",
                    payload="fact number 1",
                    observed_at=datetime(2024, 5, 1, tzinfo=UTC),
                ),
                EvidenceItem(
                    id=ids[1],
                    kind="measurement",
                    source="test",
                    payload="fact number 2",
                    observed_at=datetime(2024, 5, 1, tzinfo=UTC),
                ),
            ],
        )

    a = with_ids(("zz", "aa"))
    b = with_ids(("yy", "xx"))
    assert a.fingerprint == b.fingerprint
    assert [it.id for it in a.items] == [it.id for it in b.items]


def test_cache_key_surfaces_every_cache_dimension() -> None:
    key = ArtifactKey(
        entity_type="repository",
        entity_key="acme/widget",
        artifact_type="repository-summary",
        window_days=7,
        source_fingerprint="abc123",
        prompt_version="repository-summary-v1",
        model="gpt-test",
    )
    assert key.provider == "openai-compatible"
    assert hash(key) == hash(
        ArtifactKey(
            entity_type="repository",
            entity_key="acme/widget",
            artifact_type="repository-summary",
            window_days=7,
            source_fingerprint="abc123",
            prompt_version="repository-summary-v1",
            model="gpt-test",
        )
    )


def test_llmsummary_requires_grounded_key_points() -> None:
    with pytest.raises(ValidationError):
        LLMSummary.model_validate(
            {
                "headline": "x",
                "summary": "y",
                "key_points": [{"text": "claim without evidence ids"}],
            }
        )


def test_llmsummary_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        LLMSummary.model_validate(
            {
                "headline": "x",
                "summary": "y",
                "key_points": [{"text": "k", "evidence_ids": ["E1"]}],
                "unknowns": [],
                "made_up": True,
            }
        )


def test_aiartifact_round_trip_json() -> None:
    artifact = AIArtifact(
        entity_type="repository",
        entity_key="acme/widget",
        artifact_type="repository-summary",
        window_days=7,
        prompt_version="repository-summary-v1",
        provider="openai-compatible",
        model="gpt-test",
        generated_at=datetime(2024, 5, 1, tzinfo=UTC),
        source_fingerprint="abc",
        headline="Stable growth",
        summary="The repository grew steadily within the tracked dataset.",
        key_points=[
            KeyPoint(text="one", evidence_ids=["E1"]),
            KeyPoint(text="two", evidence_ids=["E2", "E3"]),
        ],
        unknowns=["Leftover risk"],
        confidence="MEDIUM",
        input_tokens=10,
        output_tokens=20,
    )
    restored = AIArtifact.model_validate_json(artifact.model_dump_json())
    assert restored == artifact
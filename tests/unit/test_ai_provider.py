"""OpenAI-compatible provider behaviour: retries, repair, tokens, fail-fast."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
import respx

from github_radar.ai.errors import (
    AINetworkError,
    AIProviderError,
    AIRateLimitError,
    AIResponseValidationError,
)
from github_radar.ai.models import LLMSummary
from github_radar.ai.openai_compatible import OpenAICompatibleProvider

AI_BASE = "https://ai.example.com"


def _payload(
    *,
    content: str,
    prompt_tokens: int | None = 7,
    completion_tokens: int = 11,
) -> dict[str, object]:
    body: dict[str, object] = {"choices": [{"message": {"content": content}}]}
    if prompt_tokens is not None:
        body["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
    return body


def _valid_summary(custom: str = "The repository grew.") -> dict[str, object]:
    return {
        "headline": "grew",
        "summary": custom,
        "key_points": [{"text": "point", "evidence_ids": ["E1"]}],
        "unknowns": [],
    }


@pytest.fixture
def ai_mock() -> Iterator[respx.MockRouter]:
    mock = respx.mock
    mock.start()
    yield mock
    mock.reset()
    mock.stop()


@pytest.fixture
def provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=AI_BASE,
        api_key="sk-test-key-value",
        model="gpt-test",
        timeout_seconds=5.0,
        max_output_tokens=600,
        temperature=0.0,
    )


async def _noop_sleep(_seconds: float) -> None:  # noqa: D401
    return None


def _fast_sleep(monkeypatch) -> None:
    monkeypatch.setattr(
        "github_radar.ai.openai_compatible.asyncio.sleep", _noop_sleep
    )


async def test_generate_structured_returns_validated_model_and_tokens(
    ai_mock, provider
) -> None:
    route = ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=_payload(content=json.dumps(_valid_summary())))
    )
    async with provider:
        result = await provider.generate_structured(
            LLMSummary, system_prompt="s", user_prompt="u"
        )
    assert result.content.summary == "The repository grew."
    assert result.input_tokens == 7
    assert result.output_tokens == 11
    assert result.model == "gpt-test"
    assert len(route.calls) == 1


async def test_tokens_are_none_when_usage_absent(ai_mock, provider) -> None:
    ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=_payload(content=json.dumps(_valid_summary()), prompt_tokens=None)
        )
    )
    async with provider:
        result = await provider.generate_structured(
            LLMSummary, system_prompt="s", user_prompt="u"
        )
    assert result.input_tokens is None
    assert result.output_tokens is None


async def test_auth_or_other_4xx_fail_fast_no_retry(ai_mock, provider, monkeypatch) -> None:
    _fast_sleep(monkeypatch)
    route = ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "bad token"})
    )
    async with provider:
        with pytest.raises(AIProviderError) as excinfo:
            await provider.generate_structured(LLMSummary, system_prompt="s", user_prompt="u")
    assert excinfo.value.status_code == 401
    assert len(route.calls) == 1


async def test_429_without_retry_after_raises_rate_limit(ai_mock, provider) -> None:
    route = ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    async with provider:
        with pytest.raises(AIRateLimitError):
            await provider.generate_structured(LLMSummary, system_prompt="s", user_prompt="u")
    assert len(route.calls) == 1


async def test_429_then_success_honours_retry_after(ai_mock, provider) -> None:
    route = ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow"}),
            httpx.Response(200, json=_payload(content=json.dumps(_valid_summary()))),
        ]
    )
    async with provider:
        result = await provider.generate_structured(
            LLMSummary, system_prompt="s", user_prompt="u"
        )
    assert result.content.headline == "grew"
    assert len(route.calls) == 2


async def test_5xx_retries_then_succeeds(ai_mock, provider, monkeypatch) -> None:
    _fast_sleep(monkeypatch)
    route = ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        side_effect=[
            httpx.Response(503, json={"error": "overloaded"}),
            httpx.Response(200, json=_payload(content=json.dumps(_valid_summary()))),
        ]
    )
    async with provider:
        result = await provider.generate_structured(
            LLMSummary, system_prompt="s", user_prompt="u"
        )
    assert result.content.headline == "grew"
    assert len(route.calls) == 2


async def test_transport_error_retries_then_succeeds(ai_mock, provider, monkeypatch) -> None:
    _fast_sleep(monkeypatch)
    route = ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json=_payload(content=json.dumps(_valid_summary()))),
        ]
    )
    async with provider:
        result = await provider.generate_structured(
            LLMSummary, system_prompt="s", user_prompt="u"
        )
    assert result.content.headline == "grew"
    assert len(route.calls) == 2


async def test_all_transport_attempts_exhausted(ai_mock, provider, monkeypatch) -> None:
    _fast_sleep(monkeypatch)
    route = ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        side_effect=[httpx.ConnectError("down")] * 8
    )
    async with provider:
        with pytest.raises(AINetworkError):
            await provider.generate_structured(LLMSummary, system_prompt="s", user_prompt="u")
    assert len(route.calls) >= 4


async def test_invalid_json_triggers_one_repair_then_succeeds(ai_mock, provider) -> None:
    route = ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_payload(content="not json at all")),
            httpx.Response(200, json=_payload(content=json.dumps(_valid_summary()))),
        ]
    )
    async with provider:
        result = await provider.generate_structured(
            LLMSummary, system_prompt="s", user_prompt="u"
        )
    assert result.content.headline == "grew"
    assert len(route.calls) == 2
    repair_user = route.calls[1].request.content
    assert b"corrected JSON" in repair_user


async def test_two_bad_responses_raise_validation_error(ai_mock, provider) -> None:
    route = ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_payload(content="bad")),
            httpx.Response(200, json=_payload(content="still bad")),
        ]
    )
    async with provider:
        with pytest.raises(AIResponseValidationError):
            await provider.generate_structured(LLMSummary, system_prompt="s", user_prompt="u")
    assert len(route.calls) == 2


async def test_schema_invalid_but_valid_json_triggers_repair(ai_mock, provider) -> None:
    malformed = {"headline": "grew", "key_points": [{"text": "missing evidence ids"}]}
    route = ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_payload(content=json.dumps(malformed))),
            httpx.Response(200, json=_payload(content=json.dumps(_valid_summary()))),
        ]
    )
    async with provider:
        result = await provider.generate_structured(
            LLMSummary, system_prompt="s", user_prompt="u"
        )
    assert result.content.summary == "The repository grew."
    assert len(route.calls) == 2


async def test_empty_completion_raises_provider_error(ai_mock, provider) -> None:
    ai_mock.post(f"{AI_BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]})
    )
    async with provider:
        with pytest.raises(AIProviderError):
            await provider.generate_structured(LLMSummary, system_prompt="s", user_prompt="u")
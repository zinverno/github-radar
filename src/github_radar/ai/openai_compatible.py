"""OpenAI-compatible chat-completions provider (httpx-based, no SDK).

Talks to ``{base_url}/chat/completions``. Standard providers, permissive
gateways (OpenRouter, ...) and self-hosted OpenAI-compatible servers all work:
the only requirement is a ``Bearer`` token and JSON chat completion output.

Behaviour contract (see ``docs/AI.md``):

* retry — only 429 (honouring ``Retry-After``, capped), 5xx and transport
  failures, with bounded attempts and exponential backoff;
* fail fast — 401/403/400/422 and any other 4xx raise ``AIProviderError``
  without retrying;
* repair — exactly ONE bounded repair attempt when the provider returns
  content that is not valid JSON for the requested schema; a second failure
  raises ``AIResponseValidationError`` (nothing is persisted);
* tokens — ``usage.prompt_tokens`` / ``usage.completion_tokens`` are captured
  when present and left as ``None`` otherwise; never fabricated.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel

from github_radar.ai.errors import (
    AIConfigurationError,
    AINetworkError,
    AIProviderError,
    AIRateLimitError,
    AIResponseValidationError,
)
from github_radar.ai.provider import ProviderResult, SchemaT
from github_radar.config import Settings
from github_radar.config.settings import SettingsError

logger = logging.getLogger(__name__)

NAME = "openai-compatible"

# Bounded retry policy for the *transport-level* loop.
MAX_TRANSPORT_ATTEMPTS = 4  # 1 initial + 3 retries
_BACKOFF_BASE_SECONDS = 1.0
_MAX_RETRY_AFTER_SECONDS = 120.0


def make_provider(settings: Settings) -> OpenAICompatibleProvider:
    """Build the configured provider, raising ``AIConfigurationError`` on any
    missing/invalid configuration."""
    try:
        base_url, api_key, model = settings.require_ai_config()
    except SettingsError as exc:
        raise AIConfigurationError(str(exc)) from exc
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=settings.ai_timeout_seconds,
        max_output_tokens=settings.ai_max_output_tokens,
        temperature=settings.ai_temperature,
    )


class OpenAICompatibleProvider:
    """A single-model chat-completions producer of JSON objects."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        temperature: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature

    @property
    def name(self) -> str:
        return NAME

    @property
    def model(self) -> str:
        return self._model

    async def generate_structured(
        self,
        schema: type[SchemaT],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> ProviderResult[SchemaT]:
        body = self._request_body(system_prompt, user_prompt)
        content, input_tokens, output_tokens = await self._chat_completions(body)
        try:
            parsed = schema.model_validate(json.loads(content))
        except (ValueError, TypeError) as exc:
            content, input_tokens, output_tokens = await self._repair(
                body, schema, original_error=str(exc)
            )
            parsed = schema.model_validate(json.loads(content))
        return ProviderResult(
            content=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self._model,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> OpenAICompatibleProvider:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _request_body(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
            "response_format": {"type": "json_object"},
        }

    async def _chat_completions(
        self, body: dict[str, Any]
    ) -> tuple[str, int | None, int | None]:
        """Return ``(content, input_tokens, output_tokens)`` or raise."""
        url = f"{self.base_url}/chat/completions"
        last_transport_error: httpx.TransportError | None = None

        for attempt in range(MAX_TRANSPORT_ATTEMPTS):
            try:
                response = await self._client.post(url, json=body)
            except httpx.TransportError as exc:
                last_transport_error = exc
                logger.warning("AI transport error (attempt %d): %s", attempt, exc)
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
                continue

            if response.status_code < 300:
                return _extract_content_and_usage(response)

            status = response.status_code
            if status == 429:
                retry_after = _retry_after(response)
                if retry_after is None or retry_after > _MAX_RETRY_AFTER_SECONDS:
                    raise AIRateLimitError(
                        "AI provider rate limit reached without an acceptable "
                        "retry window.",
                        retry_after_seconds=retry_after,
                        status_code=status,
                    )
                logger.warning(
                    "AI provider 429 (attempt %d); retrying in %.1fs",
                    attempt,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                continue

            if status in {500, 502, 503, 504}:
                wait = _BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "AI provider transient failure %s (attempt %d); retrying in %.1fs",
                    status,
                    attempt,
                    wait,
                )
                await asyncio.sleep(wait)
                continue

            raise self._non_retryable(status, response)

        raise AINetworkError(
            f"AI provider unreachable after {MAX_TRANSPORT_ATTEMPTS} attempts"
            f" (last error: {last_transport_error})"
        )

    def _non_retryable(
        self, status: int, response: httpx.Response
    ) -> AIProviderError:
        detail = response.text[:500] if response.text else ""
        return AIProviderError(
            f"AI provider rejected the request (HTTP {status}): {detail.strip()}",
            status_code=status,
            response_body=detail,
        )

    async def _repair(
        self,
        body: dict[str, Any],
        schema: type[BaseModel],
        *,
        original_error: str,
    ) -> tuple[str, int | None, int | None]:
        """One bounded repair attempt on malformed JSON/schema output."""
        repair = dict(body)
        repair["messages"] = list(body["messages"]) + [
            {
                "role": "user",
                "content": (
                    "Your previous answer was not valid JSON matching the requested "
                    "schema. Validation error: "
                    f"{original_error[:500]}. Respond with ONLY a corrected JSON "
                    "object and nothing else."
                ),
            }
        ]
        content, input_tokens, output_tokens = await self._chat_completions(repair)
        try:
            schema.model_validate(json.loads(content))
        except (ValueError, TypeError) as exc:
            raise AIResponseValidationError(
                "AI provider returned content that does not match the requested "
                f"schema even after one repair attempt: {exc}"
            ) from exc
        return content, input_tokens, output_tokens


def _extract_content_and_usage(
    response: httpx.Response,
) -> tuple[str, int | None, int | None]:
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIProviderError(
            "AI provider response did not include a chat completion message.",
            status_code=response.status_code,
            response_body=response.text[:500],
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise AIProviderError(
            "AI provider returned an empty chat completion message.",
            status_code=response.status_code,
            response_body=response.text[:500],
        )
    usage = data.get("usage") or {}
    return (
        content,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
    )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


__all__ = ["NAME", "OpenAICompatibleProvider", "make_provider"]
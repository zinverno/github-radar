"""Provider abstraction for the AI synthesis layer.

A provider is a thin async adapter that turns a system/user prompt pair into a
Pydantic-validated structured result. Exactly one bounded repair attempt and
bounded retries (429 / 5xx / network only) live in the adapter; the service
layer never loops.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@dataclass(frozen=True)
class ProviderResult[SchemaT]:
    """A successful structured provider response.

    ``input_tokens``/``output_tokens`` are ``None`` when the provider did not
    report usage — the caller shows "unavailable" and never fabricates a value.
    """

    content: SchemaT
    input_tokens: int | None
    output_tokens: int | None
    model: str


@runtime_checkable
class AIProvider(Protocol):
    """Async, OpenAI-compatible provider contract."""

    @property
    def name(self) -> str:
        """Stable provider identifier stored on artifacts (e.g. ``openai``)."""
        ...

    @property
    def model(self) -> str:
        """Model identifier stored on artifacts and used in the cache key."""
        ...

    async def generate_structured(
        self,
        schema: type[SchemaT],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> ProviderResult[SchemaT]:
        """Return a validated ``schema`` instance, or raise an ``AIError``."""
        ...

    async def close(self) -> None:
        """Release any transport resources."""
        ...


__all__ = ["AIProvider", "ProviderResult"]
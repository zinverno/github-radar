"""AI synthesis layer.

This module is OPTIONAL — every deterministic analytics feature works without
it. When configured, it turns the deterministic evidence computed by Phases
2–4 into *explanatory* prose via an LLM. The LLM only ever explains and
synthesizes the measured facts bundled into the prompt; it never measures,
ranks, or decides anything the analytics did not already establish.
"""

from __future__ import annotations

from github_radar.ai.errors import (
    AIConfigurationError,
    AIError,
    AINetworkError,
    AIProviderError,
    AIRateLimitError,
    AIRequestLimitError,
    AIResponseValidationError,
)

__all__ = [
    "AIConfigurationError",
    "AIError",
    "AINetworkError",
    "AIProviderError",
    "AIRateLimitError",
    "AIRequestLimitError",
    "AIResponseValidationError",
]
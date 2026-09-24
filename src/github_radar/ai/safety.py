"""Safety of the evidence that leaves the process.

Two guarantees:

#. **Allowlist** — evidence is built exclusively from fields we explicitly
   include (:mod:`github_radar.ai.evidence`). Credentials, connection strings,
   environment variables, local paths and private/contact columns are never
   added. As defence-in-depth a final :func:`redact_secrets` pass scrubs any
   occurrence of the configured secrets from rendered payload texts.

#. **Injection resistance** — untrusted repository text (README, releases,
   commit messages, descriptions) is structurally delimited as *data*, and the
   truncated result is normalized so hostile control sequences cannot smuggle
   instructions past the delimiters.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from github_radar.config import Settings

# Printable control characters (everything except \n and \t) are stripped so a
# malicious payload cannot hide instructions behind invisible characters.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Cap on a single rendered evidence item's payload.
MAX_PAYLOAD_CHARS = 4000

UNTRUSTED_START = "<untrusted-data>"
UNTRUSTED_END = "</untrusted-data>"

_MISSING_PLACEHOLDER = "—"


def normalize_payload(text: str, *, max_chars: int = MAX_PAYLOAD_CHARS) -> str:
    """Strip control characters and bound a rendered evidence payload."""
    cleaned = _CONTROL_RE.sub("", text)
    stripped = cleaned.strip()
    if len(stripped) > max_chars:
        return stripped[:max_chars].rstrip() + " …[truncated]"
    return stripped or _MISSING_PLACEHOLDER


def normalize_excerpt(text: str, *, max_chars: int = MAX_PAYLOAD_CHARS) -> str:
    """Normalize untrusted repository text for embedding as evidence."""
    compact = _CONTROL_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    stripped = "\n".join(
        line.rstrip() for line in compact.split("\n")
    ).strip()
    if len(stripped) > max_chars:
        return stripped[:max_chars].rstrip() + " …[truncated]"
    return stripped or _MISSING_PLACEHOLDER


def wrap_untrusted(label: str, text: str) -> str:
    """Delimit untrusted repository text so it cannot masquerade as prompt."""
    return (
        f"{UNTRUSTED_START} kind={label}\n{text}\n{UNTRUSTED_END}"
    )


def redact_secrets(text: str, secrets: Sequence[str]) -> str:
    """Replace any occurrence of a configured secret with ``[REDACTED]``.

    Applied to every rendered payload before it enters a prompt or cache. Only
    non-empty secrets are considered; values shorter than the placeholder are
    skipped to avoid mangling innocent text.
    """
    safe = text
    for secret in secrets:
        if not secret or len(secret) < 8:
            continue
        if secret in safe:
            safe = safe.replace(secret, "[REDACTED]")
    return safe


def configured_secrets(settings: Settings) -> tuple[str, ...]:
    """The credential pool used for defence-in-depth redaction."""
    return tuple(
        value
        for value in (
            settings.github_token,
            settings.database_url,
            settings.ai_api_key,
        )
        if value
    )


def secrets_present(text: str, secrets: Sequence[str]) -> tuple[str, ...]:
    """Secrets actually found in ``text`` (used by tests and as a guard)."""
    return tuple(
        secret for secret in secrets if secret and len(secret) >= 8 and secret in text
    )


def evidence_ids_in_text(text: str) -> list[str]:
    """Existing ``En`` ids referenced inside a model output."""
    return sorted(set(re.findall(r"\bE\d+\b", text)))


__all__ = [
    "MAX_PAYLOAD_CHARS",
    "UNTRUSTED_END",
    "UNTRUSTED_START",
    "configured_secrets",
    "evidence_ids_in_text",
    "normalize_excerpt",
    "normalize_payload",
    "redact_secrets",
    "secrets_present",
    "wrap_untrusted",
]
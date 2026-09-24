"""Evidence safety: normalization, untrusted wrapping, secret redaction."""

from __future__ import annotations

from github_radar.ai.safety import (
    configured_secrets,
    evidence_ids_in_text,
    normalize_excerpt,
    normalize_payload,
    redact_secrets,
    secrets_present,
    wrap_untrusted,
)
from tests.conftest import BASE_URL


def test_normalize_payload_strips_control_characters() -> None:
    assert normalize_payload("clean\x00\x1ftext") == "cleantext"


def test_normalize_payload_truncates_and_marks() -> None:
    out = normalize_payload("x" * 5000, max_chars=100)
    assert out.endswith("…[truncated]")
    assert len(out) <= len("x" * 100) + len(" …[truncated]")


def test_normalize_excerpt_normalizes_newlines_and_truncates() -> None:
    assert normalize_excerpt("a\r\nb\r\nc\nd") == "a\nb\nc\nd"
    out = normalize_excerpt("y" * 300, max_chars=50)
    assert out.endswith("…[truncated]")


def test_wrap_untrusted_delimiters_and_kind() -> None:
    wrapped = wrap_untrusted("README", "hello")
    assert wrapped.startswith("<untrusted-data> kind=README\n")
    assert wrapped.endswith("\n</untrusted-data>")


def test_redact_secrets_hides_credentials_not_short_values() -> None:
    assert "hunter2secret" not in redact_secrets(
        "token is hunter2secret", ["hunter2secret"]
    )
    # Secrets shorter than the placeholder are skipped entirely.
    assert redact_secrets("ok", ["ab"]) == "ok"
    assert "sk-long-key" not in redact_secrets("injected sk-long-key text", ["sk-long-key"])


def test_secrets_present_reports_found_secrets() -> None:
    found = secrets_present("db postgresql+asyncpg://u:p@h/db", ["postgresql+asyncpg://u:p@h/db"])
    assert found == ("postgresql+asyncpg://u:p@h/db",)


def test_configured_secrets_include_only_nonempty() -> None:
    from github_radar.config import Settings

    settings = Settings(
        github_token="ghp_long_secret_value",
        database_url="postgresql+asyncpg://u:p@localhost/radar",
        ai_api_key="sk-long-test-key",
        github_api_base_url=BASE_URL,
    )
    secrets = configured_secrets(settings)
    assert secrets == (
        "ghp_long_secret_value",
        "postgresql+asyncpg://u:p@localhost/radar",
        "sk-long-test-key",
    )


def test_evidence_ids_in_text_sorts_unique() -> None:
    assert evidence_ids_in_text("[E3] and [E1] then E2 and E1 again") == ["E1", "E2", "E3"]
    assert evidence_ids_in_text("no references") == []
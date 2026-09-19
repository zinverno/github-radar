"""Safety-guard tests for the destructive PostgreSQL integration fixtures.

``tests/integration/safety.py`` refuses to run schema-dropping fixtures when
``TEST_DATABASE_URL`` is unset, names the application database, is not a
test-looking database, or equals ``DATABASE_URL``. These tests exercise the
guard in isolation (no database required) and verify it never restricts the
production ``DATABASE_URL``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from tests.integration.safety import (
    UnsafeTestDatabaseError,
    extract_database_name,
    validate_test_database_url,
)

APP_URL = "postgresql+asyncpg://github_radar:secret@localhost:5432/github_radar"
TEST_URL = "postgresql+asyncpg://github_radar:secret@localhost:5432/github_radar_test"


def test_extract_database_name_parses_asyncpg_url() -> None:
    url = "postgresql+asyncpg://u:p@localhost:5432/radar_test"
    assert extract_database_name(url) == "radar_test"


def test_extract_database_name_handles_query_string() -> None:
    url = "postgresql+asyncpg://u:p@localhost/github_radar_test?sslmode=disable"
    assert extract_database_name(url) == "github_radar_test"


def test_accepts_github_radar_test_without_app_url() -> None:
    validate_test_database_url(test_url=TEST_URL)
    validate_test_database_url(test_url=TEST_URL, app_url=APP_URL)


def test_accepts_documented_test_name_conventions() -> None:
    for db in ("radar_test", "test_radar", "ci_github_radar_test"):
        validate_test_database_url(test_url=f"postgresql+asyncpg://u:p@h/{db}")


def test_rejects_missing_test_url() -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="not set"):
        validate_test_database_url(test_url="", app_url=APP_URL)
    with pytest.raises(UnsafeTestDatabaseError, match="not set"):
        validate_test_database_url(test_url="   ", app_url=APP_URL)


def test_rejects_url_without_database_name() -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="does not name a database"):
        validate_test_database_url(test_url="postgresql+asyncpg://u:p@localhost:5432/")


def test_rejects_normal_application_database() -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="normal application database"):
        validate_test_database_url(test_url="postgresql+asyncpg://u:p@localhost:5432/github_radar")


def test_rejects_test_url_equal_to_app_url() -> None:
    same = "postgresql+asyncpg://u:p@localhost:5432/radar_test"
    with pytest.raises(UnsafeTestDatabaseError, match="same database as DATABASE_URL"):
        validate_test_database_url(test_url=same, app_url=same)


def test_rejects_test_url_equal_to_app_database_even_with_different_credentials() -> None:
    app = "postgresql+asyncpg://other:pass@remote:5432/radar_test"
    test = "postgresql+asyncpg://u:p@localhost:5432/radar_test"
    with pytest.raises(UnsafeTestDatabaseError, match="same database as DATABASE_URL"):
        validate_test_database_url(test_url=test, app_url=app)


def test_rejects_non_test_looking_database_name() -> None:
    for db in ("radar", "github_radar_ci", "prod", "staging_radar"):
        with pytest.raises(UnsafeTestDatabaseError, match="does not look like a test database"):
            validate_test_database_url(test_url=f"postgresql+asyncpg://u:p@localhost:5432/{db}")


def test_guard_does_not_restrict_production_database_url() -> None:
    # The guard must never validate or reject the application URL itself.
    url = make_url(APP_URL)
    assert url.database == "github_radar"
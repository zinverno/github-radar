"""Safety guard for the destructive PostgreSQL integration fixtures.

The integration suite creates and drops whole schemas. It must NEVER be aimed
at the normal application database, so ``TEST_DATABASE_URL`` is validated
before any destructive setup or teardown.

This module belongs to the test infrastructure only — it adds no restriction
on the production ``DATABASE_URL``.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url


class UnsafeTestDatabaseError(Exception):
    """``TEST_DATABASE_URL`` is unsafe to use for destructive test fixtures."""


# The application's default database. Destructive tests must never target it.
_PROHIBITED_DATABASES = frozenset({"github_radar"})

# Explicitly test-looking names accepted verbatim.
_EXPLICIT_TEST_NAMES = frozenset({"github_radar_test"})

# Documented equivalent conventions for a test database name.
_TEST_NAME_SUFFIX = "_test"
_TEST_NAME_PREFIX = "test_"


def extract_database_name(url: str) -> str:
    """Return the database name parsed from ``url`` (empty when absent)."""
    return (make_url(url).database or "").strip()


def validate_test_database_url(*, test_url: str, app_url: str | None = None) -> None:
    """Refuse destructive test fixtures that target an unsafe database.

    Raises :class:`UnsafeTestDatabaseError` when ``test_url`` is empty, names
    no database, equals the application database, names the normal
    ``github_radar`` database, or does not look like a test database.
    """
    if not test_url or not test_url.strip():
        raise UnsafeTestDatabaseError(
            "TEST_DATABASE_URL is not set; refusing to run destructive "
            "PostgreSQL integration fixtures without an explicit test target."
        )

    test_db = extract_database_name(test_url)
    if not test_db:
        raise UnsafeTestDatabaseError(
            "TEST_DATABASE_URL does not name a database; refusing to run "
            "destructive integration fixtures against an unknown target."
        )

    if test_db in _PROHIBITED_DATABASES:
        raise UnsafeTestDatabaseError(
            f"TEST_DATABASE_URL targets {test_db!r}, the normal application "
            "database; refusing to run destructive integration fixtures."
        )

    if app_url and app_url.strip():
        app_db = extract_database_name(app_url)
        if app_db == test_db:
            raise UnsafeTestDatabaseError(
                f"TEST_DATABASE_URL targets the same database as DATABASE_URL "
                f"({test_db!r}); refusing to run destructive integration fixtures."
            )

    if not (
        test_db in _EXPLICIT_TEST_NAMES
        or test_db.endswith(_TEST_NAME_SUFFIX)
        or test_db.startswith(_TEST_NAME_PREFIX)
    ):
        raise UnsafeTestDatabaseError(
            f"TEST_DATABASE_URL database {test_db!r} does not look like a "
            "test database (expected e.g. 'github_radar_test'); refusing to "
            "run destructive integration fixtures."
        )


__all__ = ["UnsafeTestDatabaseError", "extract_database_name", "validate_test_database_url"]
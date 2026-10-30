from pathlib import Path

import pytest

from github_radar.recovery import pg_environment
from github_radar.services.automation import AutomationBlocked, CollectorCredentials
from github_radar.storage.connection import connection_options, direct_url

REMOTE = "postgresql+psycopg://radar_public:secret@ep-example.eu.aws.neon.tech/radar_beta"


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?sslmode=require",
        "?ssl=disable",
        "?sslmode=verify-ca",
        "?sslmode=verify-full&ssl=verify-full",
        "?sslmode=verify-full&host=evil.test",
        "?sslmode=verify-full&sslmode=disable",
        "?sslmode=verify-full&sslrootcert=relative.crt",
        "?sslmode=verify-full&channel_binding=prefer",
        "?sslmode=verify-full&channel_binding=require&channel_binding=require",
        "?sslmode=verify-full&sslmode=verify-full",
        "?sslmode=verify-full&channel_binding=",
        "?sslmode=verify-full&channel_binding=require%20",
        "?sslmode=verify-full&options=-crole=owner",
    ],
)
def test_external_tls_never_downgrades_or_ignores_options(query):
    with pytest.raises(ValueError):
        connection_options(REMOTE + query)
    with pytest.raises(ValueError):
        pg_environment(direct_url(REMOTE + query))


@pytest.mark.parametrize("suffix", ["", "&channel_binding=require"])
def test_verified_tls_and_backup_mapping(suffix, monkeypatch, tmp_path):
    keylog = tmp_path / "keys"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.2")
    monkeypatch.setenv("PGSSLMODE", "disable")
    value = REMOTE + "?sslmode=verify-full" + suffix
    url, args = connection_options(value)
    assert not url.query
    assert args["sslmode"] == "verify-full" and args["channel_binding"] == "require"
    assert args["ssl_min_protocol_version"] == "TLSv1.2"
    assert args["sslcertmode"] == "disable" and args["sslkeylogfile"] == ""
    assert Path(str(args["sslrootcert"])).is_file() and not keylog.exists()
    env = pg_environment(direct_url(value))
    assert env["PGSSLMODE"] == "verify-full" and "PGSSLROOTCERT" in env
    assert "PGHOSTADDR" not in env


def test_neon_pooler_refused_by_reader_collector_and_backup():
    value = REMOTE.replace("ep-example.", "ep-example-pooler.")
    with pytest.raises(ValueError):
        connection_options(value + "?sslmode=verify-full")
    with pytest.raises(AutomationBlocked):
        CollectorCredentials.from_environment(
            {
                "COLLECTOR_DATABASE_URL": value.replace("radar_public", "github_radar_collector"),
            }
        )


def test_loopback_also_requires_verified_tls_and_channel_binding():
    _, args = connection_options("postgresql+psycopg://test@127.0.0.1/github_radar_test")
    assert args["sslmode"] == "verify-full" and args["channel_binding"] == "require"


@pytest.mark.parametrize(
    "value",
    [
        REMOTE + "?sslmode=verify-full&",
        REMOTE + "#fragment",
        REMOTE.replace("secret@", "secret@ambiguous@"),
        REMOTE.replace("secret@", "bad%XY@"),
        REMOTE + " ",
        REMOTE.replace("radar_beta", "radar%00beta"),
        REMOTE.replace("psycopg", "asyncpg"),
    ],
)
def test_ambiguous_url_is_rejected_without_echoing_input(value):
    with pytest.raises(ValueError) as error:
        direct_url(value)
    assert "secret" not in str(error.value) and value not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("driver", "3.3.7"),
        ("implementation", "binary"),
        ("libpq", 170000),
        ("sqlalchemy", "2.0.55"),
    ],
)
def test_unreviewed_driver_internals_fail_closed(field, value, monkeypatch):
    import psycopg
    import sqlalchemy

    from github_radar.storage.db import make_engine

    if field == "driver":
        monkeypatch.setattr(psycopg, "__version__", value)
    elif field == "implementation":
        monkeypatch.setattr(psycopg.pq, "__impl__", value)
    elif field == "libpq":
        monkeypatch.setattr(psycopg.pq, "version", lambda: value)
    else:
        monkeypatch.setattr(sqlalchemy, "__version__", value)
    with pytest.raises(ValueError, match="Unreviewed PostgreSQL driver"):
        make_engine(REMOTE + "?sslmode=verify-full")


@pytest.mark.parametrize("limit", [0, -1, True, float("inf"), float("nan"), "5"])
async def test_invalid_driver_deadlines_fail_before_connect(limit):
    from github_radar.storage.deadlines import DeadlineConnection

    with pytest.raises(ValueError, match="finite and positive"):
        await DeadlineConnection.connect(io_seconds=limit)


async def test_libpq_environment_override_is_refused_without_leaking(monkeypatch):
    from github_radar.storage.deadlines import DeadlineConnection

    monkeypatch.setenv("PGPASSWORD", "do-not-render-this-secret")
    with pytest.raises(ValueError) as error:
        await DeadlineConnection.connect()
    assert "do-not-render" not in str(error.value)

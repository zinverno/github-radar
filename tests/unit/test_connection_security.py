import ssl

import pytest

from github_radar.recovery import pg_environment
from github_radar.services.automation import AutomationBlocked, CollectorCredentials
from github_radar.storage.connection import connection_options, direct_url

REMOTE = "postgresql+asyncpg://radar_public:secret@ep-example.eu.aws.neon.tech/radar_beta"


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
        "?sslmode=verify-full&channel_binding=require",
        "?sslmode=verify-full&options=-crole=owner",
    ],
)
def test_external_tls_never_downgrades_or_ignores_options(query):
    with pytest.raises(ValueError):
        connection_options(REMOTE + query)
    with pytest.raises(ValueError):
        pg_environment(direct_url(REMOTE + query))


@pytest.mark.parametrize("option", ["ssl", "sslmode"])
def test_verified_tls_and_backup_mapping(option, monkeypatch, tmp_path):
    keylog = tmp_path / "keys"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.2")
    monkeypatch.setenv("PGSSLMODE", "disable")
    value = REMOTE + f"?{option}=verify-full"
    url, context = connection_options(value)
    assert not url.query and isinstance(context, ssl.SSLContext)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.keylog_filename is None and not keylog.exists()
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


def test_loopback_is_explicitly_plaintext_only_without_tls_options():
    _, context = connection_options("postgresql+asyncpg://test@127.0.0.1/github_radar_test")
    assert context is False

import os
from unittest.mock import Mock

import pytest

from github_radar import public_server

VALID = {
    "DEPLOYMENT_MODE": "public",
    "PUBLIC_ALLOWED_HOSTS": '["radar.example.org"]',
    "DATABASE_URL": "postgresql+psycopg://radar_public:secret@db.example.org/beta?sslmode=verify-full",
    "PORT": "10000",
}


@pytest.mark.parametrize(
    "changes",
    [
        {"DEPLOYMENT_MODE": "local"},
        {"PUBLIC_ALLOWED_HOSTS": "[]"},
        {"PUBLIC_ALLOWED_HOSTS": '["*.example.org"]'},
        {"PORT": ""},
        {"PORT": "0"},
        {"PORT": "65536"},
        {"PORT": "1 --reload"},
        {"DATABASE_URL": VALID["DATABASE_URL"].replace("radar_public", "owner")},
        {"DATABASE_URL": VALID["DATABASE_URL"].replace("verify-full", "require")},
        *(
            {name: "SECRET_SENTINEL"}
            for name in [
                "GITHUB_TOKEN",
                "GH_TOKEN",
                "AI_API_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "COLLECTOR_DATABASE_URL",
                "COLLECTOR_GITHUB_TOKEN",
                "WORKSPACE_ADMIN_TOKEN",
                "MIGRATION_DATABASE_URL",
                "PGPASSWORD",
                "PGPASSFILE",
                "PGSERVICE",
            ]
        ),
    ],
)
def test_invalid_startup_redacts_and_never_creates_app(monkeypatch, changes):
    monkeypatch.setattr(os, "environ", VALID | changes)
    create = Mock(side_effect=AssertionError("app created"))
    monkeypatch.setattr(public_server, "create_app", create)
    with pytest.raises(SystemExit) as exc:
        public_server.main()
    assert "SECRET_SENTINEL" not in str(exc.value)
    create.assert_not_called()


def test_single_bounded_process_ignores_proxy_worker_and_debug_environment(monkeypatch):
    monkeypatch.setattr(
        os,
        "environ",
        VALID
        | {
            "WEB_CONCURRENCY": "99",
            "FORWARDED_ALLOW_IPS": "*",
            "LOG_LEVEL": "DEBUG",
        },
    )
    app, run = Mock(), Mock()
    monkeypatch.setattr(public_server, "create_app", Mock(return_value=app))
    monkeypatch.setattr("uvicorn.run", run)
    public_server.main()
    options = run.call_args.kwargs
    assert options["port"] == 10000 and options["workers"] == 1
    assert options["proxy_headers"] is False and options["access_log"] is False
    assert options["limit_concurrency"] == 4 and options["log_level"] == "warning"

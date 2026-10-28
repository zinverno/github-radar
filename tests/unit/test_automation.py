"""Scheduling guards must fail before acquiring a connection or sending HTTP."""

from __future__ import annotations

import asyncio
import json
import os
import select
import signal
import ssl
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from github_radar.cli.automation import app
from github_radar.services import automation as policy
from github_radar.storage.collection import CollectionLockError

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


def approved_env():
    return {
        "RADAR_COLLECTION_LIVE": "true",
        "RADAR_REFRESH_ENABLED": "true",
        "RADAR_DISCOVERY_ENABLED": "true",
        "COLLECTOR_DIRECT_CONNECTION": "true",
        "GITHUB_ACTIONS": "true",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": "zinverno/github-radar",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "COLLECTOR_GITHUB_TOKEN": "fake-secret",
        "COLLECTOR_DATABASE_URL": "postgresql+asyncpg://github_radar_collector:fake@db.invalid/test",
        "RADAR_DISCOVERY_PLAN_SHA256": policy.discovery_plan(NOW)[1],
    }


@pytest.mark.parametrize("kind", ["refresh", "discover"])
def test_preview_never_constructs_credentials_or_engine(kind, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Dry-run touched credential/database boundary")

    monkeypatch.setattr(policy.CollectorCredentials, "from_environment", forbidden)
    result = CliRunner().invoke(app, ["run", kind])
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["mode"] == "dry-run"
    if kind == "refresh":
        assert output["budget"] == {
            "total": 120,
            "repository_details": 120,
            "search": 0,
            "contributors": 0,
            "profiles": 0,
        }
        assert output["execution"]["max_repositories"] == 100
        assert output["execution"]["max_seconds"] == 300
        assert output["min_refresh_hours"] == 24
    else:
        assert output["plan"]["candidate_slots"] == 18
        assert output["plan"]["execution"]["max_new_repositories"] == 10
        assert output["plan"]["budget"]["profiles"] == 0


@pytest.mark.parametrize("kind", ["refresh", "discover"])
def test_console_preview_does_not_load_application_settings(kind, tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=invalid://must-not-read\nLOG_LEVEL=DEBUG\n")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from github_radar.cli.app import main; main()",
            "automation",
            "run",
            kind,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "DATABASE_URL": "invalid://must-not-read", "LOG_LEVEL": "DEBUG"},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["mode"] == "dry-run"
    assert "must-not-read" not in result.stdout + result.stderr


@pytest.mark.parametrize("key", list(approved_env()))
def test_every_live_gate_is_required(key, monkeypatch):
    for name, value in approved_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(key)

    def forbidden(*args, **kwargs):
        pytest.fail("Missing gate allowed a database connection")

    monkeypatch.setattr(policy.CollectorCredentials, "engine", forbidden)
    kind = "refresh" if key == "RADAR_REFRESH_ENABLED" else "discover"
    result = CliRunner().invoke(app, ["run", kind, "--live"])
    assert result.exit_code == 2, result.output
    assert "fake-secret" not in result.output and "fake@" not in result.output


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_EVENT_NAME", "pull_request_target"),
        ("GITHUB_REF", "refs/heads/untrusted"),
        ("GITHUB_REPOSITORY", "someone/fork"),
        ("GITHUB_RUN_ATTEMPT", "2"),
        ("RADAR_COLLECTION_LIVE", "TRUE"),
        ("COLLECTOR_DIRECT_CONNECTION", "false"),
    ],
)
def test_untrusted_context_and_reruns_refused(key, value):
    env = {**approved_env(), key: value}
    assert policy.activation_reasons("refresh", env)


def test_activations_are_independent():
    env = approved_env()
    env["RADAR_DISCOVERY_ENABLED"] = "false"
    assert not policy.activation_reasons("refresh", env)
    assert policy.activation_reasons("discover", env)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-url",
        "postgresql://github_radar_collector:x@db.invalid/test",
        "postgresql+asyncpg://public_reader:x@db.invalid/test",
        "postgresql+asyncpg://github_radar_collector:x@db.invalid/test?ssl=disable",
        "postgresql+asyncpg://github_radar_collector@db.invalid/test",
    ],
)
def test_invalid_credentials_are_not_echoed(value):
    with pytest.raises(policy.AutomationBlocked) as exc:
        policy.CollectorCredentials.from_environment({"COLLECTOR_DATABASE_URL": value})
    assert "db.invalid" not in str(exc.value)


def test_tls_verifies_hostname_and_cannot_keylog(monkeypatch, tmp_path):
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "keylog"))
    captured = {}

    def create(url, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(policy, "create_async_engine", create)
    credentials = policy.CollectorCredentials.from_environment(approved_env())
    credentials.engine()
    context = captured["connect_args"]["ssl"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert not (tmp_path / "keylog").exists()
    assert captured["connect_args"]["timeout"] == 10
    assert captured["hide_parameters"] is True
    assert "fake" not in repr(credentials)


def test_settings_ignore_local_env_and_redirects(monkeypatch):
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://untrusted.invalid")
    monkeypatch.setenv("HTTP_MAX_RETRIES", "10")
    monkeypatch.setenv("AI_API_KEY", "do-not-load")
    settings = policy.CollectorCredentials.from_environment(approved_env()).settings(
        policy.REFRESH_BUDGET
    )
    assert settings.github_api_base_url == "https://api.github.com"
    assert settings.http_max_retries == 3
    assert settings.ai_api_key == settings.workspace_admin_token.get_secret_value() == ""


@pytest.mark.parametrize(
    "edit",
    [
        ("total = 36", "total = 37"),
        ("profiles = 0", "profiles = 1"),
        ("contributors_limit = 0", "contributors_limit = 1"),
        ("max_seconds = 300", "max_seconds = 301"),
        ("max_candidates = 18", "max_candidates = 19"),
    ],
)
def test_invalid_plan_bounds_refuse_before_transport(edit, monkeypatch, tmp_path):
    path = tmp_path / "catalog.toml"
    path.write_text(policy.CATALOG.read_text().replace(*edit))
    monkeypatch.setattr(policy, "CATALOG", path)
    with pytest.raises(policy.AutomationBlocked):
        policy.discovery_plan(NOW)


def test_changed_query_requires_new_operator_digest(monkeypatch, tmp_path):
    for key, value in approved_env().items():
        monkeypatch.setenv(key, value)
    changed = tmp_path / "catalog.toml"
    changed.write_text(policy.CATALOG.read_text().replace("topic:ai-agents", "topic:unreviewed"))
    monkeypatch.setattr(policy, "CATALOG", changed)
    result = CliRunner().invoke(app, ["run", "discover", "--live"])
    assert result.exit_code == 2 and "digest" in result.output


def test_corrupt_catalog_is_generic_failure(monkeypatch, tmp_path):
    path = tmp_path / "catalog.toml"
    path.write_text("fake-secret = [invalid")
    monkeypatch.setattr(policy, "CATALOG", path)
    result = CliRunner().invoke(app, ["run", "discover"])
    assert result.exit_code == 1 and "fake-secret" not in result.output


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (RuntimeError("secret-dsn-and-token"), 1),
        (TimeoutError("secret-dsn-and-token"), 1),
        (CollectionLockError("secret-dsn-and-token"), 1),
        (asyncio.CancelledError(), 130),
    ],
)
def test_execution_failures_are_redacted(failure, code, monkeypatch):
    for key, value in approved_env().items():
        monkeypatch.setenv(key, value)

    def fail(self):
        raise failure

    monkeypatch.setattr(policy.CollectorCredentials, "engine", fail)
    result = CliRunner().invoke(app, ["run", "refresh", "--live"])
    assert result.exit_code == code, result.output
    assert "secret-dsn-and-token" not in result.output and "Traceback" not in result.output


def test_periods_and_acknowledgments():
    assert policy.period_start("refresh", NOW) == NOW.replace(hour=0)
    assert policy.period_start("discover", NOW).isoformat() == "2026-10-26T00:00:00+00:00"
    assert not policy.acknowledged_runs("")
    with pytest.raises(policy.AutomationBlocked):
        policy.acknowledged_runs("not-a-uuid")


@pytest.mark.parametrize(
    ("kind", "flag", "cron"),
    [
        ("refresh", "RADAR_REFRESH_ENABLED", "17 3 * * *"),
        ("discover", "RADAR_DISCOVERY_ENABLED", "43 4 * * 0"),
    ],
)
def test_workflow_security_contract(kind, flag, cron):
    path = Path(f".github/workflows/collection-{kind}.yml")
    # BaseLoader preserves YAML's 'on' key and scalar spelling for exact policy checks.
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"schedule", "workflow_dispatch"}
    assert workflow["on"]["schedule"] == [{"cron": cron}]
    assert workflow["on"]["workflow_dispatch"]["inputs"]["live"]["default"] == "false"
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "github-radar-collection",
        "cancel-in-progress": "false",
    }
    jobs = workflow["jobs"]
    assert jobs["collect"]["environment"] == "collection"
    for job in jobs.values():
        assert "github.ref == 'refs/heads/main'" in job["if"]
        assert "github.repository == 'zinverno/github-radar'" in job["if"]
        assert int(job["timeout-minutes"]) <= 8
        assert "env" not in job
        for step in job["steps"]:
            if "uses" in step:
                assert len(step["uses"].split("@")[1]) == 40
            if step.get("uses", "").startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] == "false"
            assert "${{ inputs." not in step.get("run", "")
    plan_steps = jobs["plan"]["steps"]
    assert "secrets." not in json.dumps(jobs["plan"])
    gate = next(step for step in plan_steps if step.get("id") == "gate")
    assert gate["env"]["JOB_ENABLED"] == "${{ vars." + flag + " }}"
    assert gate["env"]["LIVE_ENABLED"] == "${{ vars.RADAR_COLLECTION_LIVE }}"
    assert "needs.plan.outputs.live == 'true'" in jobs["collect"]["if"]
    collector = next(s for s in jobs["collect"]["steps"] if s.get("id") == "collection")
    assert f"automation run {kind} --live" in collector["run"]
    assert collector["env"]["COLLECTOR_GITHUB_TOKEN"] == "${{ secrets.COLLECTOR_GITHUB_TOKEN }}"
    assert "continue-on-error" not in json.dumps(workflow)


@pytest.mark.parametrize("kind", ["refresh", "discover"])
@pytest.mark.parametrize(
    "disabled", [None, "JOB_ENABLED", "LIVE_ENABLED", "LIVE_REQUESTED", "rerun"]
)
def test_actual_workflow_gate_script(kind, disabled, monkeypatch, tmp_path):
    workflow = yaml.load(
        Path(f".github/workflows/collection-{kind}.yml").read_text(), Loader=yaml.BaseLoader
    )
    gate = next(s for s in workflow["jobs"]["plan"]["steps"] if s.get("id") == "gate")
    code = gate["run"].split("python - <<'PY'\n")[1].split("\nPY")[0]
    for key in ("JOB_ENABLED", "LIVE_ENABLED", "LIVE_REQUESTED"):
        monkeypatch.setenv(key, "false" if key == disabled else "true")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2" if disabled == "rerun" else "1")
    output, summary = tmp_path / "output", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    exec(compile(code, "workflow-gate", "exec"), {})
    assert output.read_text() == f"live={str(disabled is None).lower()}\n"
    assert ("SKIPPED" in summary.read_text()) is (disabled is not None)


@pytest.mark.parametrize("kind", ["refresh", "discover"])
@pytest.mark.parametrize("exit_code", [0, 1, 2, 130])
def test_workflow_preserves_collector_exit_through_tee(kind, exit_code, tmp_path):
    workflow = yaml.load(
        Path(f".github/workflows/collection-{kind}.yml").read_text(), Loader=yaml.BaseLoader
    )
    job = workflow["jobs"]["collect"]
    step = next(s for s in job["steps"] if s.get("id") == "collection")
    shell = step.get(
        "shell", job.get("defaults", workflow.get("defaults", {})).get("run", {}).get("shell")
    )
    assert shell in (None, "bash")
    # GitHub's unspecified shell is bash -e; explicit bash additionally enables pipefail.
    command = ["bash", "--noprofile", "--norc", "-e"]
    if shell == "bash":
        command += ["-o", "pipefail"]
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(f'#!/bin/sh\nprintf "collector-result\\n"\nexit {exit_code}\n')
    fake_uv.chmod(0o700)
    result = subprocess.run(
        [*command, "-c", step["run"]],
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": f"{tmp_path}{os.pathsep}{os.defpath}", "RUNNER_TEMP": str(tmp_path)},
    )
    assert (tmp_path / "collection-result.json").read_text() == "collector-result\n"
    assert result.returncode == exit_code, result.stdout + result.stderr


def test_sigterm_cancels_cli_and_disposes_without_real_connections():
    code = """
import asyncio
from github_radar.cli import automation
class Engine:
    async def dispose(self):
        print("disposed", flush=True)
async def fake_run(*args, **kwargs):
    print("ready", flush=True)
    await asyncio.Future()
automation.CollectorCredentials.engine = lambda self: Engine()
automation.run_scheduled = fake_run
automation.app(["run", "refresh", "--live"])
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, **approved_env()},
    )
    try:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 10)[0], "child did not become ready"
        assert process.stdout.readline().strip() == "ready"
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, (stdout, stderr)
        assert '"status": "interrupted"' in stdout and "disposed" in stdout
        assert "fake-secret" not in stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

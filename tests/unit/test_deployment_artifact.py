import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "credential",
    [
        "DATABASE_URL", "MIGRATION_DATABASE_URL", "GITHUB_TOKEN", "AI_API_KEY",
        "PGPASSWORD", "PGPASSFILE", "PGSERVICE",
    ],
)
def test_build_refuses_credentials_before_dependency_commands(credential):
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(
        [bash, str(ROOT / "deploy/build.sh")],
        env={"PATH": str(Path(sys.executable).parent), credential: "PRIVATE_BUILD_SENTINEL"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Build refused: credentials must be absent" in result.stderr
    assert "PRIVATE_BUILD_SENTINEL" not in result.stdout + result.stderr


@pytest.mark.parametrize("extra_asset", [None, "secret.map", ".env", "data.json"])
def test_build_excludes_runtime_secrets_and_unreviewed_files(tmp_path, monkeypatch, extra_asset):
    build = runpy.run_path(str(ROOT / "deploy/build_frontend.py"))
    build["main"].__globals__["ROOT"] = tmp_path
    web = tmp_path / "web"
    web.mkdir()
    for name in (
        "package.json",
        "package-lock.json",
        "index.html",
        "vite.config.ts",
        "tsconfig.json",
    ):
        (web / name).write_text("reviewed input")
    (web / "src").mkdir()
    (web / "src/main.ts").write_text("reviewed source")
    (web / ".env.production").write_text("VITE_SECRET=PRIVATE_BUILD_SENTINEL")
    (web / "public").mkdir()
    (web / "public/private.json").write_text("PRIVATE_BUILD_SENTINEL")
    monkeypatch.setenv("VITE_SECRET", "PRIVATE_BUILD_SENTINEL")
    monkeypatch.setenv("DATABASE_URL", "PRIVATE_BUILD_SENTINEL")
    monkeypatch.setenv("NODE_OPTIONS", "PRIVATE_BUILD_SENTINEL")

    def run(command, *, cwd, env, check):
        assert "PRIVATE_BUILD_SENTINEL" not in str(env)
        assert not (cwd / "public").exists() and not (cwd / ".env.production").exists()
        assert check
        if command[1] == "run":
            dist = cwd / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("reviewed output")
            if extra_asset:
                (dist / extra_asset).write_text("unreviewed")

    monkeypatch.setattr("subprocess.run", run)
    if extra_asset:
        with pytest.raises(RuntimeError, match="Unexpected public artifact"):
            build["main"]()
        assert not (web / "dist").exists()
    else:
        build["main"]()
        assert list((web / "dist").iterdir()) == [web / "dist/index.html"]


def test_render_blueprint_is_single_manual_public_service():
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    assert set(blueprint) == {"services", "previews"}
    assert blueprint["previews"]["generation"] == "off"
    [service] = blueprint["services"]
    assert service["autoDeployTrigger"] == "off"
    assert service["runtime"] == "python" and service["plan"] == "free"
    assert service["numInstances"] == 1 and "preDeployCommand" not in service
    assert service["healthCheckPath"] == "/api/v1/health"
    env = {entry["key"]: entry for entry in service["envVars"]}
    assert env["DEPLOYMENT_MODE"]["value"] == "public"
    assert env["PUBLIC_ALLOWED_HOSTS"] == {"key": "PUBLIC_ALLOWED_HOSTS", "sync": False}
    assert "DATABASE_URL" not in env
    assert not any("TOKEN" in key or "AI_" in key or "COLLECTOR_" in key for key in env)

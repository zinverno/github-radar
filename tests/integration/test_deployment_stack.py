"""Real TLS, role SQL, recovery and HTTP on a newly created, disposable local cluster only."""

from __future__ import annotations

import asyncio
import json
import os
import re
import resource
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, TimeoutError

from github_radar.initialize import initialize
from github_radar.recovery import RESTORE_TARGET, fingerprint, pg_environment, recovery_drill
from github_radar.services.automation import CollectorCredentials, check_collector_role
from github_radar.storage.collection import locked_collection_session
from github_radar.storage.db import make_engine, make_session_factory
from github_radar.storage.permissions import audit_application_role
from tests.unit.test_api_routes import _seed

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="integration opt-in unset"),
]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60, **kwargs)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    for binary in ("initdb", "pg_ctl", "psql", "pg_dump", "pg_restore", "openssl"):
        assert shutil.which(binary), f"Deployment verification requires {binary}"
    directory = tmp_path_factory.mktemp("radar-deployment")
    data, ca = directory / "data", directory / "ca.crt"
    password = secrets.token_hex(24)
    pwfile = directory / "password"
    pwfile.write_text(password)
    pwfile.chmod(0o600)
    run(
        [
            "initdb",
            "-D",
            str(data),
            "-U",
            "radar_owner",
            "--auth-local=trust",
            "--auth-host=scram-sha-256",
            "--pwfile",
            str(pwfile),
        ]
    )
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=Disposable Radar Test CA",
            "-keyout",
            str(directory / "ca.key"),
            "-out",
            str(ca),
            "-addext",
            "basicConstraints=critical,CA:TRUE",
        ]
    )
    run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-keyout",
            str(data / "server.key"),
            "-out",
            str(directory / "server.csr"),
        ]
    )
    (directory / "extensions").write_text(
        "subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n"
    )
    run(
        [
            "openssl",
            "x509",
            "-req",
            "-days",
            "1",
            "-in",
            str(directory / "server.csr"),
            "-CA",
            str(ca),
            "-CAkey",
            str(directory / "ca.key"),
            "-CAcreateserial",
            "-extfile",
            str(directory / "extensions"),
            "-out",
            str(data / "server.crt"),
        ]
    )
    (data / "server.key").chmod(0o600)
    port = free_port()
    with (data / "postgresql.conf").open("a") as stream:
        stream.write(
            f"\nlisten_addresses='127.0.0.1'\nport={port}\n"
            f"unix_socket_directories='{directory}'\nssl=on\n"
        )
    run(["pg_ctl", "-D", str(data), "-l", str(directory / "postgres.log"), "-w", "start"])
    base = f"postgresql+asyncpg://radar_owner:{password}@localhost:{port}"
    tls = f"?sslmode=verify-full&sslrootcert={ca}"
    env = pg_environment(make_url(base + "/postgres" + tls))
    try:
        run(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-c", "CREATE DATABASE github_radar_test"],
            env=env,
        )
        run(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-c", f"CREATE DATABASE {RESTORE_TARGET}"],
            env=env,
        )
        url = base + "/github_radar_test" + tls
        asyncio.run(initialize(url, "github_radar_test"))
        run(
            [
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-v",
                "target_database=github_radar_test",
                "-f",
                str(ROOT / "deploy/roles.sql"),
            ],
            env=pg_environment(make_url(url)),
        )
        # Synthetic, cluster-local passwords never appear in argv or test output.
        result = subprocess.run(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1"],
            env=pg_environment(make_url(url)),
            input=f"ALTER ROLE radar_public PASSWORD '{password}';\n"
            f"ALTER ROLE github_radar_collector PASSWORD '{password}';\n",
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, "Test password setup failed"
        yield {
            "owner": url,
            "reader": url.replace("radar_owner:", "radar_public:"),
            "collector": url.replace("radar_owner:", "github_radar_collector:").split("?")[0],
            "ca": str(ca),
            "directory": directory,
            "restore": base.replace("localhost", "127.0.0.1") + "/" + RESTORE_TARGET,
        }
    finally:
        run(["pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop"])


def collector_credentials(cluster, **changes):
    return CollectorCredentials.from_environment(
        {
            "COLLECTOR_DATABASE_URL": cluster["collector"],
            "COLLECTOR_CA_PEM": Path(cluster["ca"]).read_text(),
        }
        | changes
    )


async def test_initializer_refuses_existing_database_and_bad_confirmation(cluster):
    for confirmation in ("wrong_database", "github_radar_test"):
        with pytest.raises(ValueError):
            await initialize(cluster["owner"], confirmation)


async def test_pg_prefixed_user_schema_blocks_initialization_and_restore(cluster):
    engine = make_engine(cluster["restore"])
    archive = cluster["directory"] / "occupied-schema.dump"
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE SCHEMA pgprivate"))
            await connection.execute(text("CREATE TABLE pgprivate.secret (value text)"))
        with pytest.raises(ValueError, match="not empty"):
            await initialize(cluster["restore"], RESTORE_TARGET)
        with pytest.raises(ValueError, match="not empty"):
            await recovery_drill(cluster["owner"], cluster["restore"], RESTORE_TARGET, archive)
        assert not archive.exists()
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
                is None
            )
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE pgprivate.secret"))
            await connection.execute(text("DROP SCHEMA pgprivate"))
        await engine.dispose()


@pytest.mark.parametrize("revision", [None, "0005"])
async def test_role_script_refuses_missing_or_wrong_revision(cluster, revision):
    owner = make_engine(cluster["owner"])
    try:
        async with owner.begin() as connection:
            await connection.execute(text("DELETE FROM alembic_version"))
            if revision:
                await connection.execute(
                    text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision}
                )
        result = subprocess.run(
            [
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-v",
                "target_database=github_radar_test",
                "-f",
                str(ROOT / "deploy/roles.sql"),
            ],
            env=pg_environment(make_url(cluster["owner"])),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0 and "Expected migration 0006" in result.stderr
    finally:
        async with owner.begin() as connection:
            await connection.execute(text("DELETE FROM alembic_version"))
            await connection.execute(text("INSERT INTO alembic_version VALUES ('0006')"))
        await owner.dispose()


async def test_public_pool_refuses_a_third_connection(cluster):
    engine = make_engine(cluster["reader"], public=True)
    try:
        async with engine.connect() as first, engine.connect():
            assert (
                await first.scalar(
                    text("SELECT rolconnlimit FROM pg_roles WHERE rolname=current_user")
                )
                == 4
            )
            with pytest.raises(TimeoutError):
                await engine.connect()
    finally:
        await engine.dispose()


async def test_real_verified_reader_and_session_stable_collector(cluster):
    reader = make_engine(cluster["reader"], public=True)
    collector = collector_credentials(cluster).engine()
    try:
        async with make_session_factory(reader)() as session:
            await audit_application_role(session)
            assert await session.scalar(text("SHOW transaction_read_only")) == "on"
            assert await session.scalar(
                text("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")
            )
        async with locked_collection_session(collector) as session:
            await check_collector_role(session)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.commit()
            assert await session.scalar(text("SELECT pg_backend_pid()")) == pid
            assert await session.scalar(
                text("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")
            )
    finally:
        await reader.dispose()
        await collector.dispose()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM repositories",
        "SELECT notes FROM workspace_repositories",
        "SELECT result_json FROM ai_artifacts",
        "SELECT * FROM collection_runs",
        "SELECT nextval('repositories_id_seq')",
        "SET ROLE radar_owner",
    ],
)
async def test_reader_privileges_hold_even_after_disabling_readonly(cluster, sql):
    engine = make_engine(cluster["reader"])
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ WRITE"))
            with pytest.raises(DBAPIError) as error:
                await connection.execute(text(sql))
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("role", ["reader", "collector"])
@pytest.mark.parametrize("failure", ["untrusted_ca", "wrong_hostname"])
async def test_actual_tls_failures_do_not_connect(cluster, role, failure):
    if role == "reader":
        url = cluster["reader"]
        if failure == "untrusted_ca":
            url = url.split("&sslrootcert")[0]
        else:
            url = url.replace("localhost", "127.0.0.1")
        engine = make_engine(url, public=True)
    else:
        changes = (
            {"COLLECTOR_CA_PEM": ""}
            if failure == "untrusted_ca"
            else {
                "COLLECTOR_DATABASE_URL": cluster["collector"].replace("localhost", "127.0.0.1"),
            }
        )
        engine = collector_credentials(cluster, **changes).engine()
    try:
        with pytest.raises(Exception, match="certificate verify failed"):
            async with engine.connect():
                pytest.fail("Unverified TLS connected")
    finally:
        await engine.dispose()


@pytest.mark.parametrize("role", ["radar_public", "github_radar_collector"])
@pytest.mark.parametrize(
    "grant, revoke",
    [
        (
            "GRANT SELECT (notes) ON workspace_repositories TO {role}",
            "REVOKE SELECT (notes) ON workspace_repositories FROM {role}",
        ),
        ("GRANT SELECT ON ai_artifacts TO PUBLIC", "REVOKE SELECT ON ai_artifacts FROM PUBLIC"),
        ("GRANT UPDATE ON developers TO {role}", "REVOKE UPDATE ON developers FROM {role}"),
        (
            "GRANT USAGE ON SEQUENCE developers_id_seq TO {role}",
            "REVOKE USAGE ON SEQUENCE developers_id_seq FROM {role}",
        ),
        ("GRANT radar_owner TO {role}", "REVOKE radar_owner FROM {role}"),
        ("GRANT CREATE ON SCHEMA public TO {role}", "REVOKE CREATE ON SCHEMA public FROM {role}"),
        (
            "GRANT TEMP ON DATABASE github_radar_test TO PUBLIC",
            "REVOKE TEMP ON DATABASE github_radar_test FROM PUBLIC",
        ),
        (
            "ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO {role}",
            "ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM {role}",
        ),
        (
            "CREATE FUNCTION public.unsafe() RETURNS int LANGUAGE sql "
            "SECURITY DEFINER AS 'SELECT 1'; "
            "GRANT EXECUTE ON FUNCTION public.unsafe() TO PUBLIC",
            "DROP FUNCTION public.unsafe()",
        ),
        (
            "GRANT SELECT ON repositories TO {role} WITH GRANT OPTION",
            "REVOKE GRANT OPTION FOR SELECT ON repositories FROM {role}",
        ),
        *(
            (
                "CREATE SCHEMA pgprivate; GRANT USAGE ON SCHEMA pgprivate TO {role}; " + sql,
                cleanup + "; DROP SCHEMA pgprivate",
            )
            for sql, cleanup in [
                (
                    "CREATE TABLE pgprivate.secret (value text); "
                    "GRANT SELECT ON pgprivate.secret TO {role}",
                    "DROP TABLE pgprivate.secret",
                ),
                ("GRANT CREATE ON SCHEMA pgprivate TO {role}", "SELECT 1"),
                (
                    "CREATE FUNCTION pgprivate.unsafe() RETURNS int LANGUAGE sql "
                    "AS 'SELECT 1'; GRANT EXECUTE ON FUNCTION pgprivate.unsafe() TO PUBLIC",
                    "DROP FUNCTION pgprivate.unsafe()",
                ),
                (
                    "CREATE SEQUENCE pgprivate.secret; "
                    "GRANT USAGE ON SEQUENCE pgprivate.secret TO {role}",
                    "DROP SEQUENCE pgprivate.secret",
                ),
            ]
        ),
        (
            "GRANT MAINTAIN ON repositories TO {role}",
            "REVOKE MAINTAIN ON repositories FROM {role}",
        ),
        (
            "GRANT SET ON PARAMETER session_replication_role TO {role}",
            "REVOKE SET ON PARAMETER session_replication_role FROM {role}",
        ),
        (
            "GRANT ALTER SYSTEM ON PARAMETER log_statement TO PUBLIC",
            "REVOKE ALTER SYSTEM ON PARAMETER log_statement FROM PUBLIC",
        ),
        (
            "GRANT USAGE ON SCHEMA public TO {role} WITH GRANT OPTION",
            "REVOKE GRANT OPTION FOR USAGE ON SCHEMA public FROM {role}",
        ),
        (
            "GRANT CONNECT ON DATABASE github_radar_test TO {role} WITH GRANT OPTION",
            "REVOKE GRANT OPTION FOR CONNECT ON DATABASE github_radar_test FROM {role}",
        ),
    ],
)
async def test_effective_permissions_fail_closed(cluster, role, grant, revoke):
    env = pg_environment(make_url(cluster["owner"]))
    if "MAINTAIN" in grant:
        version = run(["psql", "-XAt", "-c", "SHOW server_version_num"], env=env)
        if int(version) < 170000:
            pytest.skip("MAINTAIN privilege was introduced in PostgreSQL 17")
    run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-c", grant.format(role=role)], env=env)
    collector = role == "github_radar_collector"
    engine = (
        collector_credentials(cluster).engine() if collector else make_engine(cluster["reader"])
    )
    try:
        async with make_session_factory(engine)() as session:
            with pytest.raises(ValueError):
                await audit_application_role(session, collector=collector)
    finally:
        await engine.dispose()
        run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-c", revoke.format(role=role)], env=env)


async def test_external_tls_backup_and_restore_never_overwrite(cluster):
    owner = make_engine(cluster["owner"])
    try:
        async with make_session_factory(owner)() as session:
            await _seed(session)
            await session.execute(
                text("""
                INSERT INTO workspace_developers
                  (entity_id, saved, notes, relationship_status, version, updated_at)
                SELECT id, true, 'PRIVATE_DEPLOYMENT_SENTINEL', 'interested', repeat('b',32), now()
                FROM developers LIMIT 1
            """)
            )
            await session.commit()
    finally:
        await owner.dispose()
    archive = cluster["directory"] / "independent.dump"
    report = await recovery_drill(cluster["owner"], cluster["restore"], RESTORE_TARGET, archive)
    assert report["all_table_rows_match"] and report["constraints_valid"]
    assert report["counts"]["repositories"] == 3
    assert report["counts"]["workspace_developers"] == 1
    assert report["revision"] == "0006" and archive.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="not empty"):
        await recovery_drill(
            cluster["owner"],
            cluster["restore"],
            RESTORE_TARGET,
            cluster["directory"] / "forbidden.dump",
        )
    assert not (cluster["directory"] / "forbidden.dump").exists()


def memory_limit():
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))


async def test_production_http_spa_privacy_errors_and_reconnect(cluster):
    assert (ROOT / "web/dist/index.html").is_file(), "Build production frontend before smoke"
    owner = make_engine(cluster["owner"])
    async with make_session_factory(owner)() as session:
        await _seed(session)
        await session.execute(
            text("""
            INSERT INTO workspace_repositories
              (entity_id, saved, notes, relationship_status, version, updated_at)
            SELECT id, true, 'PRIVATE_DEPLOYMENT_SENTINEL', 'interested', repeat('a',32), now()
            FROM repositories LIMIT 1
        """)
        )
        await session.commit()
    async with owner.connect() as conn:
        before = await fingerprint(conn)
    port = free_port()
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "src"),
        "DEPLOYMENT_MODE": "public",
        "PUBLIC_ALLOWED_HOSTS": '["radar.test"]',
        "DATABASE_URL": cluster["reader"],
        "PORT": str(port),
    }
    log = cluster["directory"] / "web.log"
    with log.open("w") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "github_radar.public_server"],
            cwd=ROOT,
            env=env,
            stdout=stream,
            stderr=stream,
            preexec_fn=memory_limit,
        )
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Host": "radar.test"}, trust_env=False
        ) as client:
            for _ in range(100):
                assert process.poll() is None, log.read_text()
                try:
                    if (await client.get("/api/v1/health")).status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(0.1)
            else:
                pytest.fail("Public startup timed out")
            for path in ("/", "/repositories", "/repositories/octo/alpha", "/topics"):
                response = await client.get(path)
                assert response.status_code == 200 and '<div id="root">' in response.text
                assert response.headers["strict-transport-security"] == "max-age=31536000"
                assert response.headers["x-frame-options"] == "DENY"
            for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', response.text):
                assert (await client.get(asset)).status_code == 200
            for path in (
                "/api/v1/ready",
                "/api/v1/status",
                "/api/v1/repositories",
                "/api/v1/explore",
                "/api/v1/developers",
                "/api/v1/ecosystem",
            ):
                response = await client.get(path)
                assert response.status_code == 200, (path, response.text)
                assert "PRIVATE_DEPLOYMENT_SENTINEL" not in response.text
            for path in (
                "/workspace",
                "/ai",
                "/api/v1/workspace/session",
                "/api/v1/ai/status",
                "/api/v1/docs",
                "/api/v1/openapi.json",
            ):
                assert (
                    await client.get(
                        path, headers={"X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "https"}
                    )
                ).status_code == 404
            assert (
                await client.get(
                    "/api/v1/health",
                    headers={
                        "Host": "hostile.test",
                        "X-Forwarded-Host": "radar.test",
                        "Forwarded": "host=radar.test;for=127.0.0.1;proto=https",
                    },
                )
            ).status_code == 400
            assert (await client.post("/api/v1/repositories")).status_code == 405
            # Revoke after startup: generic errors, no traceback/SQL/password exposure; recover.
            async with owner.begin() as conn:
                await conn.execute(text("REVOKE SELECT ON repositories FROM radar_public"))
            try:
                assert (await client.get("/api/v1/ready")).status_code == 503
                response = await client.get("/api/v1/repositories")
                assert response.status_code == 500 and "internal_error" in response.text
                assert "password" not in response.text and "SELECT" not in response.text
            finally:
                async with owner.begin() as conn:
                    await conn.execute(text("GRANT SELECT ON repositories TO radar_public"))
            async with owner.begin() as conn:
                await conn.execute(
                    text("""SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                    WHERE usename='radar_public' AND pid <> pg_backend_pid()""")
                )
            assert (await client.get("/api/v1/ready")).status_code == 200
            async with owner.connect() as conn:
                connections = await conn.scalar(
                    text("SELECT count(*) FROM pg_stat_activity WHERE usename='radar_public'")
                )
                assert connections <= 2
                assert await fingerprint(conn) == before
            status = Path(f"/proc/{process.pid}/status").read_text()
            peak_match = re.search(r"VmHWM:\s+(\d+)", status)
            assert peak_match
            peak_kib = int(peak_match[1])
            assert peak_kib < 512 * 1024
            assert not Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().strip()
            print(
                json.dumps(
                    {
                        "web_peak_rss_kib": peak_kib,
                        "reader_connections": connections,
                        "address_space_limit_mib": 512,
                        "fixture_repositories": 3,
                    }
                )
            )
            if os.environ.get("RADAR_TEST_RESOURCE_LIMITS") == "1":
                cgroup = Path(f"/proc/{process.pid}/cgroup").read_text().strip().split("::")[1]
                control = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
                quota, period = (control / "cpu.max").read_text().split()
                assert int(quota) / int(period) <= 0.1
                assert int((control / "memory.max").read_text()) <= 512 * 1024 * 1024
                print("VERIFIED cgroup: CPU <= 0.1, memory <= 512 MiB")
    finally:
        process.terminate()
        process.wait(timeout=25)
        await owner.dispose()
    logged = log.read_text()
    assert "PRIVATE_DEPLOYMENT_SENTINEL" not in logged
    assert make_url(cluster["reader"]).password not in logged
    assert "SELECT" not in logged and "asyncpg" not in logged

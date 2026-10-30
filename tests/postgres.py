"""Fresh PostgreSQL 16 TLS/SCRAM fixtures; never connect to an operator database."""

from __future__ import annotations

import secrets
import shutil
import socket
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from github_radar.recovery import RESTORE_TARGET, pg_environment


class PrivateCluster(dict[str, Any]):
    def __repr__(self):
        return "<disposable PostgreSQL fixture; credentials redacted>"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Disposable PostgreSQL command failed: {Path(args[0]).name}")
    return result.stdout


@contextmanager
def temporary_postgres(parent=None):
    for binary in ("initdb", "pg_ctl", "psql", "pg_dump", "pg_restore", "openssl"):
        assert shutil.which(binary), f"Deployment verification requires {binary}"
    directory = Path(tempfile.mkdtemp(prefix="radar-postgres-", dir=parent))
    directory.chmod(0o700)
    (directory / "fixture-owned").write_text(str(directory.resolve()))
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
            f"unix_socket_directories='{directory}'\nssl=on\ntimezone='Europe/Moscow'\n"
        )
    run(["pg_ctl", "-D", str(data), "-l", str(directory / "postgres.log"), "-w", "start"])
    base = f"postgresql+psycopg://radar_owner:{password}@localhost:{port}"
    tls = f"?sslmode=verify-full&channel_binding=require&sslrootcert={ca}"
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
        yield PrivateCluster(
            owner=base + "/github_radar_test" + tls,
            restore=base + "/" + RESTORE_TARGET + tls,
            ca=str(ca),
            directory=directory,
        )
    finally:
        assert (directory / "fixture-owned").read_text() == str(directory.resolve())
        run(["pg_ctl", "-D", str(data), "-m", "fast", "-w", "stop"])

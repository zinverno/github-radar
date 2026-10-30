"""Fail-closed provenance for the reviewed system-linked Linux PostgreSQL runtime.

Package baselines are vendor backports, not upstream-version guesses. This check
verifies loaded objects against the installed dpkg database; signed APT acquisition
and repository origin must also be recorded by the build/operator. It is not an
attestation against a compromised root account or a general OS vulnerability scan.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib
import importlib.util
import json
import os
import platform
import re
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import psycopg
import sqlalchemy

# Reviewed vendor security records: docs/PHASE8C_B_MIGRATION_VERIFICATION.md.
BASELINES = {
    ("debian", "12"): ("libssl3", "3.0.22-1~deb12u2"),
    ("ubuntu", "24.04"): ("libssl3t64", "3.0.13-0ubuntu3.16"),
}


def _command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
    if result.returncode:
        raise ValueError("Native package provenance could not be verified")
    return result.stdout.strip()


def require_implementation() -> None:
    if (
        psycopg.__version__ != "3.3.6"
        or psycopg.pq.__impl__ != "python"
        or psycopg.pq.version() != 180006
        or sqlalchemy.__version__ != "2.0.54"
    ):
        raise ValueError("Unreviewed PostgreSQL driver; install the frozen dependencies")
    if importlib.util.find_spec("psycopg_binary") is not None:
        raise ValueError("Bundled psycopg libraries are forbidden")
    if any(
        os.environ.get(key)
        for key in (
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "OPENSSL_CONF",
            "OPENSSL_MODULES",
            "OPENSSL_ENGINES",
        )
    ):
        raise ValueError("Native library overrides are forbidden")


class _DlInfo(ctypes.Structure):
    _fields_ = [
        ("path", ctypes.c_char_p),
        ("base", ctypes.c_void_p),
        ("symbol", ctypes.c_char_p),
        ("address", ctypes.c_void_p),
    ]


def _path_of(function: Any) -> Path:
    dladdr = ctypes.CDLL(None).dladdr
    dladdr.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DlInfo)]
    dladdr.restype = ctypes.c_int
    info = _DlInfo()
    if not dladdr(ctypes.cast(function, ctypes.c_void_p), ctypes.byref(info)) or not info.path:
        raise ValueError("Loaded native symbol has no verifiable library path")
    path = Path(os.fsdecode(info.path)).resolve(strict=True)
    if not path.is_relative_to("/usr/lib"):
        raise ValueError("Native library is not installed in the system library directory")
    return path


def _module_handle(name: str) -> ctypes.CDLL:
    module = importlib.import_module(name)
    if not (filename := getattr(module, "__file__", None)):
        raise ValueError("Bundled or static Python OpenSSL cannot be certified")
    return ctypes.CDLL(filename)


def _package(name: str) -> dict[str, str]:
    record = _command(
        [
            "dpkg-query",
            "--show",
            "--showformat=${binary:Package}\t${Version}\t${Status}",
            name,
        ]
    ).split("\t")
    if len(record) != 3 or record[2] != "install ok installed":
        raise ValueError("Native package is not fully installed")
    package, version, _ = record
    if not re.fullmatch(r"[a-z0-9+.-]+(?::[a-z0-9]+)?", package):
        raise ValueError("Native package identity is invalid")
    return {"package": package, "version": version}


def _file_record(path: Path, package: dict[str, str]) -> dict[str, str]:
    manifest = Path("/var/lib/dpkg/info", package["package"] + ".md5sums")
    expected = None
    for line in manifest.read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if (Path("/") / name).resolve() == path:
            expected = digest
            break
    # dpkg's md5 manifest is a file-integrity check, not the APT signature trust root.
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "md5").hexdigest()
    if expected is None or actual != expected:
        raise ValueError("Loaded native library differs from its installed package")
    with path.open("rb") as stream:
        digest256 = hashlib.file_digest(stream, "sha256").hexdigest()
    return dict(package, path=str(path), sha256=digest256, package_file_verified="true")


def _require_vendor_fix(os_id: str, release: str, package: dict[str, str]) -> None:
    expected, minimum = BASELINES[(os_id, release)]
    version = package["version"]
    if package["package"].split(":")[0] != expected or not re.fullmatch(
        r"3\.0\.\d+-\d+~deb12u\d+" if os_id == "debian" else r"3\.0\.13-0ubuntu3\.\d+",
        version,
    ):
        raise ValueError("OpenSSL package does not match the reviewed vendor release")
    _command(["dpkg", "--compare-versions", version, "ge", minimum])


@cache
def native_report() -> dict[str, Any]:
    require_implementation()
    os_info = platform.freedesktop_os_release()
    identity = (os_info.get("ID", ""), os_info.get("VERSION_ID", ""))
    if identity not in BASELINES:
        raise ValueError("Native provenance requires reviewed Debian 12 or Ubuntu 24.04 packages")
    ssl_package = _package(BASELINES[identity][0])
    _require_vendor_fix(*identity, ssl_package)
    pq_package = _package("libpq5")
    # This is the actual ctypes handle used by the pinned psycopg Python implementation.
    from psycopg.pq import _pq_ctypes

    pq_handle = getattr(_pq_ctypes, "pq")
    pq_path = _path_of(pq_handle.PQlibVersion)
    ssl_path = _path_of(pq_handle.SSL_new)
    crypto_path = _path_of(pq_handle.OpenSSL_version)
    for handle in (_module_handle("_ssl"), _module_handle("_hashlib")):
        if _path_of(handle.OpenSSL_version) != crypto_path:
            raise ValueError("Python and libpq load different OpenSSL libraries")
    if _path_of(_module_handle("_ssl").SSL_new) != ssl_path:
        raise ValueError("Python and libpq load different TLS libraries")
    pq_handle.OpenSSL_version.argtypes = [ctypes.c_int]
    pq_handle.OpenSSL_version.restype = ctypes.c_char_p
    openssl = pq_handle.OpenSSL_version(0).decode("ascii")
    if not openssl.startswith("OpenSSL " + ssl_package["version"].split("-")[0] + " "):
        raise ValueError("Loaded OpenSSL version does not match the vendor package")
    expected_paths = {pq_path, ssl_path, crypto_path}
    mapped_paths = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and re.search(r"/lib(?:pq|ssl|crypto)[-.]", fields[5]):
            if fields[5].endswith(" (deleted)"):
                raise ValueError("Native libraries changed; restart on the reviewed environment")
            mapped_paths.add(Path(fields[5]).resolve(strict=True))
    if mapped_paths != expected_paths:
        raise ValueError("Unexpected or bundled PostgreSQL/OpenSSL library is loaded")
    return {
        "status": "VERIFIED",
        "scope": "CVE-2026-35189 and loaded library provenance",
        "os": identity[0],
        "os_release": identity[1],
        "psycopg": psycopg.__version__,
        "implementation": psycopg.pq.__impl__,
        "libpq": psycopg.pq.version(),
        "openssl": openssl,
        "vendor_fixed_baseline": BASELINES[identity][1],
        "libraries": [
            _file_record(pq_path, pq_package),
            _file_record(ssl_path, ssl_package),
            _file_record(crypto_path, ssl_package),
        ],
        "python_ssl_shared_with_libpq": True,
        "extra_loaded_openssl": False,
    }


def main() -> None:
    try:
        require_implementation()
        report = native_report()
    except Exception as exc:
        # Only our own ValueError messages are safe; never render subprocess/env data.
        reason = str(exc) if type(exc) is ValueError else "Native dependency provenance failed"
        print(json.dumps({"status": "BLOCKED", "reason": reason}))
        raise SystemExit(1) from None
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

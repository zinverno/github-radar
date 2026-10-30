"""Vendor backports, actual loader identity and package integrity fail closed."""

import hashlib
from pathlib import Path

import pytest

from github_radar import native


@pytest.fixture(autouse=True)
def clear_native_cache():
    native.native_report.cache_clear()
    yield
    native.native_report.cache_clear()


@pytest.mark.parametrize(
    ("os_id", "release", "package", "version", "accepted"),
    [
        ("debian", "12", "libssl3", "3.0.20-1~deb12u2", False),
        ("debian", "12", "libssl3", "3.0.22-1~deb12u1", False),
        ("debian", "12", "libssl3", "3.0.22-1~deb12u2", True),
        ("debian", "12", "libssl3", "3.0.22-1~deb12u3", True),
        ("debian", "12", "libssl3", "3.5.8", False),
        ("debian", "12", "libssl3", "3.5.9-unreviewed", False),
        ("debian", "12", "libssl3t64", "3.0.22-1~deb12u2", False),
        ("ubuntu", "24.04", "libssl3t64", "3.0.13-0ubuntu3.15", False),
        ("ubuntu", "24.04", "libssl3t64", "3.0.13-0ubuntu3.16", True),
        ("ubuntu", "24.04", "libssl3t64", "3.0.13-0ubuntu3.17", True),
        ("ubuntu", "24.04", "libssl3", "3.0.13-0ubuntu3.16", False),
    ],
)
def test_only_reviewed_vendor_fixed_packages_pass(os_id, release, package, version, accepted):
    record = {"package": package + ":amd64", "version": version}
    if accepted:
        native._require_vendor_fix(os_id, release, record)
    else:
        with pytest.raises(ValueError):
            native._require_vendor_fix(os_id, release, record)


def test_report_identifies_actual_shared_libraries():
    report = native.native_report()
    assert report["implementation"] == "python" and report["libpq"] == 180006
    assert report["python_ssl_shared_with_libpq"] and not report["extra_loaded_openssl"]
    assert len(report["libraries"]) == 3
    for library in report["libraries"]:
        path = Path(library["path"])
        assert path.is_file() and library["package_file_verified"] == "true"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == library["sha256"]


@pytest.mark.parametrize(
    "name",
    [
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "OPENSSL_CONF",
        "OPENSSL_MODULES",
        "OPENSSL_ENGINES",
    ],
)
def test_native_override_values_are_not_logged(name, monkeypatch):
    monkeypatch.setenv(name, "PRIVATE_OVERRIDE_VALUE")
    with pytest.raises(ValueError, match="overrides are forbidden") as error:
        native.require_implementation()
    assert "PRIVATE_OVERRIDE_VALUE" not in str(error.value)


def test_bundled_distribution_refused_even_when_not_selected(monkeypatch):
    monkeypatch.setattr(
        "github_radar.native.importlib.util.find_spec", lambda name: object()
    )
    with pytest.raises(ValueError, match="Bundled psycopg libraries are forbidden"):
        native.require_implementation()


def test_unreviewed_os_cannot_inherit_debian_acceptance(monkeypatch):
    monkeypatch.setattr(
        "github_radar.native.platform.freedesktop_os_release",
        lambda: {"ID": "unknown", "VERSION_ID": "12"},
    )
    with pytest.raises(ValueError, match="requires reviewed"):
        native.native_report()


def test_static_python_openssl_is_not_certified(monkeypatch):
    class BuiltinModule:
        pass

    monkeypatch.setattr(
        "github_radar.native.importlib.import_module", lambda name: BuiltinModule()
    )
    with pytest.raises(ValueError, match="static Python OpenSSL"):
        native._module_handle("_ssl")


def test_package_hash_mismatch_is_rejected(tmp_path, monkeypatch):
    library = tmp_path / "libssl.so.3"
    library.write_bytes(b"modified library")
    original = Path.read_text

    def manifest(path, *args, **kwargs):
        if path.name == "fixture.md5sums":
            return "0" * 32 + "  " + str(library).lstrip("/") + "\n"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", manifest)
    with pytest.raises(ValueError, match="differs from its installed package"):
        native._file_record(library, {"package": "fixture", "version": "1"})


def test_extra_loaded_openssl_object_is_rejected(tmp_path, monkeypatch):
    extra = tmp_path / "libcrypto-unreviewed.so.3"
    extra.write_bytes(b"fixture")
    original = Path.read_text

    def maps(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if str(path) == "/proc/self/maps":
            result += f"\n00000000-00000001 r-xp 0 00:00 0 {extra}\n"
        return result

    monkeypatch.setattr(Path, "read_text", maps)
    with pytest.raises(ValueError, match="Unexpected or bundled"):
        native.native_report()

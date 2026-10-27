"""A PATH Julia WG cannot hash, or did not install, never blocks WG's own Julia.

juliaup and the Microsoft Store Julia put an App Execution Alias at
``%LOCALAPPDATA%\\Microsoft\\WindowsApps\\julia.exe``. Opening it as a file
raises OSError 22, which used to leave BEAT CPU "identity-unavailable" for good.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from server.engines.registry import _beat_engine_info
from server.solver.beat_runtime import discovery, installer, paths, readiness, state

# What shutil.which finds here: Windows matches julia.EXE through PATHEXT.
JULIA = "julia.EXE" if os.name == "nt" else "julia"



@pytest.fixture
def store_alias(tmp_path, monkeypatch):
    """Mock the alias: an executable entry whose open() raises OSError 22."""
    alias = executable(tmp_path / "WindowsApps" / JULIA)
    original = Path.open

    def guarded(self, *args, **kwargs):
        if "WindowsApps" in Path(self).parts:
            raise OSError(errno.EINVAL, "Invalid argument", str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    return alias


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path / "wg"))
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(tmp_path / "hbb"))
    monkeypatch.delenv(discovery.JULIA_ENV_VAR, raising=False)


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake Julia")
    path.chmod(0o755)
    return path


def search_path(*entries: Path) -> dict[str, str]:
    return {**os.environ, "PATH": os.pathsep.join(str(entry.parent) for entry in entries)}


def pinned_install(root: Path) -> Path:
    """An intact WG-owned portable Julia for this host, without a julia.json record."""
    spec = installer.julia_download()
    tree = root / "julia" / spec.directory
    binary = executable(tree / "bin" / ("julia.exe" if spec.windows else "julia"))
    (tree / ".wg-julia.json").write_text(json.dumps(
        {"provider": paths.PROVIDER_ID, "version": installer.JULIA_VERSION, "platform": spec.platform}))
    return binary


def test_store_alias_on_path_is_skipped(store_alias, tmp_path):
    other = executable(tmp_path / "other" / JULIA)
    assert discovery.shutil.which("julia", path=str(store_alias.parent)) == str(store_alias)
    assert discovery.discover_julia(environ=search_path(store_alias)) is None
    assert discovery.discover_julia(environ=search_path(store_alias, other)) == str(other)


def test_unhashable_path_julia_falls_through(tmp_path, monkeypatch):
    locked = executable(tmp_path / "locked" / JULIA)
    monkeypatch.setattr(discovery, "executable_identity", lambda path: (_ for _ in ()).throw(
        OSError(errno.EACCES, "Permission denied", str(path))))
    assert discovery.discover_julia(environ=search_path(locked)) is None


def test_reparse_point_on_path_is_skipped(tmp_path, monkeypatch):
    alias = executable(tmp_path / "alias" / JULIA)
    monkeypatch.setattr(paths, "reparse_point", lambda path: path == alias)
    assert discovery.discover_julia(environ=search_path(alias)) is None


def test_wg_pinned_julia_outranks_path(tmp_path):
    other = executable(tmp_path / "other" / JULIA)
    pinned = pinned_install(paths.runtime_dir())
    assert discovery.discover_julia(environ=search_path(other)) == str(pinned)


def test_path_alias_leaves_cpu_preparable(cpu_provisioning, store_alias, monkeypatch):
    monkeypatch.delenv("WG2_SKIP_BEAT_CPU_PROVISION", raising=False)
    root, _, _, _, _ = cpu_provisioning
    verdict = readiness.backend_readiness("cpu", root, environ=search_path(store_alias), julia_threads=3)
    assert verdict.state == "no-julia", verdict
    info = _beat_engine_info("cpu", readiness.backend_status("cpu", root, environ=search_path(store_alias), julia_threads=3))
    assert info.runtime_state == "unprepared" and info.runtime_preparable


def test_identity_failure_on_discovered_julia_is_not_terminal(cpu_provisioning, store_alias, monkeypatch):
    # Discovery saw a readable file; it became unreadable before the proof hashed it.
    root, _, _, _, _ = cpu_provisioning
    monkeypatch.setattr(discovery, "_path_julia", lambda env: str(store_alias))
    verdict = readiness.backend_readiness("cpu", root, environ=search_path(store_alias), julia_threads=3)
    assert verdict.state == "no-julia", verdict


@pytest.fixture
def download(monkeypatch):
    """Replace the 280 MB download and unpack with a WG-owned pinned tree."""
    events = []
    monkeypatch.setattr(installer, "check_disk_space", lambda *a, **k: None)
    monkeypatch.setattr(installer, "download_archive", lambda spec, archive, **k: events.append("download"))
    monkeypatch.setattr(installer, "extract_julia", lambda archive, root, spec, **k: pinned_install(root))
    return events


@pytest.mark.parametrize("on_path", ["alias", "readable"])
def test_provisioning_installs_pinned_julia_not_path_julia(store_alias, tmp_path, download, on_path):
    found = store_alias if on_path == "alias" else executable(tmp_path / "other" / JULIA)
    messages = []
    root = paths.runtime_dir()
    julia = installer.ensure_julia(root, environ=search_path(found), status_cb=messages.append)
    assert download == ["download"]
    assert julia == str(installer.pinned_julia(root))
    assert not any(message.startswith("Using existing Julia") for message in messages)
    assert state.read_julia(root)["origin"] == "managed"


def test_unproved_path_record_is_replaced_by_pinned_julia(tmp_path, download):
    adopted = executable(tmp_path / "other" / JULIA)
    root = paths.runtime_dir()
    discovery.write_julia_record(root, adopted, origin="external", version=None, selection="path")
    assert discovery.discover_julia(environ={"PATH": ""}) is None
    assert installer.ensure_julia(root, environ={"PATH": ""}) == str(installer.pinned_julia(root))
    assert download == ["download"]


def test_outdated_wg_julia_upgrades_to_pinned_not_path(tmp_path, download):
    root = paths.runtime_dir()
    old = executable(root / "julia" / "1.11.0" / "bin" / "julia")
    discovery.write_julia_record(root, old, origin="managed", version="1.11.0")
    other = executable(tmp_path / "other" / JULIA)
    assert installer.ensure_julia(root, environ=search_path(other)) == str(installer.pinned_julia(root))
    assert download == ["download"]

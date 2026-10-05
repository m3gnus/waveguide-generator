from __future__ import annotations

from pathlib import Path

import pytest

from server.solver.beat_runtime import discovery, installer, paths
from server.tests.beat_runtime.test_installer_extraction import archive


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path / "wg"))
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(tmp_path / "hbb"))
    monkeypatch.delenv(discovery.JULIA_ENV_VAR, raising=False)
    monkeypatch.setattr(discovery.shutil, "which", lambda *a, **k: None)

    def forbidden(*a, **k):
        raise AssertionError("network/disk checks forbidden during intact recovery")

    monkeypatch.setattr(installer.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(installer, "check_disk_space", forbidden)


@pytest.mark.parametrize("invalid_target", [False, True])
@pytest.mark.parametrize("recorded", [False, True])
def test_owned_backup_restores_before_offline_download_and_disk_budget(tmp_path, invalid_target, recorded):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    binary = installer.extract_julia(archive(tmp_path), root, spec)
    if recorded:
        discovery.write_julia_record(root, binary, origin="managed", version=installer.JULIA_VERSION)
    target = binary.parent.parent
    backup = target.with_name(f".{spec.directory}.previous")
    target.rename(backup)
    if invalid_target:
        target.mkdir()
        (target / ".wg-julia.json").write_bytes((backup / ".wg-julia.json").read_bytes())
        (target / "partial").write_bytes(b"incomplete")
    recovered = installer.ensure_julia(root, system="Linux", machine="x86_64")
    assert recovered == str(binary) and binary.read_bytes() == b"new Julia"
    assert not backup.exists()
    assert not (target / "partial").exists()
    assert discovery.discover_julia(root=root) == str(binary)


@pytest.mark.parametrize("unowned", ["target", "backup"])
def test_backup_recovery_never_deletes_unowned_trees(tmp_path, unowned):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    binary = installer.extract_julia(archive(tmp_path), root, spec)
    target = binary.parent.parent
    backup = target.with_name(f".{spec.directory}.previous")
    target.rename(backup)
    protected = target if unowned == "target" else backup
    protected.mkdir(exist_ok=True)
    (protected / ".wg-julia.json").unlink(missing_ok=True)
    keep = protected / "keep"
    keep.write_bytes(b"unowned")
    with pytest.raises(RuntimeError, match="unowned|Unowned"):
        installer.ensure_julia(root, system="Linux", machine="x86_64")
    assert keep.read_bytes() == b"unowned" and backup.exists()


@pytest.mark.parametrize("recorded", [False, True])
def test_published_install_survives_backup_cleanup_permission_error_and_retries(tmp_path, monkeypatch, recorded):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    source = archive(tmp_path)
    binary = installer.extract_julia(source, root, spec)
    if recorded:
        discovery.write_julia_record(root, binary, origin="managed", version=installer.JULIA_VERSION)
    backup = binary.parent.parent.with_name(f".{spec.directory}.previous")
    original = installer.shutil.rmtree
    calls = []

    def denied(path, **kwargs):
        if Path(path) == backup / "bin":
            calls.append(path)
            raise PermissionError("DLL held by reader")
        original(path, **kwargs)

    monkeypatch.setattr(installer.shutil, "rmtree", denied)
    assert installer.extract_julia(source, root, spec) == binary
    assert backup.exists() and installer._owned(backup, spec) and binary.read_bytes() == b"new Julia"
    assert installer.ensure_julia(root, system="Linux", machine="x86_64") == str(binary)
    assert len(calls) >= 2
    monkeypatch.setattr(installer.shutil, "rmtree", original)
    assert installer.ensure_julia(root, system="Linux", machine="x86_64") == str(binary)
    assert not backup.exists()


def test_backup_directory_sharing_violation_retains_ownership_marker(tmp_path, monkeypatch):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    source = archive(tmp_path)
    binary = installer.extract_julia(source, root, spec)
    backup = binary.parent.parent.with_name(f".{spec.directory}.previous")
    original = Path.rmdir

    def denied(path):
        if path == backup:
            raise PermissionError("directory reader")
        original(path)

    monkeypatch.setattr(Path, "rmdir", denied)
    assert installer.extract_julia(source, root, spec) == binary
    assert installer._owned(backup, spec)
    assert installer.ensure_julia(root, system="Linux", machine="x86_64") == str(binary)
    monkeypatch.setattr(Path, "rmdir", original)
    assert installer.ensure_julia(root, system="Linux", machine="x86_64") == str(binary)
    assert not backup.exists()


def test_interrupted_staging_cleanup_retains_ownership_until_retry(tmp_path, monkeypatch):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    staging, _, _ = installer._layout(root, spec)
    original = installer.shutil.rmtree

    def denied(path, **kwargs):
        if Path(path) == staging / "julia-1.12.7":
            raise PermissionError("open staging file")
        original(path, **kwargs)

    monkeypatch.setattr(installer.shutil, "rmtree", denied)
    source = archive(tmp_path, members={"julia-1.12.7/partial": b"no executable"})
    with pytest.raises(RuntimeError, match="no executable"):
        installer.extract_julia(source, root, spec)
    assert installer._staging_owned(staging)
    monkeypatch.setattr(installer.shutil, "rmtree", original)
    assert installer.extract_julia(archive(tmp_path), root, spec).read_bytes() == b"new Julia"
    assert not staging.exists()

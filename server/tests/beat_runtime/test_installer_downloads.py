from __future__ import annotations

from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest

from server.solver.beat_runtime import discovery, installer, paths


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path / "wg"))
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(tmp_path / "hbb"))
    monkeypatch.delenv(discovery.JULIA_ENV_VAR, raising=False)
    monkeypatch.setattr(discovery.shutil, "which", lambda *args, **kwargs: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("real network forbidden")
    monkeypatch.setattr(installer.urllib.request, "urlopen", forbidden)


@pytest.mark.parametrize(("system", "machine", "suffix", "segment"), [
    ("Darwin", "arm64", "macaarch64.tar.gz", "mac/aarch64"),
    ("Darwin", "x86_64", "mac64.tar.gz", "mac/x64"),
    ("Windows", "AMD64", "win64.zip", "winnt/x64"),
    ("Linux", "x86_64", "linux-x86_64.tar.gz", "linux/x64"),
    ("Linux", "aarch64", "linux-aarch64.tar.gz", "linux/aarch64"),
])
def test_release_matrix(system, machine, suffix, segment):
    spec = installer.julia_download(system, machine)
    assert spec.filename == f"julia-1.12.7-{suffix}"
    assert spec.url == f"https://julialang-s3.julialang.org/bin/{segment}/1.12/{spec.filename}"
    assert len(spec.sha256) == 64
    if system == "Windows":
        assert spec.sha256 == "ff5c7eb354c2fcb48401114a5fbcfe8e60181f95d9af42b266f975265a5bad47"


def test_aliases_and_unsupported_platform():
    assert installer.julia_download("Windows", "x86_64") == installer.julia_download("Windows", "AMD64")
    assert installer.julia_download("Linux", "arm64") == installer.julia_download("Linux", "aarch64")
    with pytest.raises(RuntimeError, match="No portable Julia"):
        installer.julia_download("Linux", "riscv64")


def test_download_stages_verifies_and_publishes(tmp_path):
    content = b"verified archive"
    spec = replace(installer.julia_download("Linux", "x86_64"), sha256=hashlib.sha256(content).hexdigest())
    destination = tmp_path / "downloads" / spec.filename
    seen = []
    def fetch(url, partial):
        seen.append((url, partial))
        assert not destination.exists()
        partial.write_bytes(content)
    installer.download_archive(spec, destination, fetcher=fetch)
    assert seen == [(spec.url, destination.with_name(destination.name + ".part"))]
    assert destination.read_bytes() == content
    assert not seen[0][1].exists()


@pytest.mark.parametrize("failure", ["offline", "partial", "checksum"])
def test_download_failure_never_publishes_or_leaves_part(tmp_path, failure):
    spec = installer.julia_download("Linux", "x86_64")
    destination = tmp_path / spec.filename
    destination.write_bytes(b"previous verified archive")
    partial = destination.with_name(destination.name + ".part")
    partial.write_bytes(b"stale partial")
    def fetch(url, target):
        if failure == "offline":
            raise OSError("offline")
        target.write_bytes(b"truncated archive")
        if failure == "partial":
            raise OSError("interrupted download")
    with pytest.raises((OSError, RuntimeError), match="offline|interrupted|SHA-256 mismatch"):
        installer.download_archive(spec, destination, fetcher=fetch)
    assert destination.read_bytes() == b"previous verified archive"
    assert not partial.exists()


def test_download_requires_checksum_and_refuses_symlink(tmp_path):
    spec = installer.julia_download("Linux", "x86_64")
    with pytest.raises(ValueError, match="pinned SHA-256"):
        installer.download_archive(replace(spec, sha256=""), tmp_path / "archive")
    external = tmp_path / "external"
    external.write_bytes(b"keep")
    destination = tmp_path / "archive"
    destination.with_name("archive.part").symlink_to(external)
    with pytest.raises(RuntimeError, match="Linked"):
        installer.download_archive(spec, destination)
    assert external.read_bytes() == b"keep"


@pytest.mark.parametrize("budget", [installer.CPU_REQUIRED_FREE_BYTES, installer.GPU_REQUIRED_FREE_BYTES])
def test_disk_budget_checked_before_fetch(tmp_path, monkeypatch, budget):
    calls = []
    monkeypatch.setattr(installer.shutil, "disk_usage", lambda path: calls.append(path) or SimpleNamespace(free=budget - 1))
    with pytest.raises(RuntimeError, match="Not enough free disk space"):
        installer.ensure_julia(tmp_path / "missing/runtime", system="Linux", machine="x86_64", required_bytes=budget)
    assert calls
    assert not (tmp_path / "missing/runtime/downloads").exists()


def test_cpu_budget_allows_hosts_below_gpu_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.shutil, "disk_usage", lambda path: SimpleNamespace(free=installer.CPU_REQUIRED_FREE_BYTES))
    installer.check_disk_space(tmp_path / "missing/runtime")
    with pytest.raises(RuntimeError, match="Not enough"):
        installer.check_disk_space(tmp_path, installer.GPU_REQUIRED_FREE_BYTES)
    with pytest.raises(ValueError, match="negative"):
        installer.check_disk_space(tmp_path, -1)


def test_callback_failure_does_not_fail_download(tmp_path):
    content = b"archive"
    spec = replace(installer.julia_download("Linux", "x86_64"), sha256=hashlib.sha256(content).hexdigest())
    def callback(message):
        raise UnicodeEncodeError("ascii", "✓", 0, 1, "console")
    destination = tmp_path / "archive"
    installer.download_archive(spec, destination, fetcher=lambda url, path: path.write_bytes(content), status_cb=callback)
    assert destination.read_bytes() == content

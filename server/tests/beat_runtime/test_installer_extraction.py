from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile

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


def archive(tmp_path: Path, *, windows: bool = False, members: dict[str, bytes] | None = None) -> Path:
    members = members if members is not None else {f"julia-1.12.7/bin/julia{'.exe' if windows else ''}": b"new Julia"}
    path = tmp_path / ("julia.zip" if windows else "julia.tar.gz")
    if windows:
        with zipfile.ZipFile(path, "w") as bundle:
            for name, content in members.items():
                bundle.writestr(name, content)
    else:
        with tarfile.open(path, "w:gz") as bundle:
            for name, content in members.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(content), 0o755
                bundle.addfile(info, io.BytesIO(content))
    return path


def executable(path: Path, content: bytes = b"old Julia") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o755)
    return path


def fake_download(monkeypatch, source: Path, system: str = "Linux") -> installer.JuliaDownload:
    spec = replace(installer.julia_download(system, "x86_64"), sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(installer, "julia_download", lambda *args: spec)
    return spec


@pytest.mark.parametrize("windows", [False, True])
def test_zip_and_tar_publish_version_platform_tree(tmp_path, windows):
    spec = installer.julia_download("Windows" if windows else "Linux", "x86_64")
    root = paths.runtime_dir()
    binary = installer.extract_julia(archive(tmp_path, windows=windows), root, spec)
    assert binary == root / "julia" / spec.directory / "bin" / ("julia.exe" if windows else "julia")
    assert binary.read_bytes() == b"new Julia"
    assert installer._owned(binary.parent.parent, spec)
    assert not list((root / ("dl" if windows else "downloads")).iterdir())


def test_mac_application_bundle_layout(tmp_path):
    spec = installer.julia_download("Darwin", "arm64")
    source = archive(tmp_path, members={"Julia-1.12.app/Contents/Resources/julia/bin/julia": b"mac Julia"})
    binary = installer.extract_julia(source, paths.runtime_dir(), spec)
    assert binary.read_bytes() == b"mac Julia"
    assert binary.parts[-5:] == ("Contents", "Resources", "julia", "bin", "julia")


@pytest.mark.parametrize("members", [
    {"one/bin/julia": b"one", "two/bin/julia": b"two"},
    {"julia/lib/readme": b"no executable"},
    {"julia/bin/julia": b"Julia", "extra.txt": b"unexpected"},
])
def test_invalid_layout_never_publishes(tmp_path, members):
    spec = installer.julia_download("Linux", "x86_64")
    root = paths.runtime_dir()
    with pytest.raises(RuntimeError, match="layout|no executable"):
        installer.extract_julia(archive(tmp_path, members=members), root, spec)
    assert not (root / "julia" / spec.directory).exists()
    assert not list((root / "downloads").iterdir())
    assert not (root / "julia.json").exists()


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("name", ["../outside", "/absolute", "julia/../../outside", "C:/outside", "julia\\..\\outside"])
def test_unsafe_archive_member_is_rejected(tmp_path, windows, name):
    root = paths.runtime_dir()
    spec = installer.julia_download("Windows" if windows else "Linux", "x86_64")
    with pytest.raises(RuntimeError, match="Unsafe archive member"):
        installer.extract_julia(archive(tmp_path, windows=windows, members={name: b"bad"}), root, spec)
    assert not (tmp_path / "outside").exists()
    assert not (root / "julia" / spec.directory).exists()


def test_tar_preserves_internal_library_link_but_refuses_escape(tmp_path):
    source = archive(tmp_path)
    spec = installer.julia_download("Linux", "x86_64")
    with tarfile.open(source, "w:gz") as bundle:
        info = tarfile.TarInfo("julia/bin/julia")
        info.size, info.mode = 5, 0o755
        bundle.addfile(info, io.BytesIO(b"Julia"))
        link = tarfile.TarInfo("julia/lib/libjulia.so")
        link.type, link.linkname = tarfile.SYMTYPE, "../bin/julia"
        bundle.addfile(link)
    root = paths.runtime_dir()
    binary = installer.extract_julia(source, root, spec)
    assert (binary.parent.parent / "lib/libjulia.so").read_bytes() == b"Julia"
    with tarfile.open(source, "w:gz") as bundle:
        link.linkname = str(tmp_path / "external")
        bundle.addfile(link)
    with pytest.raises(tarfile.FilterError):
        installer.extract_julia(source, root, spec)
    assert binary.read_bytes() == b"Julia"


def test_zip_symlink_is_rejected(tmp_path):
    source = tmp_path / "julia.zip"
    with zipfile.ZipFile(source, "w") as bundle:
        link = zipfile.ZipInfo("julia/bin/julia.exe")
        link.create_system, link.external_attr = 3, 0o120777 << 16
        bundle.writestr(link, "/external")
    with pytest.raises(RuntimeError, match="Linked ZIP"):
        installer.extract_julia(source, paths.runtime_dir(), installer.julia_download("Windows", "x86_64"))


def test_interrupted_staging_is_recovered_and_download_removed(tmp_path, monkeypatch):
    source = archive(tmp_path)
    spec = fake_download(monkeypatch, source)
    root = paths.runtime_dir()
    stale = root / "downloads" / f"unpack-{spec.directory}"
    stale.mkdir(parents=True)
    (stale / installer._STAGING_MARKER).write_text(json.dumps({"provider": paths.PROVIDER_ID}))
    (stale / "partial.txt").write_bytes(b"interrupted")
    calls = []
    def fetch(url, partial):
        calls.append(url)
        partial.write_bytes(source.read_bytes())
    binary = Path(installer.ensure_julia(fetcher=fetch))
    assert binary.read_bytes() == b"new Julia"
    assert calls == [spec.url]
    assert not list((root / "downloads").iterdir())
    assert discovery.discover_julia() == str(binary)
    assert not list(root.glob("state*.json"))


@pytest.mark.parametrize("recorded_version", ["1.12.6", "1.12.7"])
def test_upgrade_retains_old_tree_and_current_reuse_survives_failed_backend(tmp_path, monkeypatch, recorded_version):
    source = archive(tmp_path)
    spec = fake_download(monkeypatch, source)
    root = paths.runtime_dir()
    old = executable(root / "julia/1.12.6-linux-x86_64/bin/julia")
    discovery.write_julia_record(root, old, origin="managed", version=recorded_version)
    before = old.read_bytes()
    binary = Path(installer.ensure_julia(fetcher=lambda url, partial: partial.write_bytes(source.read_bytes())))
    assert binary == root / "julia" / spec.directory / "bin/julia"
    assert binary != old and old.read_bytes() == before
    (root / "state-cpu.json").write_text('{"status":"failed"}')
    assert installer.ensure_julia() == str(binary)  # Network remains forbidden.
    assert (root / "state-cpu.json").read_text() == '{"status":"failed"}'


def test_published_tree_without_record_recovers_offline(tmp_path):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    binary = installer.extract_julia(archive(tmp_path), root, spec)
    assert not (root / "julia.json").exists()
    assert installer.ensure_julia(system="Linux", machine="x86_64") == str(binary)
    assert discovery.read_julia_record()["version"] == "1.12.7"


def test_selected_external_is_recorded_without_claiming_version(tmp_path):
    external = executable(tmp_path / "external/bin/julia")
    assert installer.ensure_julia(explicit=str(external)) == str(external)
    assert discovery.read_julia_record()["selection"] == "explicit"
    record = discovery.read_julia_record()
    assert record["origin"] == "external" and record["version"] is None
    assert not list(paths.runtime_dir().glob("state*.json"))


@pytest.mark.parametrize("origin", ["external", "legacy"])
def test_external_and_legacy_trees_are_preserved_on_install(tmp_path, monkeypatch, origin):
    root = paths.runtime_dir()
    source = archive(tmp_path)
    fake_download(monkeypatch, source)
    external = executable(tmp_path / "hbb/julia-1.12.6/bin/julia")
    if origin == "external":
        discovery.write_julia_record(root, external, origin=origin, version="1.12.6")
    else:
        # Temporary discovery records (including legacy hints) are not readiness
        # or executable authority in the canonical state.py schema.
        root.mkdir(parents=True)
        (root / "julia.json").write_text(json.dumps({
            "origin": "legacy", "julia_executable": str(external),
        }))
    assert installer.ensure_julia(fetcher=lambda url, partial: partial.write_bytes(source.read_bytes())) != str(external)
    assert external.read_bytes() == b"old Julia"
    assert list(external.parent.iterdir()) == [external]


def test_explicit_old_managed_and_custom_external_win(tmp_path):
    root = paths.runtime_dir()
    old = executable(root / "julia/1.12.6-linux-x86_64/bin/julia")
    assert installer.ensure_julia(explicit=str(old)) == str(old)
    assert installer.ensure_julia(configured=str(old)) == str(old)
    custom = executable(root / "julia-custom/bin/julia")
    discovery.write_julia_record(root, custom, origin="external", version="1.12.6")
    assert installer.ensure_julia() == str(custom)
    assert custom.read_bytes() == b"old Julia"


def test_unowned_target_and_linked_staging_are_never_deleted(tmp_path):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    unowned = executable(root / "julia" / spec.directory / "bin/julia")
    source = archive(tmp_path)
    with pytest.raises(RuntimeError, match="unowned"):
        installer.extract_julia(source, root, spec)
    assert unowned.read_bytes() == b"old Julia"
    other_root = tmp_path / "other"
    downloads = other_root / "downloads"
    downloads.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep").write_bytes(b"keep")
    (downloads / f"unpack-{spec.directory}").symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match="Linked"):
        installer.extract_julia(source, other_root, spec)
    assert (external / "keep").read_bytes() == b"keep"


def test_failed_promotion_restores_owned_target(tmp_path, monkeypatch):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    source = archive(tmp_path)
    binary = installer.extract_julia(source, root, spec)
    original = installer.os.replace
    def fail(source_path, destination):
        if "unpack-" in str(source_path):
            raise OSError("promotion interrupted")
        original(source_path, destination)
    monkeypatch.setattr(installer.os, "replace", fail)
    with pytest.raises(OSError, match="promotion interrupted"):
        installer.extract_julia(source, root, spec)
    assert binary.read_bytes() == b"new Julia"
    assert not list((root / "downloads").iterdir())
    assert not (root / "julia" / f".{spec.directory}.previous").exists()


def test_interrupted_replacement_backup_is_recovered(tmp_path):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    source = archive(tmp_path)
    binary = installer.extract_julia(source, root, spec)
    target = binary.parent.parent
    backup = target.with_name(f".{spec.directory}.previous")
    target.rename(backup)
    recovered = installer.extract_julia(source, root, spec)
    assert recovered == binary and recovered.read_bytes() == b"new Julia"
    assert not backup.exists()


def test_empty_unmarked_staging_recovers_after_marker_write_failure(tmp_path, monkeypatch):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    source = archive(tmp_path)
    staging, _, _ = installer._layout(root, spec)
    original = Path.write_text

    def fail_marker(path, *args, **kwargs):
        if path == staging / installer._STAGING_MARKER:
            raise OSError("marker write interrupted")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", fail_marker)
        with pytest.raises(OSError, match="marker write interrupted"):
            installer.extract_julia(source, root, spec)
    assert staging.exists() and list(staging.iterdir()) == []
    assert installer.extract_julia(source, root, spec).read_bytes() == b"new Julia"
    assert not staging.exists()


@pytest.mark.parametrize("selection", ["explicit", "configured"])
def test_relative_selected_julia_record_is_absolute_and_keeps_launcher(tmp_path, monkeypatch, selection):
    actual = executable(tmp_path / "actual/bin/julia")
    launcher = tmp_path / "juliaup"
    launcher.symlink_to(actual)
    monkeypatch.chdir(tmp_path)
    root = paths.runtime_dir()
    assert installer.ensure_julia(**{selection: "juliaup"}) == str(launcher)
    assert discovery.read_julia_record(root)["executable"] == str(launcher)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert discovery.recorded_julia(root) == str(launcher)


@pytest.mark.parametrize("selection", ["explicit", "configured"])
def test_ignored_hbb_selected_julia_reports_status(tmp_path, monkeypatch, selection):
    source = archive(tmp_path)
    fake_download(monkeypatch, source)
    legacy = executable(tmp_path / "hbb/bin/julia")
    lines = []
    binary = installer.ensure_julia(**{selection: str(legacy)}, status_cb=lines.append,
                                    fetcher=lambda url, partial: partial.write_bytes(source.read_bytes()))
    assert binary != str(legacy) and legacy.read_bytes() == b"old Julia"
    assert any(f"Ignoring {selection} Julia in an HBB root" in line for line in lines)

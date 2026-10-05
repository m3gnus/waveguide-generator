from __future__ import annotations

import io
import json
from pathlib import Path, PureWindowsPath
import stat
import tarfile
from types import SimpleNamespace

import pytest

from server.solver.beat_runtime import discovery, installer, paths
from server.tests.beat_runtime.test_installer_extraction import archive, executable, fake_download


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path / "wg"))
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(tmp_path / "hbb"))
    monkeypatch.delenv(discovery.JULIA_ENV_VAR, raising=False)
    monkeypatch.setattr(discovery.shutil, "which", lambda *a, **k: None)

    def forbidden(*a, **k):
        raise AssertionError("real network forbidden")

    monkeypatch.setattr(installer.urllib.request, "urlopen", forbidden)


@pytest.mark.parametrize("api", ["ensure", "extract", "download", "private_directory", "unpack"])
@pytest.mark.parametrize("alias", [False, True])
def test_installer_explicit_root_hbb_isolation_before_mutation(tmp_path, api, alias):
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    keep = legacy / "keep"
    keep.write_bytes(b"HBB")
    root = tmp_path / "alias" if alias else legacy
    if alias:
        root.symlink_to(legacy, target_is_directory=True)
    spec = installer.julia_download("Linux", "x86_64")
    source = archive(tmp_path)
    with pytest.raises(paths.RootConflict):
        if api == "ensure":
            installer.ensure_julia(root)
        elif api == "extract":
            installer.extract_julia(source, root, spec)
        elif api == "download":
            installer.download_archive(spec, root / "archive")
        elif api == "unpack":
            installer._unpack(source, root)
        else:
            installer._private_directory(root)
    assert list(legacy.iterdir()) == [keep] and keep.read_bytes() == b"HBB"


def test_windows_max_path_default_twenty_character_username():
    home = Path("C:/Users/" + "u" * 20)
    root = paths.runtime_dir(system="win32", environ={}, home=home)
    spec = installer.julia_download("Windows", "x86_64")
    assert PureWindowsPath(root) == PureWindowsPath(home) / "AppData/Local/WaveguideGenerator/beat"
    staging, target, backup = installer._layout(root, spec)
    # A complete archive name of exactly 155 characters, including its top level.
    member = "julia-1.12.7/" + "a" * (155 - len("julia-1.12.7/"))
    assert len(str(PureWindowsPath(staging) / member)) < 260
    assert len(str(PureWindowsPath(target) / member.split('/', 1)[1])) < 260
    assert len(str(PureWindowsPath(backup) / member.split('/', 1)[1])) < 260
    assert installer.windows_path_length(root, spec) < 260
    installer._check_windows_paths(root, spec)


@pytest.mark.parametrize("api", ["ensure", "extract"])
def test_windows_max_path_refused_before_download_or_mutation(tmp_path, monkeypatch, api):
    root = tmp_path / ("long-" + "x" * 100)
    monkeypatch.setattr(installer, "_WINDOWS", True)
    monkeypatch.setattr(installer, "_windows_long_paths_enabled", lambda: False)
    spec = installer.julia_download("Windows", "x86_64")
    with pytest.raises(RuntimeError, match="MAX_PATH.*shorter WG2_BEAT_RUNTIME_DIR"):
        if api == "ensure":
            installer.ensure_julia(root, system="Windows", machine="x86_64")
        else:
            installer.extract_julia(archive(tmp_path, windows=True), root, spec)
    assert not root.exists()


@pytest.mark.parametrize("mechanism", ["junction", "reparse"])
@pytest.mark.parametrize("component", ["root", "downloads", "staging", "target", "backup", "partial"])
def test_windows_junction_and_reparse_points_are_refused(tmp_path, monkeypatch, mechanism, component):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    staging, target, backup = installer._layout(root, spec)
    destination = root / "downloads" / spec.filename
    linked = {"root": root, "downloads": staging.parent, "staging": staging,
              "target": target, "backup": backup, "partial": destination.with_name(destination.name + ".part")}[component]
    linked.parent.mkdir(parents=True, exist_ok=True)
    if component == "partial":
        linked.write_bytes(b"keep")
    else:
        linked.mkdir(exist_ok=True)
    if mechanism == "junction":
        monkeypatch.setattr(Path, "is_junction", lambda p: p == linked, raising=False)
    else:
        original = Path.lstat
        monkeypatch.setattr(Path, "lstat", lambda p: SimpleNamespace(st_mode=original(p).st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT) if p == linked else original(p))
    with pytest.raises(RuntimeError, match="Linked"):
        if component == "partial":
            installer.download_archive(spec, destination, root=root)
        else:
            installer.extract_julia(archive(tmp_path), root, spec)
    assert linked.exists()


def test_symlinked_ancestors_allow_install_and_resolved_record_reuse(tmp_path):
    real = tmp_path / "real-home"
    real.mkdir()
    alias = tmp_path / "home"
    alias.symlink_to(real, target_is_directory=True)
    root = alias / "wg"
    spec = installer.julia_download("Linux", "x86_64")
    binary = installer.extract_julia(archive(tmp_path), root, spec)
    assert binary.read_bytes() == b"new Julia"
    assert installer.ensure_julia(root, system="Linux", machine="x86_64") == str(binary)
    assert discovery.read_julia_record(root)["executable"] == str(binary)


def test_unowned_staging_tree_is_never_deleted(tmp_path):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    staging, _, _ = installer._layout(root, spec)
    staging.mkdir(parents=True)
    keep = staging / "keep"
    keep.write_bytes(b"unowned")
    with pytest.raises(RuntimeError, match="Unowned staging"):
        installer.extract_julia(archive(tmp_path), root, spec)
    assert list(staging.iterdir()) == [keep] and keep.read_bytes() == b"unowned"


def test_staging_marker_is_written_before_unpack_and_reserved(tmp_path, monkeypatch):
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    original = installer._unpack

    def inspect(source, staging):
        assert installer._staging_owned(staging)
        original(source, staging)

    monkeypatch.setattr(installer, "_unpack", inspect)
    installer.extract_julia(archive(tmp_path), root, spec)
    with pytest.raises(RuntimeError, match="reserved staging marker"):
        installer.extract_julia(archive(tmp_path, members={installer._STAGING_MARKER: b"malicious"}), root, spec)


@pytest.mark.parametrize("linkname", ["../../libjulia.so", "../../escape/elsewhere", "chain"])
def test_tar_link_escape_from_promoted_tree_is_refused(tmp_path, linkname):
    source = tmp_path / "julia.tar.gz"
    with tarfile.open(source, "w:gz") as bundle:
        info = tarfile.TarInfo("julia/bin/julia")
        info.size, info.mode = 5, 0o755
        bundle.addfile(info, io.BytesIO(b"Julia"))
        link = tarfile.TarInfo("julia/lib/libjulia.so")
        link.type, link.linkname = tarfile.SYMTYPE, linkname
        bundle.addfile(link)
        if linkname == "chain":
            chain = tarfile.TarInfo("julia/lib/chain")
            chain.type, chain.linkname = tarfile.SYMTYPE, "../../libjulia.so"
            bundle.addfile(chain)
    root = paths.runtime_dir()
    with pytest.raises(RuntimeError, match="escapes published"):
        installer.extract_julia(source, root, installer.julia_download("Linux", "x86_64"))
    assert not (root / "julia.json").exists()


def test_windows_style_zip_names_are_normalized(tmp_path):
    binary = installer.extract_julia(
        archive(tmp_path, windows=True, members={r"julia-1.12.7\bin\julia.exe": b"Julia"}),
        paths.runtime_dir(), installer.julia_download("Windows", "x86_64"),
    )
    assert binary.read_bytes() == b"Julia"


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("limit", ["size", "members"])
def test_archive_total_size_and_member_count_limits(tmp_path, monkeypatch, windows, limit):
    monkeypatch.setattr(installer, "MAX_EXTRACTED_BYTES" if limit == "size" else "MAX_ARCHIVE_MEMBERS", 1)
    root = paths.runtime_dir()
    spec = installer.julia_download("Windows" if windows else "Linux", "x86_64")
    with pytest.raises(RuntimeError, match="extraction size/member limit"):
        installer.extract_julia(archive(tmp_path, windows=windows, members={"julia/bin/julia": b"Julia", "julia/lib/lib": b"lib"}), root, spec)
    assert not (root / "julia" / spec.directory).exists()


@pytest.mark.parametrize("source", ["explicit", "configured", "path", "record"])
def test_hbb_binary_selection_downloads_into_wg_ownership(tmp_path, monkeypatch, source):
    legacy = executable(tmp_path / "hbb/bin/julia")
    root = paths.runtime_dir()
    downloaded = archive(tmp_path)
    fake_download(monkeypatch, downloaded)
    kwargs = {}
    if source in ("explicit", "configured"):
        kwargs[source] = str(legacy)
    elif source == "record":
        discovery.write_julia_record(root, legacy, origin="external", version=None)
    else:
        monkeypatch.setattr(discovery.shutil, "which", lambda *a, **k: str(legacy))
    before = sorted(p.relative_to(legacy.parent.parent) for p in legacy.parent.parent.rglob("*"))
    result = installer.ensure_julia(fetcher=lambda url, p: p.write_bytes(downloaded.read_bytes()), **kwargs)
    assert Path(result).is_relative_to(root) and Path(result).read_bytes() == b"new Julia"
    assert legacy.read_bytes() == b"old Julia"
    assert sorted(p.relative_to(legacy.parent.parent) for p in legacy.parent.parent.rglob("*")) == before
    assert discovery.read_julia_record()["origin"] == "managed"


def test_stale_legacy_record_does_not_discard_valid_path_julia(tmp_path, monkeypatch):
    external = executable(tmp_path / "external/bin/julia")
    root = paths.runtime_dir()
    root.mkdir(parents=True)
    # Same path in an obsolete record must not poison a fresh PATH selection.
    (root / "julia.json").write_text(json.dumps({"origin": "legacy", "julia_executable": str(external)}))
    monkeypatch.setattr(discovery.shutil, "which", lambda *a, **k: str(external))
    assert installer.ensure_julia() == str(external)
    assert discovery.read_julia_record()["origin"] == "external"


def test_one_off_explicit_installer_selection_does_not_outrank_later_path(tmp_path, monkeypatch):
    one_off = executable(tmp_path / "one-off")
    default = executable(tmp_path / "default")
    assert installer.ensure_julia(explicit=str(one_off)) == str(one_off)
    assert discovery.read_julia_record()["selection"] == "explicit"
    monkeypatch.setattr(discovery.shutil, "which", lambda *a, **k: str(default))
    assert installer.ensure_julia() == str(default)
    assert discovery.read_julia_record()["selection"] == "path"


@pytest.mark.parametrize("target", ["julia/bin/julia", installer._STAGING_MARKER])
def test_tar_hardlink_stays_in_promoted_tree(tmp_path, target):
    source = tmp_path / "julia.tar.gz"
    with tarfile.open(source, "w:gz") as bundle:
        info = tarfile.TarInfo("julia/bin/julia")
        info.size, info.mode = 5, 0o755
        bundle.addfile(info, io.BytesIO(b"Julia"))
        link = tarfile.TarInfo("julia/lib/libjulia.so")
        link.type, link.linkname, link.mode = tarfile.LNKTYPE, target, 0o755
        bundle.addfile(link)
    root = paths.runtime_dir()
    spec = installer.julia_download("Linux", "x86_64")
    if target == installer._STAGING_MARKER:
        with pytest.raises(RuntimeError, match="escapes published"):
            installer.extract_julia(source, root, spec)
    else:
        binary = installer.extract_julia(source, root, spec)
        assert (binary.parent.parent / "lib/libjulia.so").read_bytes() == b"Julia"

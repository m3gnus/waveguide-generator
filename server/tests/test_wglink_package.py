"""WGLink releases retain exact source, license, version, and file provenance."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build_wglink_package.py"
INSTALLER = ROOT / "scripts" / "install_wglink.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_wglink_package_test", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_installer():
    spec = importlib.util.spec_from_file_location("install_wglink_test", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(tmp_path: Path, commit: str) -> Path:
    root = tmp_path / "hornlab-fusion-addin"
    addin = root / "fusion-addins" / "WGLink"
    (addin / "resources" / "insert").mkdir(parents=True)
    (root / "scripts").mkdir()
    (addin / "WGLink.py").write_text("def run(_context): pass\n", encoding="utf-8")
    (addin / "WGLink.manifest").write_text(
        json.dumps({"version": "0.1.1"}), encoding="utf-8"
    )
    (addin / "wglink_core.py").write_text(
        'PACKAGED_RUNTIME_FILE = "wglink_runtime.json"\n', encoding="utf-8"
    )
    (addin / "resources" / "insert" / "16x16.png").write_bytes(b"png")
    (root / "scripts" / "wglink_resample.py").write_text(
        "# exact resampler\n", encoding="utf-8"
    )
    (root / "LICENSE").write_text("AGPL test fixture\n", encoding="utf-8")
    return root


def _spec(commit: str) -> dict[str, object]:
    return {
        "schema": 1,
        "repository": "https://example.invalid/hornlab-fusion-addin.git",
        "commit": commit,
        "license": "AGPL-3.0-or-later",
        "addinVersion": "0.1.1",
    }


def _wg_root(tmp_path: Path, commit: str, *, version: str = "9.8.7") -> Path:
    root = tmp_path / "Waveguide Generator with spaces"
    (root / "integrations" / "wglink").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "integrations" / "wglink" / "source.json").write_text(
        json.dumps(_spec(commit)), encoding="utf-8"
    )
    (root / "shared" / "version.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    return root


def _package(tmp_path: Path, commit: str) -> tuple[Path, Path]:
    builder = _load_builder()
    source = _source(tmp_path, commit)
    root = _wg_root(tmp_path, commit)
    archive = tmp_path / "wglink.zip"
    builder.build_package(
        source,
        archive,
        spec=_spec(commit),
        version="9.8.7",
        observed_commit=commit,
    )
    return root, archive


def test_package_is_deterministic_and_records_every_source_hash(tmp_path: Path):
    builder = _load_builder()
    commit = "a" * 40
    source = _source(tmp_path, commit)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    builder.build_package(
        source, first, spec=_spec(commit), version="9.8.7", observed_commit=commit
    )
    builder.build_package(
        source, second, spec=_spec(commit), version="9.8.7", observed_commit=commit
    )

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        names = set(archive.namelist())
        provenance = json.loads(archive.read("wglink/provenance.json"))
        assert provenance["sourceCommit"] == commit
        assert provenance["sourceLicense"] == "AGPL-3.0-or-later"
        assert provenance["waveguideGeneratorVersion"] == "9.8.7"
        assert "wglink/LICENSE" in names
        assert "wglink/scripts/wglink_resample.py" in names
        assert "wglink/fusion-addins/WGLink/wglink_core.py" in names
        assert names == set(provenance["files"]) | {"wglink/provenance.json"}
        for name, expected in provenance["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected


def test_package_refuses_a_checkout_other_than_the_reviewed_commit(tmp_path: Path):
    builder = _load_builder()
    source = _source(tmp_path, "a" * 40)

    with pytest.raises(builder.PackageError, match="unpinned"):
        builder.build_package(
            source,
            tmp_path / "wrong.zip",
            spec=_spec("a" * 40),
            version="1.0.0",
            observed_commit="b" * 40,
        )


def test_package_refuses_wglink_without_the_managed_runtime_contract(tmp_path: Path):
    builder = _load_builder()
    commit = "a" * 40
    source = _source(tmp_path, commit)
    (source / "fusion-addins" / "WGLink" / "wglink_core.py").write_text(
        "# old checkout\n", encoding="utf-8"
    )

    with pytest.raises(builder.PackageError, match="packaged-runtime contract"):
        builder.build_package(
            source,
            tmp_path / "old.zip",
            spec=_spec(commit),
            version="1.0.0",
            observed_commit=commit,
        )


def test_default_fusion_addins_locations_cover_both_supported_platforms(tmp_path: Path):
    installer = _load_installer()
    home = tmp_path / "home"
    appdata = tmp_path / "Roaming"
    current = (
        home
        / "Library"
        / "Application Support"
        / "Autodesk"
        / "Autodesk Fusion"
        / "API"
        / "AddIns"
    )
    assert installer.default_addins_dir("macos", home=home, environ={}) == current
    legacy = current.parents[2] / "Autodesk Fusion 360" / "API" / "AddIns"
    legacy.mkdir(parents=True)
    assert installer.default_addins_dir("macos", home=home, environ={}) == legacy
    assert installer.default_addins_dir(
        "windows", home=home, environ={"APPDATA": str(appdata)}
    ) == appdata / "Autodesk" / "Autodesk Fusion" / "API" / "AddIns"
    windows_legacy = (
        appdata / "Autodesk" / "Autodesk Fusion 360" / "API" / "AddIns"
    )
    windows_legacy.mkdir(parents=True)
    assert installer.default_addins_dir(
        "windows", home=home, environ={"APPDATA": str(appdata)}
    ) == windows_legacy
    assert installer.default_addins_dir("linux", home=home, environ={}) is None


def test_installed_copy_points_to_wgs_existing_python_and_verified_resampler(
    tmp_path: Path,
):
    installer = _load_installer()
    commit = "a" * 40
    root, archive = _package(tmp_path, commit)
    addins = tmp_path / "Fusion profile" / "API" / "AddIns"

    status, target = installer.install(
        root=root,
        platform="macos",
        addins_dir=addins,
        archive_path=archive,
    )

    assert status == "installed"
    assert target == addins.resolve() / "WGLink"
    runtime = json.loads((target / installer.RUNTIME_FILE).read_text(encoding="utf-8"))
    marker = json.loads((target / installer.INSTALL_MARKER).read_text(encoding="utf-8"))
    assert Path(runtime["python"]) == (root / ".venv" / "bin" / "python").resolve()
    assert (Path(runtime["root"]) / "scripts" / "wglink_resample.py").is_file()
    assert marker == {
        "schema": 1,
        "managedBy": "waveguide-generator",
        "waveguideGeneratorRoot": str(root.resolve()),
        "waveguideGeneratorVersion": "9.8.7",
        "sourceCommit": commit,
        "addinVersion": "0.1.1",
    }
    assert (Path(runtime["root"]) / "LICENSE").read_text(encoding="utf-8") == (
        "AGPL test fixture\n"
    )

    resampler = Path(runtime["root"]) / "scripts" / "wglink_resample.py"
    resampler.write_text("# tampered after install\n", encoding="utf-8")

    second_status, second_target = installer.install(
        root=root,
        platform="macos",
        addins_dir=addins,
        archive_path=archive,
    )
    assert (second_status, second_target) == ("installed", target)
    assert resampler.read_text(encoding="utf-8") == "# exact resampler\n"


def test_platform_install_preserves_a_developer_managed_copy(tmp_path: Path):
    installer = _load_installer()
    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    target = addins / "WGLink"
    target.mkdir(parents=True)
    developer_file = target / "developer.py"
    developer_file.write_text("keep me\n", encoding="utf-8")

    status, observed = installer.install(
        root=root, platform="macos", addins_dir=addins
    )

    assert (status, observed) == ("preserved-external", target.resolve())
    assert developer_file.read_text(encoding="utf-8") == "keep me\n"
    assert not (root / "integrations" / "wglink" / "runtime").exists()


def test_tampered_package_is_refused_before_an_existing_install_changes(tmp_path: Path):
    installer = _load_installer()
    commit = "a" * 40
    root, archive = _package(tmp_path, commit)
    addins = tmp_path / "AddIns"
    installer.install(
        root=root, platform="macos", addins_dir=addins, archive_path=archive
    )
    target = addins.resolve() / "WGLink"
    before = (target / "WGLink.py").read_bytes()
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(tampered, "w") as changed:
        for info in original.infolist():
            data = original.read(info)
            if info.filename.endswith("/WGLink.py"):
                data += b"# tampered\n"
            changed.writestr(info, data)

    with pytest.raises(installer.InstallError, match="hash mismatch"):
        installer.install(
            root=root,
            platform="macos",
            addins_dir=addins,
            archive_path=tampered,
        )

    assert (target / "WGLink.py").read_bytes() == before


def test_uninstall_removes_only_the_copy_managed_by_this_wg_root(tmp_path: Path):
    installer = _load_installer()
    commit = "a" * 40
    root, archive = _package(tmp_path, commit)
    addins = tmp_path / "AddIns"
    installer.install(
        root=root, platform="macos", addins_dir=addins, archive_path=archive
    )

    status, target = installer.uninstall(
        root=root, platform="macos", addins_dir=addins
    )

    assert status == "removed"
    assert not target.exists()
    assert not (root / "integrations" / "wglink" / "runtime").exists()


def test_fetch_uses_a_disposable_checkout_of_the_exact_commit(tmp_path: Path):
    installer = _load_installer()
    source = _source(tmp_path, "unused")
    subprocess_commands = (
        ["git", "init", "--quiet"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "WGLink package test"],
        ["git", "add", "."],
        ["git", "commit", "--quiet", "-m", "fixture"],
    )
    for command in subprocess_commands:
        assert subprocess.run(command, cwd=source).returncode == 0
    commit = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
    )
    root = _wg_root(tmp_path / "wg", commit)
    source_spec = _spec(commit)
    source_spec["repository"] = str(source)
    (root / "integrations" / "wglink" / "source.json").write_text(
        json.dumps(source_spec), encoding="utf-8"
    )

    package = installer._fetch_package(root)

    provenance, _payloads = installer.verify_package(package, root=root)
    assert provenance["sourceCommit"] == commit
    assert package.is_file()

"""The candidate signer receives a complete, unchanged release inventory."""

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from scripts import candidate_manifest
from scripts.release_manifest import _load
from shared import release_assets as names


VERSION = "0.3.6"
RUNTIME = "0123456789ab"
SOURCE_SHA = "a" * 40
ASSETS = (
    ("spa", names.spa_archive_name(VERSION)),
    ("macos", names.app_layer_name(VERSION)),
    ("macos", names.app_manifest_name(VERSION)),
    ("macos", names.user_download_name(names.MACOS_PLATFORM, VERSION)),
    ("windows", names.user_download_name(names.WINDOWS_PLATFORM, VERSION)),
    ("windows", names.installer_name(names.WINDOWS_PLATFORM, VERSION)),
    ("linux", names.user_download_name(names.LINUX_PLATFORM, VERSION)),
    *((directory, names.runtime_layer_name(platform, RUNTIME)) for directory, platform in (
        ("macos", names.MACOS_PLATFORM), ("windows", names.WINDOWS_PLATFORM), ("linux", names.LINUX_PLATFORM))),
)


def _write(path, data):
    path.write_bytes(data)
    path.with_name(names.checksum_name(path.name)).write_text(
        f"{hashlib.sha256(data).hexdigest()}  {path.name}\n", encoding="ascii")


def _runtime_zip(platform, runtime_id=RUNTIME):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("RUNTIME-MANIFEST.json", json.dumps({
            "schemaVersion": 1, "runtimeId": runtime_id, "platform": platform,
        }))
    return buffer.getvalue()


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "inputs"
    for directory in ("spa", "macos", "windows", "linux"):
        (root / directory).mkdir(parents=True)
    for directory, name in ASSETS:
        data = name.encode()
        if name == names.app_manifest_name(VERSION):
            data = json.dumps({"schemaVersion": 1, "version": VERSION, "runtimeId": RUNTIME,
                               "commit": SOURCE_SHA, "treeSha256": "b" * 64}).encode()
        if name.startswith("update-runtime-"):
            platform = {"macos": names.MACOS_PLATFORM, "windows": names.WINDOWS_PLATFORM,
                        "linux": names.LINUX_PLATFORM}[directory]
            data = _runtime_zip(platform)
        _write(root / directory / name, data)
    return root


def test_bare_runner_cli_stages_exact_release_subjects_without_altering_bytes(inputs, tmp_path):
    out = tmp_path / "staged"
    result = subprocess.run(
        [sys.executable, "-S", str(Path(candidate_manifest.__file__)), "stage",
         "--version", VERSION, "--expected-sha", SOURCE_SHA, "--root", str(inputs), "--out", str(out)],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    expected = {name for directory, name in ASSETS
                if directory != "spa" and not name.startswith(("update-app-", "update-runtime-"))}
    assert len(expected) == 4
    assert {path.name for path in out.iterdir()} == expected | {"SHA256SUMS"}
    manifest = _load()
    tag, entries = manifest.parse_manifest((out / "SHA256SUMS").read_bytes())
    assert tag == "v0.3.6" and set(entries) == expected
    for directory, name in ASSETS:
        if name in expected:
            assert (out / name).read_bytes() == (inputs / directory / name).read_bytes()
            manifest.verify_file(entries, name, out / name)


@pytest.mark.parametrize("asset", ASSETS, ids=[name for _, name in ASSETS])
@pytest.mark.parametrize("sidecar", [False, True])
def test_every_asset_and_sidecar_is_required(inputs, tmp_path, asset, sidecar):
    directory, name = asset
    (inputs / directory / (names.checksum_name(name) if sidecar else name)).unlink()
    out = tmp_path / "staged"
    assert candidate_manifest.main(["stage", "--version", VERSION, "--expected-sha", SOURCE_SHA, "--root", str(inputs), "--out", str(out)]) == 1
    assert not out.exists()


@pytest.mark.parametrize("target", ["installer", "spa", "sidecar"])
def test_corrupt_bytes_and_sidecars_refuse_before_staging(inputs, tmp_path, target):
    directory, name = ASSETS[0 if target == "spa" else 3]
    path = inputs / directory / name
    if target == "sidecar":
        path = path.with_name(names.checksum_name(name))
    path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("damage", ["unexpected", "duplicate", "collision", "directory", "runtime"])
def test_inventory_cannot_silently_omit_or_merge_inputs(inputs, tmp_path, damage):
    source = inputs / ASSETS[3][0] / ASSETS[3][1]
    if damage == "unexpected":
        (inputs / "windows" / "extra.zip").write_bytes(b"extra")
    elif damage in {"duplicate", "collision"}:
        shutil.copyfile(source, inputs / "windows" / (source.name.upper() if damage == "collision" else source.name))
    elif damage == "directory":
        (inputs / "windows" / "nested").mkdir()
    else:
        name = names.runtime_layer_name(names.LINUX_PLATFORM, RUNTIME)
        path = inputs / "linux" / name
        wrong = path.with_name(name.replace(RUNTIME, "f" * 12))
        _write(wrong, path.read_bytes())
        path.unlink()
        path.with_name(names.checksum_name(name)).unlink()
    with pytest.raises(ValueError):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("field,value", [
    ("schemaVersion", True), ("version", "0.3.5"), ("runtimeId", "f" * 12),
    ("runtimeId", "../escape"), ("commit", "short"), ("treeSha256", None),
])
def test_checksummed_but_invalid_app_identity_is_refused(inputs, tmp_path, field, value):
    path = inputs / "macos" / names.app_manifest_name(VERSION)
    identity = json.loads(path.read_text())
    identity[field] = value
    _write(path, json.dumps(identity).encode())
    with pytest.raises(ValueError):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("kind", ["file", "directory", "root"])
def test_symlink_inputs_are_refused(inputs, tmp_path, kind):
    path = inputs / ASSETS[3][0] / ASSETS[3][1] if kind == "file" else (
        inputs / "macos" if kind == "directory" else inputs)
    target = tmp_path / "link-target"
    path.rename(target)
    try:
        path.symlink_to(target, target_is_directory=kind != "file")
    except OSError as exc:
        pytest.skip(f"This host cannot create test symlinks: {exc}")
    with pytest.raises(ValueError, match="real directory|regular file"):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)


def test_existing_output_is_never_overwritten(inputs, tmp_path):
    out = tmp_path / "staged"
    out.mkdir()
    sentinel = out / "SHA256SUMS"
    sentinel.write_bytes(b"prior candidate")
    with pytest.raises(FileExistsError):
        candidate_manifest.stage(VERSION, inputs, out, SOURCE_SHA)
    assert sentinel.read_bytes() == b"prior candidate"


@pytest.mark.parametrize("sha", ["b" * 40, "short", "g" * 40, "A" * 40, ""])
def test_expected_workflow_source_is_required_and_must_match(inputs, tmp_path, sha):
    with pytest.raises(ValueError, match="SHA"):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", sha)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("directory,platform", [
    ("macos", names.MACOS_PLATFORM), ("windows", names.WINDOWS_PLATFORM), ("linux", names.LINUX_PLATFORM),
])
@pytest.mark.parametrize("damage", ["runtime-id", "platform"])
def test_correctly_named_and_checksummed_runtime_must_match_internal_identity(inputs, tmp_path, directory, platform, damage):
    path = inputs / directory / names.runtime_layer_name(platform, RUNTIME)
    data = _runtime_zip(platform, "f" * 12) if damage == "runtime-id" else _runtime_zip("wrong-platform")
    _write(path, data)
    with pytest.raises(ValueError, match="Runtime archive identity"):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("damage", ["missing", "nested", "traversal", "duplicate", "corrupt", "symlink", "oversized", "schema"])
def test_runtime_manifest_must_be_one_valid_regular_root_entry(inputs, tmp_path, damage):
    path = inputs / "macos" / names.runtime_layer_name(names.MACOS_PLATFORM, RUNTIME)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        manifest = json.dumps({"schemaVersion": 1, "runtimeId": RUNTIME, "platform": names.MACOS_PLATFORM})
        if damage == "missing":
            archive.writestr("other.json", manifest)
        elif damage in {"nested", "traversal"}:
            prefix = "nested/" if damage == "nested" else "../"
            archive.writestr(prefix + "RUNTIME-MANIFEST.json", manifest)
        elif damage == "symlink":
            entry = zipfile.ZipInfo("RUNTIME-MANIFEST.json")
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            archive.writestr(entry, manifest)
        elif damage == "oversized":
            archive.writestr("RUNTIME-MANIFEST.json", b" " * (64 * 1024 + 1))
        elif damage == "schema":
            archive.writestr("RUNTIME-MANIFEST.json", manifest.replace('"schemaVersion": 1', '"schemaVersion": 2'))
        else:
            archive.writestr("RUNTIME-MANIFEST.json", manifest)
            if damage == "duplicate":
                with pytest.warns(UserWarning, match="Duplicate name"):
                    archive.writestr("RUNTIME-MANIFEST.json", manifest)
    _write(path, b"not a zip" if damage == "corrupt" else buffer.getvalue())
    with pytest.raises(ValueError):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)
    assert not (tmp_path / "staged").exists()

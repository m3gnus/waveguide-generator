"""Pure contracts for the standalone macOS bundle builder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import zipfile

from scripts.build_bundle import (
    PRUNE_LIBRARY_GLOBS,
    PRUNE_RELATIVE_PATHS,
    copy_tracked_app_files,
    deterministic_zip,
    launcher_stub,
    prune_runtime,
    substitute_info_plist,
    write_app_manifest,
    write_runtime_manifest,
)
from server.platform.paths import app_root
from shared.runtime_id import compute_runtime_id, runtime_id_from_files


def test_runtime_id_hashes_requirements_pins_and_exact_python_without_separators(
    tmp_path: Path,
) -> None:
    requirements = b"fastapi==1\n"
    pins = b"git+https://example.invalid/module@abc\n"
    version = "3.13.12"
    expected = hashlib.sha256(requirements + pins + version.encode()).hexdigest()[:12]
    requirements_path = tmp_path / "runtime.txt"
    pins_path = tmp_path / "pins.txt"
    requirements_path.write_bytes(requirements)
    pins_path.write_bytes(pins)

    assert compute_runtime_id(requirements, pins, version) == expected
    assert runtime_id_from_files(requirements_path, pins_path, version) == expected


def test_manifest_writers_record_layer_identity_and_stable_hashes(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    app = tmp_path / "app"
    runtime.mkdir()
    app.mkdir()

    runtime_payload = write_runtime_manifest(
        runtime,
        python_version="3.13.12",
        runtime_id="0123456789ab",
        requirements=b"runtime\n",
        pins=b"pins\n",
    )
    app_payload = write_app_manifest(
        app,
        version="1.2.3",
        commit="a" * 40,
        runtime_id="0123456789ab",
    )

    assert runtime_payload == {
        "schemaVersion": 1,
        "python": "3.13.12",
        "platform": "macos-arm64",
        "requirementsSha256": hashlib.sha256(b"runtime\n").hexdigest(),
        "pinsSha256": hashlib.sha256(b"pins\n").hexdigest(),
        "runtimeId": "0123456789ab",
    }
    assert app_payload == {
        "schemaVersion": 1,
        "version": "1.2.3",
        "commit": "a" * 40,
        "runtimeId": "0123456789ab",
    }
    assert json.loads((runtime / "RUNTIME-MANIFEST.json").read_text()) == runtime_payload
    assert json.loads((app / "APP-MANIFEST.json").read_text()) == app_payload


def test_git_ls_files_copy_never_includes_ignored_or_untracked_files(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    destination = tmp_path / "app"
    (repo / "server").mkdir(parents=True)
    (repo / "server" / "tracked.py").write_text("tracked = True\n", encoding="utf-8")
    (repo / "server" / "ignored.py").write_text("ignored = True\n", encoding="utf-8")
    (repo / "server" / "untracked.py").write_text("untracked = True\n", encoding="utf-8")
    (repo / ".gitignore").write_text("server/ignored.py\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "server/tracked.py"], cwd=repo, check=True)

    copied = copy_tracked_app_files(repo, destination)

    assert [path.as_posix() for path in copied] == ["server/tracked.py"]
    assert (destination / "server" / "tracked.py").is_file()
    assert not (destination / "server" / "ignored.py").exists()
    assert not (destination / "server" / "untracked.py").exists()


def test_prune_runtime_removes_the_contract_list_and_nested_test_caches(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    for relative in PRUNE_RELATIVE_PATHS:
        target = runtime / relative
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("remove", encoding="utf-8")
    for relative in ("lib/tcl8.6", "lib/tk8.6", "lib/itcl4.2"):
        (runtime / relative).mkdir(parents=True)
    site = runtime / "lib" / "python3.13" / "site-packages"
    (site / "pip-26.0.1.dist-info").mkdir(parents=True)
    (site / "pip-26.0.1.dist-info" / "RECORD").write_text("", encoding="utf-8")
    bin_dir = runtime / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3.13").write_bytes(b"\xcf\xfa\xed\xfe")
    (bin_dir / "python3").symlink_to("python3.13")
    (bin_dir / "python").symlink_to("python3.13")
    (bin_dir / "uvicorn").write_text(
        "#!/tmp/wg-bundle-build/runtime/bin/python3.13\n", encoding="utf-8"
    )
    (bin_dir / "python3.13-config").write_text("#!/bin/sh\n", encoding="utf-8")
    for relative in ("package/tests", "package/sub/test", "package/__pycache__"):
        target = site / relative
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("remove", encoding="utf-8")
    kept = site / "package" / "module.py"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("keep = True\n", encoding="utf-8")

    removed = prune_runtime(runtime)

    assert kept.is_file()
    assert not any((runtime / relative).exists() for relative in PRUNE_RELATIVE_PATHS)
    assert not (site / "package" / "tests").exists()
    assert not (site / "package" / "sub" / "test").exists()
    assert not (site / "package" / "__pycache__").exists()
    assert not (site / "pip-26.0.1.dist-info").exists()
    assert sorted(entry.name for entry in bin_dir.iterdir()) == [
        "python",
        "python3",
        "python3.13",
    ]
    assert (bin_dir / "python3").is_symlink()
    assert {"lib/tcl*", "lib/tk*", "lib/itcl*"} <= set(PRUNE_LIBRARY_GLOBS)
    assert removed


def test_info_plist_substitution_updates_both_bundle_versions(tmp_path: Path) -> None:
    template = tmp_path / "template.plist"
    output = tmp_path / "output.plist"
    with template.open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleIdentifier": "is.hornlab.waveguide-generator-v2",
                "CFBundleShortVersionString": "0.0.1",
                "CFBundleVersion": "0.0.1",
            },
            handle,
        )

    substitute_info_plist(template, output, "1.2.3")

    with output.open("rb") as handle:
        payload = plistlib.load(handle)
    assert payload["CFBundleShortVersionString"] == "1.2.3"
    assert payload["CFBundleVersion"] == "1.2.3"
    assert payload["CFBundleIdentifier"] == "is.hornlab.waveguide-generator-v2"


def test_launcher_stub_sets_bundle_root_and_keeps_rosetta_guard() -> None:
    stub = launcher_stub()

    assert "sysctl.proc_translated" in stub
    assert 'exec arch -arm64 "$0" "$@"' in stub
    assert "export WG2_BUNDLE=1" in stub
    assert 'export WG2_APP_ROOT="$RESOURCES/app"' in stub
    # Caches must leave the sealed bundle untouched.
    assert 'export PYTHONPYCACHEPREFIX="$CACHE_ROOT/pycache"' in stub
    assert 'export NUMBA_CACHE_DIR="$CACHE_ROOT/numba"' in stub
    assert 'CACHE_ROOT="$HOME/Library/Caches/WaveguideGenerator"' in stub
    assert 'exec "../runtime/bin/python3.13" -m launchers.desktop "$@"' in stub


def test_layer_zip_is_sorted_and_omits_appledouble_files(tmp_path: Path) -> None:
    source = tmp_path / "layer"
    source.mkdir()
    (source / "z.txt").write_text("last\n", encoding="utf-8")
    (source / "a.txt").write_text("first\n", encoding="utf-8")
    (source / "._a.txt").write_text("metadata\n", encoding="utf-8")
    (source / "include" / "nested").mkdir(parents=True)
    (source / "include" / "nested" / "header.h").write_text(
        "/* stable */\n", encoding="utf-8"
    )
    archive = tmp_path / "layer.zip"

    deterministic_zip(source, archive)

    with zipfile.ZipFile(archive) as handle:
        assert handle.namelist() == [
            "a.txt",
            "include/",
            "include/nested/",
            "include/nested/header.h",
            "z.txt",
        ]
        assert {item.date_time for item in handle.infolist()} == {
            (1980, 1, 1, 0, 0, 0)
        }


def test_app_root_uses_checkout_by_default_and_bundle_environment_when_set(
    tmp_path: Path,
) -> None:
    checkout = Path(__file__).resolve().parents[2]
    bundled = tmp_path / "Resources" / "app"

    assert app_root(environ={}) == checkout
    assert app_root(environ={"WG2_APP_ROOT": str(bundled)}) == bundled.resolve()

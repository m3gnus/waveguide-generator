"""Pure contracts for the macOS and Windows standalone bundle builder."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
import subprocess
import sys
import tarfile
from types import SimpleNamespace
import zipfile

import pytest

from launchers.macos import generate_icon
from scripts import fetch_spa
from scripts.build_bundle import (
    BundleBuilder,
    BundleError,
    MSVC_RUNTIME_DLLS,
    PYTHON_BUILD,
    PRUNE_LIBRARY_GLOBS,
    PRUNE_RELATIVE_PATHS,
    RUNTIME_RECIPE,
    WINDOWS_ICON_NAME,
    WINDOWS_LAUNCHER_NAME,
    WINDOWS_PLATFORM,
    WINDOWS_PTH_NAME,
    WINDOWS_PRUNE_RELATIVE_PATHS,
    build,
    copy_tracked_app_files,
    deterministic_zip,
    install_spa_layer,
    launcher_stub,
    locate_msvc_runtime_dlls,
    prepare_output_directory,
    prune_runtime,
    require_python_build,
    substitute_info_plist,
    windows_launcher_files,
    windows_pth,
    write_app_manifest,
    write_runtime_manifest,
    write_checksum,
    write_windows_bootstrap,
)
from server.platform.paths import app_root
from shared.runtime_id import compute_runtime_id, runtime_id_from_files


def test_runtime_id_hashes_lock_interpreter_build_and_framed_recipe_inputs(
    tmp_path: Path,
) -> None:
    requirements = b"fastapi==1\n"
    pins = b"git+https://example.invalid/module@abc\n"
    lock = b"anyio==4\n"
    version = "3.13.12"
    requirements_path = tmp_path / "runtime.txt"
    pins_path = tmp_path / "pins.txt"
    lock_path = tmp_path / "lock.txt"
    requirements_path.write_bytes(requirements)
    pins_path.write_bytes(pins)
    lock_path.write_bytes(lock)

    expected = compute_runtime_id(
        requirements,
        pins,
        lock,
        version,
        PYTHON_BUILD,
        RUNTIME_RECIPE,
    )

    assert len(expected) == 12
    assert runtime_id_from_files(
        requirements_path,
        pins_path,
        lock_path,
        version,
        PYTHON_BUILD,
        RUNTIME_RECIPE,
    ) == expected
    assert compute_runtime_id(
        requirements,
        pins,
        lock + b"security-fix\n",
        version,
        PYTHON_BUILD,
        RUNTIME_RECIPE,
    ) != expected
    assert compute_runtime_id(
        requirements,
        pins,
        lock,
        version,
        "different-build",
        RUNTIME_RECIPE,
    ) != expected
    assert compute_runtime_id(
        requirements,
        pins,
        lock,
        version,
        PYTHON_BUILD,
        "different-recipe",
    ) != expected
    assert compute_runtime_id(
        b"ab",
        b"c",
        lock,
        version,
        PYTHON_BUILD,
        RUNTIME_RECIPE,
    ) != compute_runtime_id(
        b"a",
        b"bc",
        lock,
        version,
        PYTHON_BUILD,
        RUNTIME_RECIPE,
    )


def test_python_build_marker_must_match_the_pinned_standalone_artifact(
    tmp_path: Path,
) -> None:
    (tmp_path / "BUILD").write_text("different-build", encoding="ascii")

    with pytest.raises(BundleError, match="different-build.*expected.*20260325"):
        require_python_build(tmp_path, PYTHON_BUILD)


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
        lock=b"lock\n",
        python_build=PYTHON_BUILD,
        runtime_recipe=RUNTIME_RECIPE,
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
        "pythonBuild": PYTHON_BUILD,
        "pythonDistribution": (
            f"cpython-3.13.12+{PYTHON_BUILD}-python-build-standalone"
        ),
        "platform": "macos-arm64",
        "requirementsSha256": hashlib.sha256(b"runtime\n").hexdigest(),
        "pinsSha256": hashlib.sha256(b"pins\n").hexdigest(),
        "lockSha256": hashlib.sha256(b"lock\n").hexdigest(),
        "runtimeRecipe": RUNTIME_RECIPE,
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
    assert b"\r\n" not in (runtime / "RUNTIME-MANIFEST.json").read_bytes()
    assert (app / "APP-MANIFEST.json").read_bytes().endswith(b"\n")

    windows_runtime = tmp_path / "windows-runtime"
    windows_runtime.mkdir()
    for source, _destination in windows_launcher_files():
        (windows_runtime / source).write_bytes(source.encode())
    windows_payload = write_runtime_manifest(
        windows_runtime,
        python_version="3.13.12",
        runtime_id="0123456789ab",
        requirements=b"runtime\n",
        pins=b"pins\n",
        lock=b"lock\n",
        python_build=PYTHON_BUILD,
        runtime_recipe=RUNTIME_RECIPE,
        platform_name=WINDOWS_PLATFORM,
    )
    assert windows_payload["platform"] == "windows-x86_64"
    assert all(
        entry["sha256"] == hashlib.sha256(entry["source"].encode()).hexdigest()
        for entry in windows_payload["launcherFiles"]
    )


def test_spa_stamp_binds_the_installed_tree_and_uses_lf(tmp_path: Path) -> None:
    source = tmp_path / "source" / "dist"
    source.mkdir(parents=True)
    (source / "index.html").write_text("<script src='app.js'></script>\n", encoding="utf-8")
    (source / "app.js").write_bytes(b"console.log('release');\n")
    archive = tmp_path / "spa.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(source, arcname="dist")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    repo = tmp_path / "repo"

    installed = fetch_spa.install_archive(
        archive,
        version="1.2.3",
        digest=digest,
        source="release fixture",
        root=repo,
    )

    stamp_path = installed / fetch_spa.STAMP_NAME
    tree_stamp_path = installed / fetch_spa.TREE_STAMP_NAME
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    assert stamp["sha256"] == digest
    assert tree_stamp_path.read_text(encoding="ascii").strip() == fetch_spa.tree_digest(installed)
    assert b"\r\n" not in stamp_path.read_bytes()
    assert stamp_path.read_bytes().endswith(b"\n")
    assert b"\r\n" not in tree_stamp_path.read_bytes()
    assert tree_stamp_path.read_bytes().endswith(b"\n")
    app = tmp_path / "app"
    install_spa_layer(app, version="1.2.3", archive=None, repo_root=repo)
    assert (app / "frontend" / "dist" / "app.js").is_file()

    (installed / "app.js").write_bytes(b"console.log('tampered');\n")
    with pytest.raises(BundleError, match="changed after its release archive was verified"):
        install_spa_layer(
            tmp_path / "tampered-app",
            version="1.2.3",
            archive=None,
            repo_root=repo,
        )


def test_git_object_copy_uses_committed_bytes_and_excludes_test_trees(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    destination = tmp_path / "app"
    (repo / "server").mkdir(parents=True)
    (repo / "server" / "tests").mkdir()
    (repo / "server" / "tracked.py").write_text("tracked = True\n", encoding="utf-8")
    (repo / "server" / "tests" / "test_shipped.py").write_text(
        "assert False\n", encoding="utf-8"
    )
    (repo / "server" / "ignored.py").write_text("ignored = True\n", encoding="utf-8")
    (repo / ".gitignore").write_text("server/ignored.py\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "add", ".gitignore", "server/tracked.py", "server/tests/test_shipped.py"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Bundle Test",
            "-c",
            "user.email=bundle-test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    (repo / "server" / "tracked.py").write_bytes(b"tracked = False\r\n")
    (repo / "server" / "untracked.py").write_text("untracked = True\n", encoding="utf-8")

    copied = copy_tracked_app_files(repo, destination)

    assert [path.as_posix() for path in copied] == ["server/tracked.py"]
    assert (destination / "server" / "tracked.py").read_bytes() == b"tracked = True\n"
    assert not (destination / "server" / "tests").exists()
    assert not (destination / "server" / "ignored.py").exists()
    assert not (destination / "server" / "untracked.py").exists()


def test_release_builder_refuses_a_dirty_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = repo / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Bundle Test",
            "-c",
            "user.email=bundle-test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    tracked.write_text("modified\n", encoding="utf-8")

    with pytest.raises(BundleError, match="require a clean Git worktree.*tracked.txt"):
        BundleBuilder(repo).require_clean_worktree()


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


def test_windows_prune_runtime_uses_the_flat_install_layout(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    for relative in WINDOWS_PRUNE_RELATIVE_PATHS:
        target = runtime / relative
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("remove", encoding="utf-8")
    for relative in ("tcl", "DLLs/tcl86t.dll", "DLLs/tk86t.dll", "DLLs/_tkinter.pyd"):
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if "." in target.name:
            target.write_bytes(b"dll")
        else:
            target.mkdir(exist_ok=True)
    site = runtime / "Lib" / "site-packages"
    for relative in ("package/tests", "package/sub/test", "package/__pycache__"):
        target = site / relative
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("remove", encoding="utf-8")
    kept = site / "package" / "module.py"
    kept.write_text("keep = True\n", encoding="utf-8")
    (runtime / "python.exe").write_bytes(b"python")
    (runtime / "pythonw.exe").write_bytes(b"pythonw")

    removed = prune_runtime(runtime, platform_name=WINDOWS_PLATFORM)

    assert kept.is_file()
    assert (runtime / "python.exe").is_file()
    assert (runtime / "pythonw.exe").is_file()
    assert not any((runtime / relative).exists() for relative in WINDOWS_PRUNE_RELATIVE_PATHS)
    assert not (runtime / "tcl").exists()
    assert not (runtime / "DLLs" / "_tkinter.pyd").exists()
    assert not (site / "package" / "tests").exists()
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
    (source / "include" / "nested" / "header.h").write_text("/* stable */\n", encoding="utf-8")
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
        assert {item.date_time for item in handle.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_layer_zip_materializes_internal_file_symlinks_as_regular_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime"
    (source / "bin").mkdir(parents=True)
    (source / "bin" / "python3.13").write_bytes(b"python")
    (source / "bin" / "python3").symlink_to("python3.13")
    archive = tmp_path / "runtime.zip"

    deterministic_zip(source, archive)

    with zipfile.ZipFile(archive) as handle:
        alias = handle.getinfo("bin/python3")
        assert stat.S_IFMT(alias.external_attr >> 16) == stat.S_IFREG
        assert handle.read(alias) == b"python"


def test_platform_neutral_app_zip_is_byte_identical_across_host_metadata(
    tmp_path: Path,
) -> None:
    first = tmp_path / "mac-app"
    second = tmp_path / "windows-app"
    for root in (first, second):
        (root / "nested").mkdir(parents=True)
        (root / "nested" / "bytes.txt").write_bytes(b"preserve\r\nthese\x00bytes\n")
    (first / "nested" / "bytes.txt").chmod(0o755)
    (second / "nested" / "bytes.txt").chmod(0o600)
    first_zip = tmp_path / "first.zip"
    second_zip = tmp_path / "second.zip"

    for source, output in ((first, first_zip), (second, second_zip)):
        deterministic_zip(
            source,
            output,
            canonical_modes=True,
            compression=zipfile.ZIP_STORED,
        )

    assert first_zip.read_bytes() == second_zip.read_bytes()
    with zipfile.ZipFile(first_zip) as archive:
        assert archive.read("nested/bytes.txt") == b"preserve\r\nthese\x00bytes\n"
        assert archive.getinfo("nested/bytes.txt").compress_type == zipfile.ZIP_STORED


def test_windows_distribution_zip_retains_the_enclosing_folder(tmp_path: Path) -> None:
    source = tmp_path / "Waveguide Generator"
    (source / "app").mkdir(parents=True)
    (source / "Waveguide Generator.exe").write_bytes(b"launcher")
    archive = tmp_path / "Waveguide Generator-1.2.3-windows-x86_64.zip"

    deterministic_zip(source, archive, archive_root=source.name)

    with zipfile.ZipFile(archive) as handle:
        assert handle.namelist() == [
            "Waveguide Generator/",
            "Waveguide Generator/Waveguide Generator.exe",
            "Waveguide Generator/app/",
        ]


def test_checksum_sidecar_names_the_final_dotted_public_asset(tmp_path: Path) -> None:
    asset = tmp_path / "Waveguide.Generator-1.2.3-macos-arm64.dmg"
    asset.write_bytes(b"installer")

    sidecar = write_checksum(asset)

    assert sidecar.read_text(encoding="ascii") == (
        f"{hashlib.sha256(b'installer').hexdigest()}  {asset.name}\n"
    )
    assert b"\r\n" not in sidecar.read_bytes()


def test_output_directory_must_be_empty(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    prepare_output_directory(output)
    assert output.is_dir()

    (output / "stale.zip").write_bytes(b"stale")
    with pytest.raises(BundleError, match="must be empty.*stale.zip"):
        prepare_output_directory(output)


def test_release_workflow_publishes_one_complete_draft_inventory() -> None:
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    assert workflow.count("softprops/action-gh-release@") == 1
    assert "needs: [spa, macos-bundle, windows-bundle]" in workflow
    assert "draft: true" in workflow
    assert 'gh release edit "$RELEASE_TAG" --draft=false' in workflow
    assert "Reuse a runtime already published" not in workflow
    assert "Validated seven release asset pairs." in workflow
    assert "Waveguide.Generator-*-macos-arm64.dmg" in workflow
    assert "Waveguide.Generator-*-windows-x86_64.zip" in workflow


@pytest.mark.parametrize(("runtime_only", "app_only"), ((True, False), (False, True)))
def test_layer_only_builds_are_refused_as_unverified_publishable_assets(
    tmp_path: Path,
    runtime_only: bool,
    app_only: bool,
) -> None:
    builder = BundleBuilder(
        tmp_path,
        system=lambda: "Darwin",
        machine=lambda: "arm64",
    )
    args = SimpleNamespace(
        platform="macos",
        python_version="3.13.12",
        output=tmp_path / "output",
        app_only=app_only,
        runtime_only=runtime_only,
        spa=None,
        skip_verify=False,
    )

    with pytest.raises(BundleError, match="Layer-only builds are disabled"):
        build(args, builder=builder)


def test_windows_bootstrap_executes_only_for_real_no_script_launch(
    tmp_path: Path,
) -> None:
    expected = "runtime\\Lib\nruntime\\DLLs\nruntime\\Lib\\site-packages\napp\nimport site\n"
    assert windows_pth() == expected
    bundle = tmp_path / "bundle"
    app = bundle / "app"
    harness = tmp_path / "harness"
    launchers = app / "launchers"
    launchers.mkdir(parents=True)
    harness.mkdir()
    (bundle / WINDOWS_LAUNCHER_NAME).write_bytes(b"test executable identity")
    write_windows_bootstrap(app)
    (harness / "sitecustomize.py").write_text(
        "import os\n"
        "import sys\n"
        'sys.executable = os.environ["WG_TEST_EXECUTABLE"]\n'
        "import wg_desktop_bootstrap\n",
        encoding="utf-8",
        newline="\n",
    )
    (launchers / "__init__.py").write_text("", encoding="utf-8")
    (app / "probe_support.py").write_text(
        "from __future__ import annotations\n"
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n"
        "def record(kind, argv=None):\n"
        "    payload = {\n"
        "        'kind': kind,\n"
        "        'argv': list(sys.argv if argv is None else argv),\n"
        "        'bundle': os.environ.get('WG2_BUNDLE'),\n"
        "        'appRoot': os.environ.get('WG2_APP_ROOT'),\n"
        "        'pycache': os.environ.get('PYTHONPYCACHEPREFIX'),\n"
        "        'numba': os.environ.get('NUMBA_CACHE_DIR'),\n"
        "    }\n"
        "    Path(os.environ['WG_TEST_RESULT']).write_text(json.dumps(payload))\n",
        encoding="utf-8",
        newline="\n",
    )
    (launchers / "desktop.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "from probe_support import record\n"
        "def main(argv=None):\n"
        "    record('desktop', sys.argv[1:] if argv is None else argv)\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
        newline="\n",
    )
    script = app / "worker.py"
    script.write_text(
        "from probe_support import record\nrecord('script')\n",
        encoding="utf-8",
        newline="\n",
    )

    base_environment = dict(os.environ)
    base_environment.update(
        {
            "PYTHONPATH": os.pathsep.join((str(harness), str(app))),
            "PYTHONNOUSERSITE": "1",
            "LOCALAPPDATA": str(tmp_path / "local-app-data"),
            "WG_TEST_EXECUTABLE": str(bundle / WINDOWS_LAUNCHER_NAME),
        }
    )
    for name in ("WG2_BUNDLE", "WG2_APP_ROOT", "PYTHONPYCACHEPREFIX", "NUMBA_CACHE_DIR"):
        base_environment.pop(name, None)

    invocations = (
        ("no-script", [], "desktop", [], 1),
        (
            "module",
            ["-m", "launchers.desktop", "--port", "3110"],
            "desktop",
            ["--port", "3110"],
            0,
        ),
        (
            "command",
            ["-c", "from probe_support import record; record('command')"],
            "command",
            ["-c"],
            0,
        ),
        (
            "script",
            [str(script), "worker-argument"],
            "script",
            [str(script), "worker-argument"],
            0,
        ),
    )
    for label, arguments, expected_kind, expected_argv, expected_returncode in invocations:
        result_path = tmp_path / f"{label}.json"
        environment = dict(base_environment)
        environment["WG_TEST_RESULT"] = str(result_path)
        result = subprocess.run(
            [sys.executable, *arguments],
            cwd=tmp_path,
            env=environment,
            input="" if not arguments else None,
            text=True,
            check=False,
            capture_output=True,
        )

        # Raising SystemExit from sitecustomize after the no-script desktop
        # returns makes CPython report an init_import_site failure. pythonw has
        # no console, and the desktop has already run for its full lifetime.
        assert result.returncode == expected_returncode, result.stderr
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert payload["kind"] == expected_kind
        assert payload["argv"] == expected_argv
        assert payload["bundle"] == "1"
        assert payload["appRoot"] == str(app)
        cache = tmp_path / "local-app-data" / "WaveguideGenerator" / "cache"
        assert payload["pycache"] == str(cache / "pycache")
        assert payload["numba"] == str(cache / "numba")


def test_msvc_dll_discovery_uses_where_then_system32_then_visual_studio(
    tmp_path: Path,
) -> None:
    system_root = tmp_path / "Windows"
    program_files = tmp_path / "Program Files"
    where_dll = tmp_path / "path" / MSVC_RUNTIME_DLLS[0]
    system_dll = system_root / "System32" / MSVC_RUNTIME_DLLS[1]
    visual_dll = (
        program_files
        / "Microsoft Visual Studio"
        / "2022"
        / "BuildTools"
        / "VC"
        / "Redist"
        / "MSVC"
        / "14.40"
        / "x64"
        / "Microsoft.VC143.CRT"
        / MSVC_RUNTIME_DLLS[2]
    )
    for path in (where_dll, system_dll, visual_dll):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())

    def where(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        output = str(where_dll) if command[-1] == where_dll.name else ""
        return subprocess.CompletedProcess(command, 0 if output else 1, output, "")

    located = locate_msvc_runtime_dlls(
        runner=where,
        environ={
            "SystemRoot": str(system_root),
            "ProgramFiles": str(program_files),
        },
    )

    assert located == {
        MSVC_RUNTIME_DLLS[0]: where_dll.resolve(),
        MSVC_RUNTIME_DLLS[1]: system_dll.resolve(),
        MSVC_RUNTIME_DLLS[2]: visual_dll.resolve(),
    }


def test_msvc_dll_discovery_fails_loudly_when_a_required_file_is_absent(
    tmp_path: Path,
) -> None:
    with pytest.raises(BundleError, match=MSVC_RUNTIME_DLLS[0]):
        locate_msvc_runtime_dlls(
            runner=lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", ""),
            environ={
                "SystemRoot": str(tmp_path / "Windows"),
                "ProgramFiles": str(tmp_path / "Program Files"),
            },
        )


def test_windows_runtime_build_requests_the_explicit_host_target_and_copies_msvc(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "server").mkdir(parents=True)
    (repo / "server" / "requirements-runtime.txt").write_bytes(b"runtime\n")
    (repo / "server" / "requirements-pins.txt").write_bytes(b"pins\n")
    (repo / "server" / "requirements-lock.txt").write_bytes(b"lock\n")
    redist = tmp_path / "redist"
    redist.mkdir()
    for filename in MSVC_RUNTIME_DLLS:
        (redist / filename).write_bytes(filename.encode())
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["uv", "python", "install"]:
            install_dir = Path(command[command.index("--install-dir") + 1])
            runtime = install_dir / "managed"
            (runtime / "Lib" / "site-packages").mkdir(parents=True)
            (runtime / "DLLs").mkdir()
            (runtime / "BUILD").write_text(PYTHON_BUILD, encoding="ascii")
            for filename in ("python.exe", "pythonw.exe", "python313.dll", "python3.dll"):
                (runtime / filename).write_bytes(filename.encode())
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "where.exe":
            path = redist / command[1]
            return subprocess.CompletedProcess(command, 0, str(path), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    destination = tmp_path / "scratch" / "runtime"
    destination.parent.mkdir()
    builder = BundleBuilder(repo, runner=run, system=lambda: "Windows", machine=lambda: "AMD64")

    builder.build_runtime(
        destination,
        python_version="3.13.12",
        python_build=PYTHON_BUILD,
        runtime_recipe=RUNTIME_RECIPE,
        runtime_id="0123456789ab",
        requirements=b"runtime\n",
        pins=b"pins\n",
        lock=b"lock\n",
        platform_name=WINDOWS_PLATFORM,
    )

    install = next(command for command in commands if command[:3] == ["uv", "python", "install"])
    assert install[-1] == "cpython-3.13.12-windows-x86_64-none"
    pip = next(command for command in commands if command[:3] == ["uv", "pip", "install"])
    assert pip[pip.index("--python") + 1] == str(destination / "python.exe")
    constraint = Path(pip[pip.index("-c") + 1])
    assert constraint.name == "requirements-lock.txt"
    assert constraint.read_bytes() == b"lock\n"
    assert any(command[:3] == ["uv", "pip", "check"] for command in commands)
    for filename in MSVC_RUNTIME_DLLS:
        assert (destination / filename).read_bytes() == filename.encode()
    manifest = json.loads((destination / "RUNTIME-MANIFEST.json").read_text())
    assert manifest["platform"] == "windows-x86_64"
    assert manifest["pythonBuild"] == PYTHON_BUILD
    assert manifest["lockSha256"] == hashlib.sha256(b"lock\n").hexdigest()


def test_windows_layout_writer_copies_launcher_dlls_layers_pth_and_icon(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime-source"
    app = tmp_path / "app-source"
    destination = tmp_path / "Waveguide Generator"
    runtime.mkdir()
    app.mkdir()
    (runtime / "pythonw.exe").write_bytes(b"gui python")
    (runtime / "python313.dll").write_bytes(b"python dll")
    (runtime / "python3.dll").write_bytes(b"stable dll")
    for filename in MSVC_RUNTIME_DLLS:
        (runtime / filename).write_bytes(filename.encode())
    (runtime / "Lib").mkdir()
    (runtime / "Lib" / "os.py").write_text("# stdlib\n", encoding="utf-8")
    (app / "APP-MANIFEST.json").write_text("{}\n", encoding="utf-8")
    builder = BundleBuilder(tmp_path)

    builder.assemble_windows_bundle(
        destination,
        runtime_root=runtime,
        app_root=app,
        icon_writer=lambda path: path.write_bytes(b"ico"),
    )

    assert (destination / WINDOWS_LAUNCHER_NAME).read_bytes() == b"gui python"
    assert (destination / WINDOWS_PTH_NAME).read_text(encoding="utf-8") == windows_pth()
    assert (destination / WINDOWS_ICON_NAME).read_bytes() == b"ico"
    assert (destination / "runtime" / "Lib" / "os.py").is_file()
    assert (destination / "app" / "APP-MANIFEST.json").is_file()
    for filename in ("python313.dll", "python3.dll", *MSVC_RUNTIME_DLLS):
        assert (destination / filename).read_bytes() == (runtime / filename).read_bytes()


def test_stdlib_icon_generator_writes_a_multi_resolution_ico(tmp_path: Path) -> None:
    icon = tmp_path / "WaveguideGenerator.ico"

    generate_icon.build_ico(icon)

    payload = icon.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", payload)
    assert (reserved, kind, count) == (0, 1, len(generate_icon.ICO_SIZES))
    widths = [payload[6 + index * 16] or 256 for index in range(count)]
    assert widths == list(generate_icon.ICO_SIZES)
    assert b"\x89PNG\r\n\x1a\n" in payload


def test_windows_target_refuses_a_non_windows_host_before_building(tmp_path: Path) -> None:
    builder = BundleBuilder(
        tmp_path,
        system=lambda: "Darwin",
        machine=lambda: "arm64",
    )
    args = SimpleNamespace(
        platform="windows",
        python_version="3.13.12",
        output=tmp_path / "output",
        app_only=False,
        runtime_only=False,
        spa=None,
        skip_verify=True,
    )

    with pytest.raises(BundleError, match="Windows host.*cannot cross-install"):
        build(args, builder=builder)


def test_app_root_uses_checkout_by_default_and_bundle_environment_when_set(
    tmp_path: Path,
) -> None:
    checkout = Path(__file__).resolve().parents[2]
    bundled = tmp_path / "Resources" / "app"

    assert app_root(environ={}) == checkout
    assert app_root(environ={"WG2_APP_ROOT": str(bundled)}) == bundled.resolve()

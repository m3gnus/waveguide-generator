"""Pure contracts for the macOS, Windows and Linux standalone bundle builder."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
from types import SimpleNamespace
import zipfile

import pytest

from launchers.macos import generate_icon
from scripts import build_bundle, fetch_spa
from shared import release_assets
from scripts.build_bundle import (
    BundleBuilder,
    BundleError,
    MSVC_RUNTIME_DLLS,
    PYTHON_BUILD,
    PRUNE_LIBRARY_GLOBS,
    PRUNE_RELATIVE_PATHS,
    RUNTIME_RECIPE,
    LINUX_BUNDLE_DIRECTORY,
    LINUX_DESKTOP_ENTRY_NAME,
    LINUX_ICON_NAME,
    LINUX_INSTALLER_NAME,
    LINUX_INSTALLER_SOURCE,
    LINUX_LAUNCHER_NAME,
    LINUX_PLATFORM,
    LINUX_PRUNE_RELATIVE_PATHS,
    LINUX_README_NAME,
    LINUX_UNINSTALLER_NAME,
    LINUX_UNINSTALLER_SOURCE,
    WINDOWS_CREATE_NEW_PROCESS_GROUP,
    WINDOWS_CREATE_NO_WINDOW,
    WINDOWS_ICON_NAME,
    WINDOWS_LAUNCHER_NAME,
    WINDOWS_PLATFORM,
    WINDOWS_PTH_NAME,
    WINDOWS_PRUNE_RELATIVE_PATHS,
    WINDOWS_PYVENV_NAME,
    WINDOWS_README_NAME,
    INNO_COMPILER_ENV,
    WINDOWS_RUNTIME_PTH_NAME,
    build,
    copy_tracked_app_files,
    deterministic_tar_gz,
    deterministic_zip,
    linux_desktop_entry,
    linux_launcher,
    install_spa_layer,
    locate_inno_compiler,
    max_payload_depth,
    is_x64_pe,
    locate_msvc_runtime_dlls,
    pe_file_version,
    prepare_output_directory,
    prune_runtime,
    require_python_build,
    substitute_info_plist,
    write_launcher_stub,
    windows_desktop_bootstrap,
    windows_launcher_files,
    windows_pth,
    windows_pyvenv_cfg,
    windows_runtime_pth,
    write_app_manifest,
    write_checksum,
    write_json,
    write_runtime_manifest,
    write_windows_bootstrap,
)
from server.platform.instance import PORT_ENV
from server.platform.paths import DATA_DIR_ENV, app_root
from shared.runtime_id import compute_runtime_id, runtime_id_from_files


def _version_resource(version: tuple[int, int, int, int], resource_va: int) -> bytes:
    """Build a one-entry PE resource directory holding a ``VS_FIXEDFILEINFO``."""

    major, minor, build, revision = version
    fixed = struct.pack(
        "<IIII",
        0xFEEF04BD,
        0x00010000,
        (major << 16) | minor,
        (build << 16) | revision,
    )
    payload = 88
    blob = bytearray(payload)
    # Three nested directories -- type, name, language -- each with one entry,
    # which is the shape a real DLL's version resource has.
    struct.pack_into("<IIHHHH", blob, 0, 0, 0, 0, 0, 0, 1)
    struct.pack_into("<II", blob, 16, 16, 0x80000000 | 24)  # RT_VERSION
    struct.pack_into("<IIHHHH", blob, 24, 0, 0, 0, 0, 0, 1)
    struct.pack_into("<II", blob, 40, 1, 0x80000000 | 48)
    struct.pack_into("<IIHHHH", blob, 48, 0, 0, 0, 0, 0, 1)
    struct.pack_into("<II", blob, 64, 0x409, 72)
    struct.pack_into("<IIII", blob, 72, resource_va + payload, len(fixed), 0, 0)
    return bytes(blob) + fixed


def write_pe_stub(
    path: Path,
    *,
    machine: int = 0x8664,
    version: tuple[int, int, int, int] | None = None,
) -> Path:
    """Write the smallest file the architecture probe accepts as a PE image.

    With ``version`` the stub also carries the ``.rsrc`` section a real
    redistributable DLL uses to report its file version, so the builder can
    tell one serviced set from a mixture of two.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    pe_offset = 0x40
    if version is None:
        header = bytearray(pe_offset + 6)
        header[0:2] = b"MZ"
        header[0x3C:0x40] = struct.pack("<I", pe_offset)
        header[pe_offset : pe_offset + 4] = b"PE\0\0"
        header[pe_offset + 4 : pe_offset + 6] = struct.pack("<H", machine)
        path.write_bytes(bytes(header) + path.name.encode())
        return path

    optional = pe_offset + 24
    optional_size = 240  # PE32+ with all sixteen data directories
    section_table = optional + optional_size
    raw_offset = section_table + 40
    resource_va = 0x1000
    blob = _version_resource(version, resource_va)
    image = bytearray(raw_offset)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, pe_offset)
    image[pe_offset : pe_offset + 4] = b"PE\0\0"
    struct.pack_into("<HH", image, pe_offset + 4, machine, 1)
    struct.pack_into("<H", image, pe_offset + 20, optional_size)
    struct.pack_into("<H", image, optional, 0x20B)
    struct.pack_into("<I", image, optional + 108, 16)
    struct.pack_into("<II", image, optional + 128, resource_va, len(blob))
    image[section_table : section_table + 8] = b".rsrc\0\0\0"
    struct.pack_into(
        "<IIII", image, section_table + 8, len(blob), resource_va, len(blob), raw_offset
    )
    path.write_bytes(bytes(image) + blob)
    return path


def test_runtime_id_hashes_raw_lock_interpreter_build_and_framed_recipe_inputs(
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
    requirements_path.write_bytes(requirements.replace(b"\n", b"\r\n"))
    assert runtime_id_from_files(
        requirements_path,
        pins_path,
        lock_path,
        version,
        PYTHON_BUILD,
        RUNTIME_RECIPE,
    ) != expected
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
        # Excludes the manifest itself, or no two builds could agree.
        "treeSha256": build_bundle.tree_digest(
            app, exclude=frozenset({"APP-MANIFEST.json"})
        ),
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

    assert [path.as_posix() for path in copied.paths] == ["server/tracked.py"]
    assert copied.executables == frozenset()
    assert (destination / "server" / "tracked.py").read_bytes() == b"tracked = True\n"
    assert not (destination / "server" / "tests").exists()
    assert not (destination / "server" / "ignored.py").exists()
    assert not (destination / "server" / "untracked.py").exists()


def test_materialized_app_files_carry_gits_mode(tmp_path: Path) -> None:
    """`write_bytes` does not carry a mode, and for a long time nothing did.

    Seven tracked app files are 100755 in the tree -- scripts/install.sh and
    launchers/linux/launch-wg.sh among them -- and every one of them shipped at
    0o644. "The app layer has no executable files" was therefore a fact about
    the materializer, not about the source, which is why the layer digest had to
    drop the executable bit entirely to agree across platforms.
    """

    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "scripts" / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (repo / "scripts" / "plain.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "update-index", "--chmod=+x", "scripts/install.sh"], cwd=repo, check=True
    )
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)

    destination = tmp_path / "app"
    destination.mkdir()
    copied = copy_tracked_app_files(repo, destination)

    assert copied.executables == frozenset({"scripts/install.sh"})
    if os.name != "nt":
        assert (destination / "scripts" / "install.sh").stat().st_mode & 0o111
        assert not (destination / "scripts" / "plain.py").stat().st_mode & 0o111

    # And the manifest agrees with the layer in both directions.
    build_bundle.assert_app_layer_modes_match_git(destination, copied.executables)
    if os.name != "nt":
        with pytest.raises(BundleError, match="disagree with Git"):
            build_bundle.assert_app_layer_modes_match_git(destination, frozenset())


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


def test_prune_runtime_keeps_the_gmsh_library_beside_the_interpreter(
    tmp_path: Path,
) -> None:
    """Gmsh's native library is not in site-packages, and nothing else is.

    The gmsh wheel installs ``gmsh-4.15.dll`` into ``<prefix>/Lib`` (and the
    POSIX equivalent into ``<prefix>/lib``) rather than beside ``gmsh.py`` in
    site-packages -- ``gmsh.py`` finds it by walking up from its own module
    directory. Every other dependency ships its binaries inside site-packages,
    so a prune glob or a staging step written around that assumption removes
    the one library the mesher, every STEP export and every solve depend on,
    and the bundle fails only at run time on the user's machine.

    The Windows prune list already globs ``Lib/tcl*``/``Lib/tk*`` in exactly
    this directory, so the blast radius is one careless pattern away.
    """

    runtime = tmp_path / "runtime"
    windows_library = runtime / "Lib" / "gmsh-4.15.dll"
    windows_library.parent.mkdir(parents=True)
    windows_library.write_bytes(b"gmsh")
    (runtime / "Lib" / "site-packages").mkdir()
    (runtime / "Lib" / "site-packages" / "gmsh.py").write_text("", encoding="utf-8")
    (runtime / "Lib" / "tcl8.6").mkdir()

    prune_runtime(runtime, platform_name=WINDOWS_PLATFORM)

    assert windows_library.is_file(), "the Windows bundle lost its gmsh library"
    assert not (runtime / "Lib" / "tcl8.6").exists()

    posix_runtime = tmp_path / "posix"
    posix_library = posix_runtime / "lib" / "libgmsh.4.15.dylib"
    posix_library.parent.mkdir(parents=True)
    posix_library.write_bytes(b"gmsh")
    (posix_runtime / "lib" / "python3.13" / "site-packages").mkdir(parents=True)
    (posix_runtime / "lib" / "tcl8.6").mkdir()

    prune_runtime(posix_runtime)

    assert posix_library.is_file(), "the POSIX bundle lost its gmsh library"
    assert not (posix_runtime / "lib" / "tcl8.6").exists()


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


def test_the_macos_launcher_is_compiled_not_a_script(tmp_path: Path) -> None:
    """A script main executable made the bundle unopenable as downloaded.

    It made the bundle a "script bundle" -- codesign reports
    ``Format=app bundle with generic`` -- which Gatekeeper does not give the
    ordinary unsigned-app treatment. Measured against a locally built .dmg with a
    quarantine attribute applied: SIGKILL (137) launched directly, blocked with
    no process through ``open``, and working only once quarantine was cleared.

    The source must still do everything the script did, so those are asserted
    against the C rather than against a generated string.
    """

    source = (Path(__file__).resolve().parents[2] / "launchers" / "macos" / "launcher.c").read_text(
        encoding="utf-8"
    )
    assert 'setenv("WG2_BUNDLE", "1", 1)' in source
    assert 'setenv("WG2_APP_ROOT", app_root, 1)' in source
    # Caches must leave the sealed bundle untouched; writing into it breaks the
    # signature and the next launch fails as damaged.
    assert 'setenv("PYTHONPYCACHEPREFIX", pycache, 1)' in source
    assert 'setenv("NUMBA_CACHE_DIR", numba, 1)' in source
    assert "Library/Caches/WaveguideGenerator" in source
    assert "runtime/bin/python3.13" in source
    assert '"launchers.desktop"' in source
    assert "execv(interpreter, args)" in source
    # A compiled arm64 binary cannot be started translated, so the script's
    # Rosetta re-exec has no counterpart and must not be reintroduced.
    assert "proc_translated" not in source

    written = tmp_path / "Waveguide Generator"
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(list(command))
        written.write_bytes(b"\xcf\xfa\xed\xfe")  # Mach-O magic, standing in
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    write_launcher_stub(
        written,
        repo_root=Path(__file__).resolve().parents[2],
        runner=runner,
    )

    assert calls and calls[0][0] == "cc"
    assert "-arch" in calls[0] and "arm64" in calls[0]
    # Only where the filesystem has an executable bit. NTFS does not, and
    # os.chmod there toggles the read-only attribute and nothing else -- the
    # same reason tree_digest stopped reading the bit off the filesystem at all.
    # Asserting it on Windows tests the platform, not the launcher.
    if os.name != "nt":
        assert written.stat().st_mode & 0o111


def test_a_missing_compiler_fails_the_build_rather_than_falling_back(tmp_path: Path) -> None:
    """Falling back to a script would rebuild the exact failure this removes.

    Such a bundle builds, signs, uploads and installs, and only then refuses to
    open -- on the user's machine, not on the builder's.
    """

    def failing_runner(command, **kwargs):
        return SimpleNamespace(returncode=127, stdout=b"", stderr=b"cc: not found")

    with pytest.raises(BundleError, match="Could not compile the macOS launcher"):
        write_launcher_stub(
            tmp_path / "Waveguide Generator",
            repo_root=Path(__file__).resolve().parents[2],
            runner=failing_runner,
        )


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


def test_platform_neutral_app_identity_uses_content_with_a_compressed_archive(
    tmp_path: Path,
) -> None:
    first = tmp_path / "mac-app"
    second = tmp_path / "windows-app"
    for root in (first, second):
        (root / "nested").mkdir(parents=True)
        (root / "nested" / "bytes.txt").write_bytes(b"preserve\r\nthese\x00bytes\n")
    (first / "nested" / "bytes.txt").chmod(0o600)
    (second / "nested" / "bytes.txt").chmod(0o600)
    first_zip = tmp_path / "first.zip"
    second_zip = tmp_path / "second.zip"

    deterministic_zip(first, first_zip, canonical_modes=True)
    deterministic_zip(
        second,
        second_zip,
        canonical_modes=True,
        compression=zipfile.ZIP_STORED,
    )

    assert first_zip.read_bytes() != second_zip.read_bytes()
    assert build_bundle.tree_digest(first) == build_bundle.tree_digest(second)
    with zipfile.ZipFile(first_zip) as archive:
        assert archive.read("nested/bytes.txt") == b"preserve\r\nthese\x00bytes\n"
        assert archive.getinfo("nested/bytes.txt").compress_type == zipfile.ZIP_DEFLATED


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


def test_windows_instructions_sit_outside_the_folder_so_they_precede_extraction(
    tmp_path: Path,
) -> None:
    """Both Windows walls are hit before the app is ever launched.

    Explorer copies the download mark onto everything it extracts, so an
    extracted launcher is refused by SmartScreen behind a dialog whose only
    visible button is "Don't run"; and an install root over ~127 characters
    fails partway through extraction. Instructions inside the folder would be
    read only after both have already happened, so they go at the archive root.
    """

    source = tmp_path / "Waveguide Generator"
    (source / "app").mkdir(parents=True)
    (source / "Waveguide Generator.exe").write_bytes(b"launcher")
    archive = tmp_path / "Waveguide Generator-1.2.3-windows-x86_64.zip"
    readme = BundleBuilder(tmp_path).windows_readme()

    deterministic_zip(
        source,
        archive,
        archive_root=source.name,
        extra_root_files={WINDOWS_README_NAME: readme},
    )

    with zipfile.ZipFile(archive) as handle:
        assert WINDOWS_README_NAME in handle.namelist()
        raw = handle.read(WINDOWS_README_NAME)

    # Notepad is the default handler for .txt and shows a lone LF as one line.
    assert raw.count(b"\r\n") == raw.count(b"\n")
    text = raw.decode("utf-8")
    # Unblock is the whole point: it is done on the .zip, before extracting, and
    # it stops the mark reaching the launcher at all. Naming only the recovery
    # path ("More info" > "Run anyway") would leave the dead end in place.
    assert "Unblock" in text
    assert "Windows protected your PC" in text
    # The length limit has to arrive with a destination that satisfies it.
    assert "C:\\wg" in text


def test_installer_path_budget_is_measured_from_the_bundle_not_hardcoded(
    tmp_path: Path,
) -> None:
    """A number written into the .iss would rot the first time a dep got deeper.

    bempp's kernel filenames currently set this. Pruning the macOS Metal package
    moves it by one character, so nothing about it is stable enough to copy.
    """

    bundle = tmp_path / "Waveguide Generator"
    (bundle / "runtime" / "Lib").mkdir(parents=True)
    (bundle / "Waveguide Generator.exe").write_bytes(b"launcher")
    deep = bundle / "runtime" / "Lib" / ("k" * 60)
    deep.write_bytes(b"kernel")

    measured = max_payload_depth(bundle)

    assert measured == len(deep.relative_to(bundle).as_posix())
    # Directories must not count: an empty one deeper than every file would
    # shrink the allowed install root for no reason.
    (bundle / "runtime" / "Lib" / ("d" * 90)).mkdir()
    assert max_payload_depth(bundle) == measured


def test_a_bundle_with_no_files_is_refused_rather_than_measured_as_zero(
    tmp_path: Path,
) -> None:
    """Zero would hand the installer the whole 259 characters and guarantee a
    mid-extraction failure it was written to prevent."""

    empty = tmp_path / "Waveguide Generator"
    (empty / "runtime").mkdir(parents=True)

    with pytest.raises(BundleError, match="no files to measure"):
        max_payload_depth(empty)


def test_inno_compiler_override_is_validated_before_the_build_starts(
    tmp_path: Path,
) -> None:
    real = tmp_path / "ISCC.exe"
    real.write_bytes(b"compiler")

    assert locate_inno_compiler({INNO_COMPILER_ENV: str(real)}) == real

    missing = tmp_path / "absent" / "ISCC.exe"
    with pytest.raises(BundleError, match="does not name a file"):
        locate_inno_compiler({INNO_COMPILER_ENV: str(missing)})


def test_installer_points_every_shown_icon_at_the_staged_ico() -> None:
    """The launcher is a byte copy of pythonw.exe and nothing patches its
    resources, so an icon read from the .exe is Python's, not ours -- visible
    on the Start-menu and desktop shortcuts and in Apps & features.

    assemble_windows_bundle stages WINDOWS_ICON_NAME at the payload root, so
    the fix is to name it rather than to embed anything.
    """

    script = (
        Path(__file__).resolve().parents[2] / "installers" / "windows" / "bundle-setup.iss"
    ).read_text(encoding="utf-8")

    reference = 'IconFilename: "{app}' + chr(92) + WINDOWS_ICON_NAME + '"'
    shortcuts = [
        line
        for line in script.splitlines()
        if line.startswith("Name: ") and "Waveguide Generator.exe" in line
    ]
    assert shortcuts, "no shortcut points at the launcher"
    assert all(reference in line for line in shortcuts)
    assert "UninstallDisplayIcon={app}" + chr(92) + WINDOWS_ICON_NAME in script
    # The build's own .ico is what the installer executable wears too.
    assert "SetupIconFile={#PayloadDir}" + chr(92) + WINDOWS_ICON_NAME in script


def test_installer_script_pins_the_per_user_install_that_the_updater_needs() -> None:
    """launchers/apply_update.py renames directories in place with no elevation
    path, so a Program Files install breaks in-app updates for every non-admin
    user -- and breaks them later, at update time, far from the installer.

    PrivilegesRequiredOverridesAllowed must stay empty too: without it /ALLUSERS
    or an elevated launch puts the tree back under Program Files.
    """

    script = (
        Path(__file__).resolve().parents[2] / "installers" / "windows" / "bundle-setup.iss"
    ).read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in script
    assert "PrivilegesRequiredOverridesAllowed=\n" in script
    assert "DefaultDirName={localappdata}\\Programs\\Waveguide Generator" in script

    # A silent run must fail with an exit code rather than sit on a modal box
    # that /SUPPRESSMSGBOXES does not cover. InitializeSetup owns that path.
    assert "function InitializeSetup(): Boolean;" in script
    assert "WizardSilent()" in script
    assert "{param:DIR|}" in script

    # The budget is supplied by the build, never written here.
    assert "#ifndef MaxPayloadDepth" in script
    assert "#error MaxPayloadDepth must be defined by the build" in script


def test_windows_readme_is_deterministic_like_the_rest_of_the_archive(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Waveguide Generator"
    source.mkdir()
    (source / "Waveguide Generator.exe").write_bytes(b"launcher")
    extras = {WINDOWS_README_NAME: BundleBuilder(tmp_path).windows_readme()}

    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    for output in (first, second):
        deterministic_zip(source, output, archive_root=source.name, extra_root_files=extras)

    assert first.read_bytes() == second.read_bytes()


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

    # Two upload steps since the inventory is split across two releases, and no
    # more: one per release, each uploading a whole staging directory in a single
    # call, so neither release can be assembled a file at a time. Both pinned to
    # the same reviewed action.
    uploads = re.findall(r"softprops/action-gh-release@\S+", workflow)
    assert len(uploads) == 2, uploads
    assert len(set(uploads)) == 1, f"the two upload steps use different pins: {uploads}"
    # Exactly one of them is the user-facing release, and it is the drafted one.
    assert workflow.count("draft: true") == 1
    assert workflow.count("prerelease: true") == 1
    assert "needs: [spa, macos-bundle, windows-bundle, linux-bundle]" in workflow
    assert 'gh release edit "$RELEASE_TAG" --draft=false' in workflow
    assert "Reuse a runtime already published" not in workflow
    # The count is derived from the spec list rather than spelled out. Adding the
    # Windows installer made a hardcoded "seven" wrong, and a message that has to
    # be edited in step with the list is one that eventually is not.
    assert 'f"Validated {len(specs)} release assets and one SPA sidecar: "' in workflow
    assert "Waveguide.Generator-*-macos-arm64.dmg" in workflow
    assert "Waveguide.Generator-*-windows-x86_64.zip" in workflow
    assert "Waveguide.Generator-*-windows-x86_64-setup.exe" in workflow
    assert "release_assets.windows_setup_name(version)" in workflow
    assert "Waveguide.Generator-*-linux-x86_64.tar.gz" in workflow
    assert "update-runtime-linux-x86_64-*.zip" in workflow
    # Named as the distribution the build was verified against, not as
    # "ubuntu-latest": the runner is what fixes the glibc the runtime links to.
    assert "runs-on: ubuntu-24.04" in workflow


def test_release_notes_give_both_platforms_their_first_launch_wall() -> None:
    """The notes are the only instructions someone sees before downloading.

    macOS has carried its Gatekeeper paragraph since the first bundle; Windows
    shipped with only the path-length note, so the SmartScreen refusal - the
    same shape of dead end, and the one that stops a launch outright - reached
    users undocumented.
    """

    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    # The route that needs no Terminal comes first; the command stays as the
    # fallback for a machine where Privacy & Security offers nothing.
    assert BundleBuilder.DMG_INSTALLER_NAME in workflow
    assert "Open Anyway" in workflow
    assert "xattr -dr com.apple.quarantine" in workflow
    assert "Unblock" in workflow
    assert "Windows protected your PC" in workflow
    assert "C:\\wg" in workflow


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
    bootstrap = windows_desktop_bootstrap()
    assert 'os.environ["WG2_BUNDLE"] = "1"' in bootstrap
    assert 'os.environ["WG2_APP_ROOT"] = str(app_root)' in bootstrap
    assert "from launchers.desktop import main" in bootstrap
    assert "_report_startup_failure" in bootstrap
    # An update renames the app layer out from under the running process, and
    # Windows refuses to rename any process's current directory.
    assert "os.chdir(bundle_root)" in bootstrap
    assert "os.chdir(app_root)" not in bootstrap
    # PYTHONPYCACHEPREFIX is read at interpreter start-up, so setting it in the
    # environment here only ever reaches children.
    assert "sys.pycache_prefix = os.environ[" in bootstrap

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
        ("no-script", [], "desktop", [], 1, True),
        (
            "module",
            ["-m", "launchers.desktop", "--port", "3110"],
            "desktop",
            ["--port", "3110"],
            0,
            False,
        ),
        (
            "command",
            ["-c", "from probe_support import record; record('command')"],
            "command",
            ["-c"],
            0,
            False,
        ),
        (
            "script",
            [str(script), "worker-argument"],
            "script",
            [str(script), "worker-argument"],
            0,
            False,
        ),
    )
    for (
        label,
        arguments,
        expected_kind,
        expected_argv,
        expected_returncode,
        direct_launch,
    ) in invocations:
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
        if direct_launch:
            assert payload["bundle"] == "1"
            assert payload["appRoot"] == str(app)
            cache = tmp_path / "local-app-data" / "WaveguideGenerator" / "cache"
            assert payload["pycache"] == str(cache / "pycache")
            assert payload["numba"] == str(cache / "numba")
        else:
            assert payload["bundle"] is None
            assert payload["appRoot"] is None
            assert payload["pycache"] is None
            assert payload["numba"] is None


def test_user_site_is_switched_off_before_the_interpreter_starts() -> None:
    """Post-start sys.path pruning is not isolation, so the bundle must not rely on it.

    ``site.main()`` runs ``addusersitepackages()`` -- which *executes* every
    ``.pth`` file in the user site directory -- before it imports
    ``sitecustomize``.  Anything the bootstrap does has therefore already lost
    the race, which is why the switch lives in ``pyvenv.cfg`` instead:
    ``site.venv()`` reads it earlier and clears ``ENABLE_USER_SITE``.
    """

    config = windows_pyvenv_cfg()
    assert "include-system-site-packages = false" in config
    # A wrong "home" is fatal inside getpath, before any of our code can report
    # it, and the ._pth already locates the standard library.
    assert "home" not in config

    bootstrap = windows_desktop_bootstrap()
    assert "_drop_user_site" not in bootstrap
    assert "sys.path[:]" not in bootstrap


def test_the_runtime_interpreter_keeps_its_own_site_packages() -> None:
    """pyvenv.cfg moves PREFIXES, so the plain interpreter needs explicit paths.

    ``runtime\\python.exe`` has no path file of its own and finds
    ``site-packages`` through ``sys.prefix``.  The pyvenv.cfg beside the
    launcher rewrites that prefix for every interpreter under the bundle, so
    without this the plain one cannot import ``fastapi`` and both
    ``scripts/check_backends.py`` and the build's backend gate fail.
    """

    pth = windows_runtime_pth()
    assert pth.splitlines() == ["Lib", "DLLs", "Lib\\site-packages", "import site"]
    # The two layers are swapped independently; the runtime must not reach into
    # the app layer to resolve its own imports.
    assert "app" not in pth
    # site is still imported, because that is what applies the pyvenv.cfg switch.
    assert pth.endswith("import site\n")


def _bootstrap_launch_predicate() -> object:
    """Compile only the bootstrap's declarations, without running its body."""

    head, separator, _ = windows_desktop_bootstrap().partition("\nif _is_direct_launch():")
    assert separator, "the bootstrap no longer guards its body with _is_direct_launch"
    namespace: dict[str, object] = {}
    exec(compile(head, "wg_desktop_bootstrap", "exec"), namespace)  # noqa: S102
    return namespace["_is_direct_launch"]


@pytest.mark.parametrize(
    ("argv0", "executable", "expected"),
    [
        # A double-click: CPython leaves argv[0] empty because it was given no
        # script, no -c and no -m. This is the case the bundle exists for, and
        # the one a `-c` build probe can never reach.
        ("", r"C:\Apps\Waveguide Generator\Waveguide Generator.exe", True),
        ("", r"C:\Apps\Waveguide Generator\WAVEGUIDE GENERATOR.EXE", True),
        # Worker subprocesses reuse the launcher as sys.executable and must
        # stay inert, whichever form they take.
        ("-c", r"C:\Apps\Waveguide Generator\Waveguide Generator.exe", False),
        (
            r"C:\Apps\Waveguide Generator\app\launch\serve.py",
            r"C:\Apps\Waveguide Generator\Waveguide Generator.exe",
            False,
        ),
        # And a plain interpreter is never the launcher.
        ("", r"C:\Python313\python.exe", False),
    ],
)
def test_direct_launch_is_recognised_only_for_an_argument_free_launcher_start(
    monkeypatch: pytest.MonkeyPatch, argv0: str, executable: str, expected: bool
) -> None:
    predicate = _bootstrap_launch_predicate()
    monkeypatch.setattr(sys, "argv", [argv0])
    monkeypatch.setattr(sys, "executable", executable)
    assert predicate() is expected


def test_manifest_and_checksum_writers_always_emit_lf(tmp_path: Path) -> None:
    """CRLF here breaks the release workflow's cross-platform identity check."""

    manifest = tmp_path / "APP-MANIFEST.json"
    write_json(manifest, {"schemaVersion": 1, "version": "0.2.4"})
    assert b"\r\n" not in manifest.read_bytes()

    asset = tmp_path / "layer.zip"
    asset.write_bytes(b"payload")
    sidecar = write_checksum(asset)
    body = sidecar.read_bytes()
    assert b"\r\n" not in body
    assert body.endswith(b"  layer.zip\n")


def _visual_studio_crt(program_files: Path, toolset: str = "14.40") -> Path:
    return (
        program_files
        / "Microsoft Visual Studio"
        / "2022"
        / "BuildTools"
        / "VC"
        / "Redist"
        / "MSVC"
        / toolset
        / "x64"
        / "Microsoft.VC143.CRT"
    )


def _no_where(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 1, "", "")


def _where_in(directory: Path) -> object:
    def where(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        candidate = directory / command[-1]
        if command[0] != "where.exe" or not candidate.is_file():
            return subprocess.CompletedProcess(command, 1, "", "")
        return subprocess.CompletedProcess(command, 0, f"{candidate}\n", "")

    return where


def test_msvc_dll_discovery_prefers_a_complete_set_from_the_higher_priority_source(
    tmp_path: Path,
) -> None:
    """Only whole sets compete, and the most trustworthy complete set wins."""

    system_root = tmp_path / "Windows"
    program_files = tmp_path / "Program Files"
    system32 = system_root / "System32"
    visual_studio = _visual_studio_crt(program_files)
    on_path = tmp_path / "Eclipse Adoptium" / "jdk-25.0.2.10-hotspot" / "bin"
    for name in MSVC_RUNTIME_DLLS:
        write_pe_stub(system32 / name, version=(14, 44, 35211, 0))
        write_pe_stub(visual_studio / name, version=(14, 40, 33810, 0))
        write_pe_stub(on_path / name, version=(14, 29, 30139, 0))
    environ = {"SystemRoot": str(system_root), "ProgramFiles": str(program_files)}

    located = locate_msvc_runtime_dlls(runner=_where_in(on_path), environ=environ)

    # The machine's own serviced redistributable outranks both others.
    assert located == {name: (system32 / name).resolve() for name in MSVC_RUNTIME_DLLS}

    # Without it, an installed Visual Studio's x64 CRT still outranks whatever
    # unrelated application happens to sit first on PATH.
    shutil.rmtree(system32)
    located = locate_msvc_runtime_dlls(runner=_where_in(on_path), environ=environ)

    assert located == {name: (visual_studio / name).resolve() for name in MSVC_RUNTIME_DLLS}


def test_msvc_dll_discovery_skips_an_incomplete_directory_instead_of_mixing_sources(
    tmp_path: Path,
) -> None:
    """The redistributable is serviced as a set; a half-set is not a source."""

    system_root = tmp_path / "Windows"
    program_files = tmp_path / "Program Files"
    system32 = system_root / "System32"
    visual_studio = _visual_studio_crt(program_files)
    # System32 is missing msvcp140.dll. Historically the build topped that up
    # from a JDK on PATH and shipped a combination nobody has ever tested.
    for name in MSVC_RUNTIME_DLLS[:2]:
        write_pe_stub(system32 / name, version=(14, 44, 35211, 0))
    jdk = tmp_path / "Eclipse Adoptium" / "jdk-25.0.2.10-hotspot" / "bin"
    write_pe_stub(jdk / MSVC_RUNTIME_DLLS[2], version=(14, 40, 33810, 0))
    for name in MSVC_RUNTIME_DLLS:
        write_pe_stub(visual_studio / name, version=(14, 40, 33810, 0))

    located = locate_msvc_runtime_dlls(
        runner=_where_in(jdk),
        environ={"SystemRoot": str(system_root), "ProgramFiles": str(program_files)},
    )

    assert located == {name: (visual_studio / name).resolve() for name in MSVC_RUNTIME_DLLS}
    # Nothing was taken from the incomplete System32 set or from the JDK.
    assert {path.parent for path in located.values()} == {visual_studio.resolve()}


def test_msvc_dll_discovery_skips_a_complete_32_bit_set(tmp_path: Path) -> None:
    """A 32-bit DLL beside a 64-bit interpreter fails at load with no useful error."""

    system_root = tmp_path / "Windows"
    program_files = tmp_path / "Program Files"
    redist = program_files / "Microsoft Visual Studio" / "2022" / "VC" / "x64"
    for name in MSVC_RUNTIME_DLLS:
        write_pe_stub(system_root / "System32" / name, machine=0x014C)
        write_pe_stub(redist / name)

    located = locate_msvc_runtime_dlls(
        runner=_no_where,
        environ={"SystemRoot": str(system_root), "ProgramFiles": str(program_files)},
    )

    assert located == {name: (redist / name).resolve() for name in MSVC_RUNTIME_DLLS}


def test_msvc_dll_discovery_rejects_a_directory_whose_dlls_did_not_ship_together(
    tmp_path: Path,
) -> None:
    """A mixed directory is a build-time failure, not something to ship."""

    system_root = tmp_path / "Windows"
    system32 = system_root / "System32"
    for name in MSVC_RUNTIME_DLLS[:2]:
        write_pe_stub(system32 / name, version=(14, 44, 35211, 0))
    write_pe_stub(system32 / MSVC_RUNTIME_DLLS[2], version=(14, 40, 33810, 0))

    with pytest.raises(BundleError) as error:
        locate_msvc_runtime_dlls(
            runner=_no_where,
            environ={
                "SystemRoot": str(system_root),
                "ProgramFiles": str(tmp_path / "Program Files"),
            },
        )

    message = str(error.value)
    assert "mismatched versions" in message
    assert "14.44.35211.0" in message and "14.40.33810.0" in message
    assert str(system32) in message


def test_msvc_dll_discovery_fails_loudly_when_no_directory_holds_a_complete_set(
    tmp_path: Path,
) -> None:
    system_root = tmp_path / "Windows"
    program_files = tmp_path / "Program Files"
    system32 = system_root / "System32"
    visual_studio = _visual_studio_crt(program_files)
    on_path = tmp_path / "vendor" / "bin"
    # One file each: three sources that between them could be assembled into a
    # full set, and must not be.
    write_pe_stub(system32 / MSVC_RUNTIME_DLLS[0])
    write_pe_stub(visual_studio / MSVC_RUNTIME_DLLS[1])
    write_pe_stub(on_path / MSVC_RUNTIME_DLLS[2])

    with pytest.raises(BundleError) as error:
        locate_msvc_runtime_dlls(
            runner=_where_in(on_path),
            environ={"SystemRoot": str(system_root), "ProgramFiles": str(program_files)},
        )

    message = str(error.value)
    assert all(name in message for name in MSVC_RUNTIME_DLLS)
    for directory in (system32, visual_studio, on_path):
        assert str(directory) in message
    assert "Microsoft Visual C++ x64 Redistributable" in message


def test_msvc_dll_discovery_reports_the_searched_directories_when_nothing_exists(
    tmp_path: Path,
) -> None:
    with pytest.raises(BundleError, match=MSVC_RUNTIME_DLLS[0]):
        locate_msvc_runtime_dlls(
            runner=_no_where,
            environ={
                "SystemRoot": str(tmp_path / "Windows"),
                "ProgramFiles": str(tmp_path / "Program Files"),
            },
        )


def test_pe_version_probe_reads_the_fixed_file_info(tmp_path: Path) -> None:
    stamped = write_pe_stub(tmp_path / "stamped.dll", version=(14, 44, 35211, 0))

    assert pe_file_version(stamped) == (14, 44, 35211, 0)
    # A DLL without a version resource is unknown, never a mismatch.
    assert pe_file_version(write_pe_stub(tmp_path / "bare.dll")) is None
    assert pe_file_version(tmp_path / "missing.dll") is None


def test_pe_architecture_probe_reads_the_machine_field(tmp_path: Path) -> None:
    assert is_x64_pe(write_pe_stub(tmp_path / "x64.dll")) is True
    assert is_x64_pe(write_pe_stub(tmp_path / "x86.dll", machine=0x014C)) is False
    not_pe = tmp_path / "text.dll"
    not_pe.write_bytes(b"not an executable at all")
    assert is_x64_pe(not_pe) is False
    assert is_x64_pe(tmp_path / "missing.dll") is False


def test_windows_runtime_build_requests_the_explicit_host_target_and_copies_msvc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "server").mkdir(parents=True)
    (repo / "server" / "requirements-runtime.txt").write_bytes(b"runtime\n")
    (repo / "server" / "requirements-pins.txt").write_bytes(b"pins\n")
    (repo / "server" / "requirements-lock.txt").write_bytes(b"lock\n")
    # Point the two preferred sources at empty directories so this stays a test
    # of the builder rather than of whatever the host happens to have installed.
    monkeypatch.setenv("SystemRoot", str(tmp_path / "no-windows"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "no-program-files"))
    redist = tmp_path / "redist"
    redist.mkdir()
    for filename in MSVC_RUNTIME_DLLS:
        write_pe_stub(redist / filename)
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
        assert (destination / filename).read_bytes() == (redist / filename).read_bytes()
    assert (destination / WINDOWS_RUNTIME_PTH_NAME).read_text(
        encoding="utf-8"
    ) == windows_runtime_pth()
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
    assert (destination / WINDOWS_PYVENV_NAME).read_text(encoding="utf-8") == windows_pyvenv_cfg()
    assert (destination / WINDOWS_ICON_NAME).read_bytes() == b"ico"
    assert (destination / "runtime" / "Lib" / "os.py").is_file()
    assert (destination / "app" / "APP-MANIFEST.json").is_file()
    for filename in ("python313.dll", "python3.dll", *MSVC_RUNTIME_DLLS):
        assert (destination / filename).read_bytes() == (runtime / filename).read_bytes()


class _FakeLauncherProcess:
    """Stand in for the launcher process the bare-launch gate owns."""

    def __init__(self, *, returncode: int | None = None) -> None:
        self.pid = 4321
        self.returncode = returncode
        self.waits: list[float | None] = []
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _bare_launch_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    process: _FakeLauncherProcess,
    status: int | None,
) -> tuple[BundleBuilder, Path, Path, list[tuple[list[str], dict[str, object]]], list[list[str]]]:
    launcher = tmp_path / "Waveguide Generator" / WINDOWS_LAUNCHER_NAME
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_bytes(b"launcher")
    scratch = tmp_path / "verification"
    scratch.mkdir(exist_ok=True)
    starts: list[tuple[list[str], dict[str, object]]] = []
    commands: list[list[str]] = []

    def process_factory(command: list[str], **options: object) -> _FakeLauncherProcess:
        starts.append((list(command), options))
        return process

    def runner(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(BundleBuilder, "_free_port", staticmethod(lambda: 43110))
    monkeypatch.setattr(BundleBuilder, "_http_status", staticmethod(lambda _url: status))
    builder = BundleBuilder(tmp_path, runner=runner, process_factory=process_factory)
    return builder, launcher, scratch, starts, commands


def test_bare_windows_launcher_gate_starts_with_no_arguments_and_kills_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `-c` probe cannot reach the double-click path; only a bare start can."""

    process = _FakeLauncherProcess()
    builder, launcher, scratch, starts, commands = _bare_launch_harness(
        tmp_path, monkeypatch, process=process, status=200
    )

    builder.verify_windows_bare_launch(
        launcher,
        scratch=scratch,
        environment={
            "WG2_BUNDLE": "1",
            "WG2_APP_ROOT": str(tmp_path / "stale"),
            "PYTHONPYCACHEPREFIX": str(tmp_path / "caches"),
        },
    )

    command, options = starts[0]
    # Any argument, even one the app understands, gives CPython a non-empty
    # argv[0] and the bootstrap correctly declines to start the desktop.
    assert command == [str(launcher)]
    environment = options["env"]
    # The bootstrap has to establish these itself; presetting them would hide
    # the failure this gate exists to catch.
    assert "WG2_BUNDLE" not in environment
    assert "WG2_APP_ROOT" not in environment
    # The cache redirection stays, so a verified bundle is never written into.
    assert environment["PYTHONPYCACHEPREFIX"] == str(tmp_path / "caches")
    assert Path(environment[DATA_DIR_ENV]).is_relative_to(scratch)
    assert 1 <= int(environment[PORT_ENV]) <= 65535
    assert options["cwd"] == launcher.parent
    flags = int(options["creationflags"])
    assert flags & WINDOWS_CREATE_NO_WINDOW
    assert flags & WINDOWS_CREATE_NEW_PROCESS_GROUP
    # The launcher spawns the server as a child, so only a tree kill collects it.
    assert commands == [["taskkill", "/PID", str(process.pid), "/T", "/F"]]
    assert process.waits and all(timeout is not None for timeout in process.waits)


def test_bare_windows_launcher_gate_fails_when_a_bare_start_never_serves_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeLauncherProcess()
    builder, launcher, scratch, _starts, commands = _bare_launch_harness(
        tmp_path, monkeypatch, process=process, status=None
    )

    with pytest.raises(BundleError) as error:
        builder.verify_windows_bare_launch(
            launcher,
            scratch=scratch,
            environment={},
            timeout=0.0,
        )

    message = str(error.value)
    assert "/health=None" in message
    assert "/=None" in message
    assert "REPL" in message
    # The tree is killed even when the gate fails.
    assert commands == [["taskkill", "/PID", str(process.pid), "/T", "/F"]]


def test_bare_windows_launcher_gate_names_an_immediate_launcher_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeLauncherProcess(returncode=0)
    builder, launcher, scratch, _starts, commands = _bare_launch_harness(
        tmp_path, monkeypatch, process=process, status=None
    )

    with pytest.raises(BundleError, match="exited with 0"):
        builder.verify_windows_bare_launch(launcher, scratch=scratch, environment={})

    # Nothing is left running, so nothing is killed.
    assert commands == []


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


def test_tree_digest_covers_content_not_packing(tmp_path: Path) -> None:
    """The digest must see what matters and ignore what does not."""

    def _layer(root: Path) -> Path:
        (root / "server").mkdir(parents=True)
        (root / "server" / "app.py").write_text("print('x')\n", encoding="utf-8")
        (root / "LICENSE").write_text("licence\n", encoding="utf-8")
        return root

    first = _layer(tmp_path / "a")
    second = _layer(tmp_path / "b")
    assert build_bundle.tree_digest(first) == build_bundle.tree_digest(second)

    # Content, paths and the executable bit are all part of the identity.
    (second / "server" / "app.py").write_text("print('y')\n", encoding="utf-8")
    assert build_bundle.tree_digest(first) != build_bundle.tree_digest(second)

    (second / "server" / "app.py").write_text("print('x')\n", encoding="utf-8")
    assert build_bundle.tree_digest(first) == build_bundle.tree_digest(second)

    # The executable bit is part of the identity, and comes from Git.
    executable = frozenset({"server/app.py"})
    assert build_bundle.tree_digest(first) != build_bundle.tree_digest(
        first, executables=executable
    )
    assert build_bundle.tree_digest(first, executables=executable) == (
        build_bundle.tree_digest(second, executables=executable)
    )

    # And it does NOT come from the filesystem, which is the whole cross-platform
    # fix. NTFS has no POSIX executable bit and CPython fabricates one from the
    # file extension, so a digest that read the mode back disagreed between hosts
    # on byte-identical content -- three .bat files failed the 0.2.5 release
    # build that way. Changing the mode on disk must not move the digest.
    if os.name != "nt":
        before = build_bundle.tree_digest(second)
        (second / "server" / "app.py").chmod(0o755)
        assert build_bundle.tree_digest(second) == before
        (second / "server" / "app.py").chmod(0o644)

    (second / "extra.txt").write_text("", encoding="utf-8")
    assert build_bundle.tree_digest(first) != build_bundle.tree_digest(second)


def test_tree_digest_can_exclude_the_manifest_that_carries_it(tmp_path: Path) -> None:
    root = tmp_path / "app"
    root.mkdir()
    (root / "LICENSE").write_text("licence\n", encoding="utf-8")
    before = build_bundle.tree_digest(root, exclude=frozenset({"APP-MANIFEST.json"}))

    # Writing the manifest must not change the digest the manifest reports, or
    # no two builds could ever agree.
    (root / "APP-MANIFEST.json").write_text('{"treeSha256": "..."}\n', encoding="utf-8")
    after = build_bundle.tree_digest(root, exclude=frozenset({"APP-MANIFEST.json"}))
    assert before == after
    assert build_bundle.tree_digest(root) != after


def test_the_app_manifest_records_the_tree_digest(tmp_path: Path) -> None:
    root = tmp_path / "app"
    root.mkdir()
    (root / "LICENSE").write_text("licence\n", encoding="utf-8")

    payload = build_bundle.write_app_manifest(
        root, version="0.2.4", commit="0" * 40, runtime_id="abcdef012345"
    )

    written = json.loads((root / "APP-MANIFEST.json").read_text(encoding="utf-8"))
    assert payload["treeSha256"] == written["treeSha256"]
    assert written["treeSha256"] == build_bundle.tree_digest(
        root, exclude=frozenset({"APP-MANIFEST.json"})
    )


def test_the_disk_image_carries_first_launch_instructions(tmp_path: Path) -> None:
    """The instruction has to be where the wall is.

    macOS refuses this app on first launch and offers no way to proceed: it is
    ad-hoc signed, so Gatekeeper has no developer identity to attach an exception
    to, and Privacy & Security lists nothing to allow. A user who hits that dialog
    has already left the release page, so the release notes are the wrong and only
    place for the fix.
    """

    builder = BundleBuilder(
        Path(__file__).resolve().parents[2],
        system=lambda: "Darwin",
        machine=lambda: "arm64",
    )
    readme = builder.dmg_readme()

    # The installer script is the route that does not need Terminal, so it comes
    # first and is named exactly as the file in the disk image.
    assert builder.DMG_INSTALLER_NAME == "Install Waveguide Generator.command"
    assert f'Double-click "{builder.DMG_INSTALLER_NAME}"' in readme
    assert "Privacy & Security" in readme
    assert "Open Anyway" in readme
    # The Terminal command stays as the fallback, and must be exact and
    # copy-pasteable; a wrong path is worse than none.
    assert 'xattr -dr com.apple.quarantine "/Applications/Waveguide Generator.app"' in readme
    # Say what they will actually see, including that the app itself is not
    # listed in Privacy & Security however long they look for it.
    assert "Not Opened" in readme
    assert "will NOT list the app" in readme
    assert "Move to Bin" in readme
    # And that it is once, not every launch.
    assert "once, not on every launch" in readme

    commands: list[list[str]] = []

    def runner(command, **kwargs):
        commands.append(list(command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    builder.runner = runner
    bundle = tmp_path / "Waveguide Generator.app"
    (bundle / "Contents").mkdir(parents=True)
    (bundle / "Contents" / "Info.plist").write_text("<plist/>", encoding="utf-8")
    staging = tmp_path / "staging"

    builder.create_dmg(bundle, tmp_path / "out.dmg", staging)

    shipped = staging / builder.DMG_README_NAME
    assert shipped.is_file(), "the disk image must carry the readme beside the app"
    assert shipped.read_text(encoding="utf-8") == readme
    # Still a normal drag-to-Applications image.
    assert (staging / "Applications").is_symlink()
    assert (staging / bundle.name).is_dir()


def test_the_disk_image_carries_an_executable_installer_script(tmp_path: Path) -> None:
    """The one file in the image macOS will let the user approve.

    Measured 2026-09-02 on macOS 26.5.2 against a genuinely quarantined download:
    the ad-hoc signed .app assesses as `rejected` with no `source` line at all, so
    Privacy & Security lists nothing for it, while an unsigned script assesses as
    `rejected  source=no usable signature` -- and that `source` is what the "Open
    Anyway" exception attaches to. Shipping the app unsigned instead is not
    available: an unsigned arm64 executable is SIGKILLed whatever its quarantine
    state. See docs/validation/2026-09/MACOS-GATEKEEPER.md.

    The executable bit is the failure mode worth a test of its own. A .command
    without it opens in TextEdit instead of running, which looks like nothing
    happening at all, and Windows checkouts fabricate that bit -- the same class
    of bug that already reached a release once.
    """

    repo_root = Path(__file__).resolve().parents[2]
    builder = BundleBuilder(repo_root, system=lambda: "Darwin", machine=lambda: "arm64")
    builder.runner = lambda command, **kwargs: SimpleNamespace(
        returncode=0, stdout="", stderr=""
    )
    bundle = tmp_path / "Waveguide Generator.app"
    (bundle / "Contents").mkdir(parents=True)
    (bundle / "Contents" / "Info.plist").write_text("<plist/>", encoding="utf-8")
    staging = tmp_path / "staging"

    builder.create_dmg(bundle, tmp_path / "out.dmg", staging)

    installer = staging / builder.DMG_INSTALLER_NAME
    assert installer.is_file(), "the disk image must carry the installer beside the app"
    if os.name == "posix":
        # NTFS has no POSIX execute bit and Path.chmod cannot set one, so this
        # says nothing on a Windows runner. It is not a gap: create_dmg shells
        # out to hdiutil, so the disk image is only ever built on macOS.
        assert installer.stat().st_mode & 0o111, "a non-executable .command opens in an editor"
    source = repo_root / builder.DMG_INSTALLER_SOURCE
    assert installer.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")

    script = installer.read_text(encoding="utf-8")
    # The two steps the user would otherwise open Terminal for.
    assert 'ditto "$SOURCE" "$TARGET"' in script
    assert 'xattr -dr com.apple.quarantine "$TARGET"' in script
    # It runs from a read-only mounted volume with nothing else from the checkout
    # beside it, so it may not reach back into the repository for anything.
    assert "scripts/" not in script
    assert "REPO_DIR" not in script


def test_a_missing_installer_script_fails_the_build(tmp_path: Path) -> None:
    """Never build a disk image whose installer is silently absent.

    The whole point of the file is that it is the only thing in the image a user
    can approve. An image without it looks fine and hands every macOS user back
    the Terminal command.
    """

    builder = BundleBuilder(tmp_path, system=lambda: "Darwin", machine=lambda: "arm64")
    with pytest.raises(BundleError, match="disk-image installer is missing"):
        builder.dmg_installer()


#: `bash` on a Windows runner resolves to the WSL launcher, which has no
#: distribution installed and answers every invocation with instructions for
#: installing one. So these are POSIX-only: the script itself only ever runs on
#: macOS, and Linux CI exercises it identically.
_NEEDS_POSIX_BASH = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="needs a POSIX bash; Windows resolves bash to the WSL stub",
)


@_NEEDS_POSIX_BASH
def test_the_installer_script_is_valid_bash() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / BundleBuilder.DMG_INSTALLER_SOURCE
    result = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="ditto, xattr and codesign are macOS")
def test_the_installer_script_installs_and_clears_the_quarantine(tmp_path: Path) -> None:
    """The half of the flow that does not need a human: what happens once it runs.

    Built against a real ad-hoc signed bundle carrying a real com.apple.quarantine
    attribute, because an un-quarantined artifact would pass this test without
    proving anything -- the mistake the whole Gatekeeper item keeps repeating.
    """

    repo_root = Path(__file__).resolve().parents[2]
    image = tmp_path / "image"
    image.mkdir()
    app = image / "Waveguide Generator.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir(parents=True)
    source_c = tmp_path / "main.c"
    source_c.write_text("int main(void){return 0;}\n", encoding="utf-8")
    compiled = subprocess.run(
        ["cc", "-arch", "arm64", "-o", str(app / "Contents" / "MacOS" / "app"), str(source_c)],
        capture_output=True,
        check=False,
    )
    if compiled.returncode != 0:
        pytest.skip("no working arm64 compiler")
    plistlib.dump(
        {"CFBundleExecutable": "app", "CFBundleIdentifier": "is.hornlab.test"},
        (app / "Contents" / "Info.plist").open("wb"),
    )
    (app / "Contents" / "Resources" / "payload.txt").write_text("x", encoding="utf-8")
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(
        ["xattr", "-w", "-r", "com.apple.quarantine", "0081;0;Safari;X", str(app)],
        check=True,
    )
    before = subprocess.run(
        ["xattr", "-p", "com.apple.quarantine", str(app / "Contents" / "MacOS" / "app")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert before.returncode == 0, "the fixture must actually be quarantined"

    installer = image / BundleBuilder.DMG_INSTALLER_NAME
    shutil.copy2(repo_root / BundleBuilder.DMG_INSTALLER_SOURCE, installer)
    installer.chmod(0o755)
    applications = tmp_path / "Applications"
    applications.mkdir()

    result = subprocess.run(
        ["bash", str(installer), str(applications)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    installed = applications / "Waveguide Generator.app"
    assert installed.is_dir()
    remaining = subprocess.run(
        ["xattr", "-p", "com.apple.quarantine", str(installed / "Contents" / "MacOS" / "app")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert remaining.returncode != 0, (
        "the installed copy still carries com.apple.quarantine; "
        f"got {remaining.stdout.strip()!r}"
    )
    # ditto must preserve the ad-hoc signature; an app that copies but no longer
    # verifies is SIGKILLed on launch with no explanation.
    verified = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(installed)],
        capture_output=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr

    # Installing again over the top replaces rather than failing or nesting.
    again = subprocess.run(
        ["bash", str(installer), str(applications)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert sorted(p.name for p in applications.iterdir()) == ["Waveguide Generator.app"]


@_NEEDS_POSIX_BASH
def test_the_installer_script_refuses_to_run_away_from_the_app(tmp_path: Path) -> None:
    """Copied out of the disk image on its own, it must say so, not half-install.

    And it must still hand back the Terminal command, because that is the only
    thing left that works when the user has already moved the file.
    """

    repo_root = Path(__file__).resolve().parents[2]
    installer = tmp_path / BundleBuilder.DMG_INSTALLER_NAME
    shutil.copy2(repo_root / BundleBuilder.DMG_INSTALLER_SOURCE, installer)
    installer.chmod(0o755)

    result = subprocess.run(
        ["bash", str(installer), str(tmp_path / "nowhere")],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode == 1
    assert "is not beside this installer" in result.stdout
    assert 'xattr -dr com.apple.quarantine "/Applications/Waveguide Generator.app"' in result.stdout


# --------------------------------------------------------------------------
# Linux: the third bundle, added at 0.3.2.
# --------------------------------------------------------------------------


def _linux_payload(root: Path) -> Path:
    """A bundle-shaped folder: enough for the scripts, none of the 100 MB."""

    bundle = root / LINUX_BUNDLE_DIRECTORY
    (bundle / "app").mkdir(parents=True)
    (bundle / "runtime" / "bin").mkdir(parents=True)
    (bundle / "app" / "APP-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "version": "1.2.3"}), encoding="utf-8"
    )
    (bundle / "runtime" / "RUNTIME-MANIFEST.json").write_text("{}", encoding="utf-8")
    launcher = bundle / LINUX_LAUNCHER_NAME
    launcher.write_text(linux_launcher(), encoding="utf-8")
    launcher.chmod(0o755)
    (bundle / LINUX_DESKTOP_ENTRY_NAME).write_text(linux_desktop_entry(), encoding="utf-8")
    (bundle / LINUX_ICON_NAME).write_bytes(b"\x89PNG\r\n\x1a\n")
    return bundle


def _install_linux(
    tmp_path: Path,
    *,
    arguments: list[str],
    home: Path | None = None,
    environment: dict[str, str] | None = None,
) -> "subprocess.CompletedProcess[str]":
    """Run the shipped installer against a throwaway HOME.

    ``--skip-checks`` because the payload above has no interpreter to import
    gmsh with; the preflight has its own test.
    """

    home = home or (tmp_path / "home")
    home.mkdir(exist_ok=True)
    scripts = {
        LINUX_INSTALLER_NAME: LINUX_INSTALLER_SOURCE,
        LINUX_UNINSTALLER_NAME: LINUX_UNINSTALLER_SOURCE,
    }
    for name, source in scripts.items():
        target = tmp_path / name
        if not target.exists():
            shutil.copy2(Path(__file__).resolve().parents[2] / source, target)
            target.chmod(0o755)
    run_environment = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
    }
    run_environment.update(environment or {})
    return subprocess.run(
        ["bash", str(tmp_path / LINUX_INSTALLER_NAME), *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=run_environment,
    )


def _desktop_exec_argv(entry: str) -> list[str]:
    """Decode the two escaping layers the desktop-entry Exec key applies."""

    encoded = next(line.removeprefix("Exec=") for line in entry.splitlines() if line.startswith("Exec="))
    decoded: list[str] = []
    index = 0
    string_escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    while index < len(encoded):
        if encoded[index] == "\\" and index + 1 < len(encoded):
            following = encoded[index + 1]
            if following in string_escapes:
                decoded.append(string_escapes[following])
                index += 2
                continue
        decoded.append(encoded[index])
        index += 1
    command = "".join(decoded)
    assert command.startswith('"')
    argument: list[str] = []
    index = 1
    while index < len(command):
        character = command[index]
        if character == '"':
            break
        if character == "\\" and index + 1 < len(command):
            following = command[index + 1]
            if following in {'"', "`", "$", "\\"}:
                argument.append(following)
                index += 2
                continue
        argument.append(character)
        index += 1
    assert index < len(command), "unterminated quoted Exec argument"
    fields = command[index + 1 :].split()
    return ["".join(argument).replace("%%", "%"), *fields]


#: The executable bit is a POSIX file mode, and Windows has no such thing:
#: ``chmod`` there is a no-op that reports success, so a file this builder marks
#: executable still stats as 0o666 and arrives in the tar as 0o644. That is the
#: host's property, not the artifact's -- the Linux bundle is built on Linux,
#: where these same assertions are exact. Asserting the bit on Windows tests the
#: Windows filesystem rather than the builder, which is why it is skipped rather
#: than weakened: the check stays strict everywhere it means anything.
_NEEDS_POSIX_MODES = pytest.mark.skipif(
    os.name != "posix",
    reason="executable bits are POSIX; Windows chmod is a no-op",
)


def test_the_linux_bundle_is_the_windows_shape_not_the_macos_one(tmp_path: Path) -> None:
    """One root holding ``app``, ``runtime`` and the launcher, and nothing else.

    That is the layout ``apply_update.bundle_from_app_layer`` already resolves
    for every non-macOS platform, so the in-app updater's swap, rollback and
    relaunch need no third rule. A nested macOS-style container here would have
    been invisible until the first update tried to rename a directory that was
    one level from where it looked.
    """

    builder = BundleBuilder(Path(__file__).resolve().parents[2], system=lambda: "Linux")
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python3.13").write_text("#!/bin/sh\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    (app / "APP-MANIFEST.json").write_text("{}", encoding="utf-8")
    written: list[tuple[Path, int]] = []
    bundle = tmp_path / LINUX_BUNDLE_DIRECTORY

    builder.assemble_linux_bundle(
        bundle,
        runtime_root=runtime,
        app_root=app,
        icon_writer=lambda path, size: (
            path.write_bytes(b"\x89PNG"),
            written.append((path, size)),
        )[0],
    )

    assert sorted(entry.name for entry in bundle.iterdir()) == sorted(
        ["app", "runtime", LINUX_LAUNCHER_NAME, LINUX_DESKTOP_ENTRY_NAME, LINUX_ICON_NAME]
    )
    assert (bundle / "app" / "APP-MANIFEST.json").is_file()
    assert written == [(bundle / LINUX_ICON_NAME, 512)]
    # The bit that decides whether a double-click runs the application or opens
    # it in a text editor, and the one a checkout cannot be trusted to carry.
    # Everything above this line is shape and holds on every host; only the mode
    # needs POSIX, so the rest of the test still runs on Windows.
    if os.name == "posix":
        assert (bundle / LINUX_LAUNCHER_NAME).stat().st_mode & 0o111


def test_the_linux_launcher_establishes_the_bundle_environment() -> None:
    """What the .desktop entry runs has to do what launcher.c does on macOS."""

    script = linux_launcher()

    assert script.startswith("#!/bin/sh\n")
    # Resolved from the file, not from $0: the installer puts a symlink on PATH.
    assert "readlink -f" in script
    assert "WG2_BUNDLE=1" in script
    assert "WG2_APP_ROOT=$app" in script
    assert "export WG2_BUNDLE WG2_APP_ROOT" in script
    # Caches out of the installation, so a layer swap is not racing a
    # __pycache__ directory being written into the directory it is renaming.
    assert "PYTHONPYCACHEPREFIX" in script
    assert "NUMBA_CACHE_DIR" in script
    assert 'exec "$python" -m launchers.desktop "$@"' in script
    # It may not reach into a checkout: it runs from an installed copy.
    assert "REPO_DIR" not in script
    assert ".venv" not in script


def test_the_linux_desktop_entry_is_substituted_not_guessed() -> None:
    entry = linux_desktop_entry()

    assert entry.startswith("[Desktop Entry]\n")
    assert "Type=Application" in entry
    assert f"Exec=@INSTALL_DIR@/{LINUX_LAUNCHER_NAME} %U" in entry
    # The icon is named by theme key, not by path, so the hicolor lookup the
    # installer feeds is the one the desktop performs.
    assert "Icon=waveguide-generator\n" in entry
    assert "Terminal=false" in entry


@_NEEDS_POSIX_MODES
def test_the_linux_tarball_carries_an_executable_installer(tmp_path: Path) -> None:
    """A .tar.gz because tar keeps the executable bit and .zip does not.

    A user who has to ``chmod +x install.sh`` before anything happens is the
    Linux form of a .command that opens in TextEdit, and this project has met
    that failure once already.
    """

    builder = BundleBuilder(Path(__file__).resolve().parents[2], system=lambda: "Linux")
    bundle = _linux_payload(tmp_path)
    archive = tmp_path / "out.tar.gz"

    builder.create_linux_tarball(bundle, archive)

    with tarfile.open(archive, "r:gz") as opened:
        members = {member.name: member for member in opened.getmembers()}
    for name in (LINUX_INSTALLER_NAME, LINUX_UNINSTALLER_NAME):
        assert members[name].mode & 0o111, f"{name} must arrive executable"
    assert members[LINUX_README_NAME].mode & 0o111 == 0
    assert members[f"{LINUX_BUNDLE_DIRECTORY}/{LINUX_LAUNCHER_NAME}"].mode & 0o111
    # The instructions and the scripts sit BESIDE the folder, so they are
    # reachable without entering it -- the same placement as the Windows
    # readme, and for the same reason.
    assert LINUX_INSTALLER_NAME in members
    assert f"{LINUX_BUNDLE_DIRECTORY}/app/APP-MANIFEST.json" in members
    assert members[LINUX_INSTALLER_NAME].uid == 0
    assert members[LINUX_INSTALLER_NAME].uname == ""


def test_the_linux_tarball_is_reproducible(tmp_path: Path) -> None:
    """Two builds of one commit are the same bytes, header included."""

    builder = BundleBuilder(Path(__file__).resolve().parents[2], system=lambda: "Linux")
    bundle = _linux_payload(tmp_path)
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"

    builder.create_linux_tarball(bundle, first)
    builder.create_linux_tarball(bundle, second)

    assert first.read_bytes() == second.read_bytes()


def test_a_tarball_symlink_out_of_the_bundle_fails_the_build(tmp_path: Path) -> None:
    """The same refusal the .zip makes, so neither archive can carry a link out."""

    bundle = _linux_payload(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (bundle / "escape").symlink_to(outside)

    with pytest.raises(BundleError, match="does not resolve to a file inside"):
        deterministic_tar_gz(
            bundle, tmp_path / "out.tar.gz", archive_root=LINUX_BUNDLE_DIRECTORY
        )


def test_the_linux_runtime_keeps_tcl_and_tk(tmp_path: Path) -> None:
    """On Linux the status window IS the application, so Tk cannot be pruned.

    ``launchers.desktop.main`` falls back to the tkinter status window on
    Linux because there is no native window there. Pruning Tk as the macOS and
    Windows builds do would have shipped a bundle whose only user interface
    cannot start -- and it would have looked like a size win.
    """

    runtime = tmp_path / "runtime"
    for relative in ("lib/tk9.0", "lib/tcl9.0", "lib/python3.13/tkinter"):
        (runtime / relative).mkdir(parents=True)
    for relative in ("lib/python3.13/idlelib", "lib/python3.13/ensurepip"):
        (runtime / relative).mkdir(parents=True)
    (runtime / "lib" / "libtcl9tk9.0.so").write_bytes(b"")

    removed = prune_runtime(runtime, platform_name=LINUX_PLATFORM)

    assert (runtime / "lib" / "python3.13" / "tkinter").is_dir()
    assert (runtime / "lib" / "tk9.0").is_dir()
    assert (runtime / "lib" / "libtcl9tk9.0.so").is_file()
    # Everything else the macOS build drops still goes.
    assert "lib/python3.13/idlelib" in removed
    assert "lib/python3.13/ensurepip" in removed
    assert "lib/python3.13/tkinter" not in LINUX_PRUNE_RELATIVE_PATHS


def test_the_linux_bundle_refuses_to_build_off_linux(tmp_path: Path) -> None:
    """uv can unpack the interpreter anywhere; the wheels on top of it cannot.

    A cross-built bundle would carry the right interpreter and extensions
    compiled for the wrong operating system, which fails at import rather than
    at build time -- in a user's hands.
    """

    builder = BundleBuilder(tmp_path, system=lambda: "Darwin", machine=lambda: "arm64")
    args = SimpleNamespace(
        platform="linux",
        python_version="3.13.12",
        output=tmp_path / "output",
        app_only=False,
        runtime_only=False,
        spa=None,
        skip_verify=False,
    )

    with pytest.raises(BundleError, match="must be built on Linux"):
        build(args, builder=builder)


def test_a_missing_linux_installer_script_fails_the_build(tmp_path: Path) -> None:
    """Never build a tarball whose installer is silently absent.

    A tarball with only the folder in it hands every Linux user a directory and
    no instructions, which looks like a successful build.
    """

    builder = BundleBuilder(tmp_path, system=lambda: "Linux")
    with pytest.raises(BundleError, match="bundle installer is missing"):
        builder.linux_installer()
    with pytest.raises(BundleError, match="bundle uninstaller is missing"):
        builder.linux_uninstaller()


@_NEEDS_POSIX_BASH
@pytest.mark.parametrize("source", (LINUX_INSTALLER_SOURCE, LINUX_UNINSTALLER_SOURCE))
def test_the_linux_scripts_are_valid_bash(source: str) -> None:
    script = Path(__file__).resolve().parents[2] / source
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@_NEEDS_POSIX_BASH
def test_the_linux_installer_places_the_application_menu_entry_and_command(
    tmp_path: Path,
) -> None:
    """Everything install.sh promises, checked where it puts it.

    Run against a throwaway ``HOME``, so this exercises the real script rather
    than a description of it. It is not native qualification: the payload is a
    fixture, not a built bundle.
    """

    _linux_payload(tmp_path)
    home = tmp_path / "home"

    result = _install_linux(tmp_path, arguments=["--no-launch", "--skip-checks"], home=home)

    assert result.returncode == 0, result.stdout + result.stderr
    share = home / ".local" / "share"
    installed = share / LINUX_BUNDLE_DIRECTORY
    assert (installed / "app" / "APP-MANIFEST.json").is_file()
    assert (installed / LINUX_LAUNCHER_NAME).stat().st_mode & 0o111
    # The uninstaller travels into the installation, so removing it does not
    # require still having the download.
    assert (installed / LINUX_UNINSTALLER_NAME).stat().st_mode & 0o111

    entry = (share / "applications" / LINUX_DESKTOP_ENTRY_NAME).read_text(encoding="utf-8")
    assert "@INSTALL_DIR@" not in entry
    assert _desktop_exec_argv(entry) == [str(installed / LINUX_LAUNCHER_NAME), "%U"]
    assert (share / "icons" / "hicolor" / "512x512" / "apps" / LINUX_ICON_NAME).is_file()

    command = home / ".local" / "bin" / LINUX_LAUNCHER_NAME
    assert command.is_symlink()
    assert command.resolve() == (installed / LINUX_LAUNCHER_NAME).resolve()


@_NEEDS_POSIX_BASH
def test_linux_desktop_exec_quotes_and_invokes_a_special_character_path(tmp_path: Path) -> None:
    """Exec has two escape layers and percent introduces freedesktop field codes."""

    bundle = _linux_payload(tmp_path)
    launch_record = tmp_path / "launched.txt"
    launcher = bundle / LINUX_LAUNCHER_NAME
    launcher.write_text(
        "#!/bin/sh\nprintf '%s' \"$0\" > \"$WG_TEST_LAUNCH_RECORD\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    home = tmp_path / "home"
    prefix = tmp_path / "space & pipe| slash\\"

    result = _install_linux(
        tmp_path,
        arguments=["--prefix", str(prefix), "--no-launch", "--skip-checks"],
        home=home,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    entry_path = home / ".local" / "share" / "applications" / LINUX_DESKTOP_ENTRY_NAME
    entry = entry_path.read_text(encoding="utf-8")
    executable = prefix / LINUX_BUNDLE_DIRECTORY / LINUX_LAUNCHER_NAME
    argv = _desktop_exec_argv(entry)
    assert argv == [str(executable), "%U"]
    validation_tool = shutil.which("desktop-file-validate")
    if validation_tool:
        validated = subprocess.run(
            [validation_tool, str(entry_path)], capture_output=True, text=True, check=False
        )
        assert validated.returncode == 0, validated.stdout + validated.stderr
    invoked = subprocess.run(
        [argv[0]],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "WG_TEST_LAUNCH_RECORD": str(launch_record)},
    )
    assert invoked.returncode == 0, invoked.stdout + invoked.stderr
    assert launch_record.read_text(encoding="utf-8") == str(executable)


@_NEEDS_POSIX_BASH
def test_the_linux_installer_canonicalizes_prefix_before_writing_paths(tmp_path: Path) -> None:
    _linux_payload(tmp_path)
    home = tmp_path / "home"
    prefix_with_parent = tmp_path / "missing" / ".." / "canonical"

    result = _install_linux(
        tmp_path,
        arguments=["--prefix", str(prefix_with_parent), "--no-launch", "--skip-checks"],
        home=home,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    installed = tmp_path / "canonical" / LINUX_BUNDLE_DIRECTORY
    assert installed.is_dir()
    entry = (
        home / ".local" / "share" / "applications" / LINUX_DESKTOP_ENTRY_NAME
    ).read_text(encoding="utf-8")
    assert _desktop_exec_argv(entry)[0] == str(installed / LINUX_LAUNCHER_NAME)
    assert ".." not in os.readlink(home / ".local" / "bin" / LINUX_LAUNCHER_NAME)


@_NEEDS_POSIX_BASH
def test_linux_prefix_canonicalization_resolves_symlinks_before_parent_segments(
    tmp_path: Path,
) -> None:
    _linux_payload(tmp_path)
    home = tmp_path / "home"
    linked_parent = tmp_path / "linked-parent"
    real_parent = tmp_path / "real-parent" / "nested"
    real_parent.mkdir(parents=True)
    linked_parent.mkdir()
    (linked_parent / "link").symlink_to(real_parent, target_is_directory=True)
    prefix = linked_parent / "link" / ".." / "canonical"

    result = _install_linux(
        tmp_path,
        arguments=["--prefix", str(prefix), "--no-launch", "--skip-checks"],
        home=home,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    target = tmp_path / "real-parent" / "canonical" / LINUX_BUNDLE_DIRECTORY
    assert target.is_dir()
    entry = (
        home / ".local" / "share" / "applications" / LINUX_DESKTOP_ENTRY_NAME
    ).read_text(encoding="utf-8")
    assert _desktop_exec_argv(entry)[0] == str(target / LINUX_LAUNCHER_NAME)


@_NEEDS_POSIX_BASH
def test_the_linux_installer_rejects_a_percent_path_before_mutation(tmp_path: Path) -> None:
    """Ubuntu GLib rejects spec ``%%`` while the validator rejects raw ``%``."""

    _linux_payload(tmp_path)
    home = tmp_path / "home"
    prefix = tmp_path / "percent%"

    result = _install_linux(
        tmp_path,
        arguments=["--prefix", str(prefix), "--no-launch", "--skip-checks"],
        home=home,
    )

    assert result.returncode == 1
    assert "cannot contain a percent sign" in result.stdout
    assert not prefix.exists()
    assert not (home / ".local").exists()


@_NEEDS_POSIX_BASH
@pytest.mark.parametrize(
    ("arguments", "environment", "message"),
    [
        (["--prefix", "relative"], {}, "--prefix must be an absolute path"),
        ([], {"XDG_DATA_HOME": "relative"}, "XDG_DATA_HOME must be an absolute path"),
    ],
)
def test_the_linux_installer_rejects_relative_paths_before_mutation(
    tmp_path: Path,
    arguments: list[str],
    environment: dict[str, str],
    message: str,
) -> None:
    _linux_payload(tmp_path)
    home = tmp_path / "home"

    result = _install_linux(
        tmp_path,
        arguments=[*arguments, "--no-launch", "--skip-checks"],
        home=home,
        environment=environment,
    )

    assert result.returncode == 1
    assert message in result.stdout
    assert not (home / ".local").exists()


@_NEEDS_POSIX_BASH
@pytest.mark.parametrize("inside_source", [False, True])
def test_the_linux_installer_refuses_source_target_overlap(
    tmp_path: Path, inside_source: bool
) -> None:
    source = _linux_payload(tmp_path)
    home = tmp_path / "home"
    prefix = source / "nested" if inside_source else tmp_path

    result = _install_linux(
        tmp_path,
        arguments=["--prefix", str(prefix), "--no-launch", "--skip-checks"],
        home=home,
    )

    assert result.returncode == 1
    expected = "inside the extracted application" if inside_source else "are the same"
    assert expected in result.stdout
    assert (source / "app" / "APP-MANIFEST.json").is_file()


@_NEEDS_POSIX_BASH
def test_reinstalling_replaces_rather_than_nesting_or_failing(tmp_path: Path) -> None:
    """Upgrade in place is the common path, and it must not accumulate."""

    _linux_payload(tmp_path)
    home = tmp_path / "home"

    first = _install_linux(tmp_path, arguments=["--no-launch", "--skip-checks"], home=home)
    assert first.returncode == 0, first.stdout + first.stderr
    marker = home / ".local" / "share" / LINUX_BUNDLE_DIRECTORY / "app" / "left-over.txt"
    marker.write_text("from the previous version", encoding="utf-8")

    again = _install_linux(tmp_path, arguments=["--no-launch", "--skip-checks"], home=home)

    assert again.returncode == 0, again.stdout + again.stderr
    share = home / ".local" / "share"
    assert sorted(p.name for p in share.iterdir()) == sorted(
        [LINUX_BUNDLE_DIRECTORY, "applications", "icons"]
    )
    # A replacement, not a merge: a file the old version left behind is gone.
    assert not marker.exists()


@_NEEDS_POSIX_BASH
def test_the_linux_installer_refuses_to_run_away_from_the_application(
    tmp_path: Path,
) -> None:
    """Copied out of the tarball on its own, it must say so, not half-install."""

    result = _install_linux(tmp_path, arguments=["--no-launch", "--skip-checks"])

    assert result.returncode == 1
    assert "is not beside this installer" in result.stdout


@_NEEDS_POSIX_BASH
def test_the_linux_installer_refuses_to_replace_a_directory_it_did_not_make(
    tmp_path: Path,
) -> None:
    """``rm -rf`` on a path from ``--prefix`` is how a home directory is lost.

    The manifest the builder writes is the evidence required before anything is
    displaced, so only a directory this project produced can be replaced.
    """

    _linux_payload(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    occupied = tmp_path / "elsewhere" / LINUX_BUNDLE_DIRECTORY
    occupied.mkdir(parents=True)
    (occupied / "someone-elses-work.txt").write_text("do not delete", encoding="utf-8")

    result = _install_linux(
        tmp_path,
        arguments=["--prefix", str(tmp_path / "elsewhere"), "--no-launch", "--skip-checks"],
        home=home,
    )

    assert result.returncode == 1
    assert "not a Waveguide Generator installation" in result.stdout
    assert (occupied / "someone-elses-work.txt").is_file()


@_NEEDS_POSIX_BASH
def test_the_linux_installer_refuses_root(tmp_path: Path) -> None:
    """A root-owned copy would install once and never update again.

    The in-app updater replaces ``app`` and ``runtime`` in place as the user and
    cannot elevate, which is the same reason the Windows installer writes to
    %LOCALAPPDATA% rather than Program Files.
    """

    script = (
        Path(__file__).resolve().parents[2] / LINUX_INSTALLER_SOURCE
    ).read_text(encoding="utf-8")

    assert 'if [ "$(id -u)" = "0" ]; then' in script
    assert "Do not install Waveguide Generator as root." in script


@_NEEDS_POSIX_BASH
def test_the_linux_uninstaller_removes_exactly_what_the_installer_added(
    tmp_path: Path,
) -> None:
    """And leaves the workspace, which is the reason people run it."""

    _linux_payload(tmp_path)
    home = tmp_path / "home"
    installed = _install_linux(tmp_path, arguments=["--no-launch", "--skip-checks"], home=home)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    share = home / ".local" / "share"
    workspace = share / "WaveguideGenerator"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "design.mwg").write_text("a design", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(share / LINUX_BUNDLE_DIRECTORY / LINUX_UNINSTALLER_NAME)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env={
            "HOME": str(home),
            "PATH": os.environ.get("PATH", ""),
            "XDG_DATA_HOME": str(share),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (share / LINUX_BUNDLE_DIRECTORY).exists()
    assert not (share / "applications" / LINUX_DESKTOP_ENTRY_NAME).exists()
    assert not (share / "icons" / "hicolor" / "512x512" / "apps" / LINUX_ICON_NAME).exists()
    assert not (home / ".local" / "bin" / LINUX_LAUNCHER_NAME).exists()
    # The whole point of the default: reinstalling is the common reason to run
    # this, and a workspace lost to it is unrecoverable.
    assert (workspace / "design.mwg").read_text(encoding="utf-8") == "a design"
    assert "--data" in result.stdout


@_NEEDS_POSIX_BASH
def test_the_linux_uninstaller_refuses_a_directory_it_did_not_make(tmp_path: Path) -> None:
    """The mirror of the installer's guard, on the operation that deletes."""

    home = tmp_path / "home"
    home.mkdir()
    share = home / ".local" / "share"
    stranger = share / LINUX_BUNDLE_DIRECTORY
    stranger.mkdir(parents=True)
    (stranger / "not-ours.txt").write_text("keep", encoding="utf-8")
    script = tmp_path / LINUX_UNINSTALLER_NAME
    shutil.copy2(Path(__file__).resolve().parents[2] / LINUX_UNINSTALLER_SOURCE, script)

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env={"HOME": str(home), "PATH": os.environ.get("PATH", ""), "XDG_DATA_HOME": str(share)},
    )

    assert result.returncode == 1
    assert "not a Waveguide Generator installation" in result.stdout
    assert (stranger / "not-ours.txt").is_file()


@_NEEDS_POSIX_BASH
def test_the_linux_uninstaller_leaves_another_installations_command_alone(
    tmp_path: Path,
) -> None:
    """The PATH symlink is shared ground; only its own may be removed."""

    _linux_payload(tmp_path)
    home = tmp_path / "home"
    installed = _install_linux(tmp_path, arguments=["--no-launch", "--skip-checks"], home=home)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    share = home / ".local" / "share"
    other = tmp_path / "other" / LINUX_LAUNCHER_NAME
    other.parent.mkdir(parents=True)
    other.write_text("#!/bin/sh\n", encoding="utf-8")
    command = home / ".local" / "bin" / LINUX_LAUNCHER_NAME
    command.unlink()
    command.symlink_to(other)

    result = subprocess.run(
        ["bash", str(share / LINUX_BUNDLE_DIRECTORY / LINUX_UNINSTALLER_NAME)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env={"HOME": str(home), "PATH": os.environ.get("PATH", ""), "XDG_DATA_HOME": str(share)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert command.is_symlink(), "a link into another installation was taken with this one"
    assert not (share / LINUX_BUNDLE_DIRECTORY).exists()


@_NEEDS_POSIX_BASH
def test_uninstalling_a_preserves_installation_bs_shared_desktop_assets(tmp_path: Path) -> None:
    """Desktop entry, icon, and PATH command all belong to the latest install."""

    _linux_payload(tmp_path)
    home = tmp_path / "home"
    prefix_a = tmp_path / "install-a"
    prefix_b = tmp_path / "install-b"
    for prefix in (prefix_a, prefix_b):
        result = _install_linux(
            tmp_path,
            arguments=["--prefix", str(prefix), "--no-launch", "--skip-checks"],
            home=home,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    target_a = prefix_a / LINUX_BUNDLE_DIRECTORY
    target_b = prefix_b / LINUX_BUNDLE_DIRECTORY
    share = home / ".local" / "share"
    result = subprocess.run(
        ["bash", str(target_a / LINUX_UNINSTALLER_NAME)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env={"HOME": str(home), "PATH": os.environ.get("PATH", ""), "XDG_DATA_HOME": str(share)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not target_a.exists()
    assert target_b.is_dir()
    desktop = share / "applications" / LINUX_DESKTOP_ENTRY_NAME
    icon = share / "icons" / "hicolor" / "512x512" / "apps" / LINUX_ICON_NAME
    assert _desktop_exec_argv(desktop.read_text(encoding="utf-8"))[0] == str(
        target_b / LINUX_LAUNCHER_NAME
    )
    assert icon.is_file()
    assert (desktop.parent / ".waveguide-generator.owner").read_text().strip() == str(target_b)
    assert (icon.parent / ".waveguide-generator.owner").read_text().strip() == str(target_b)
    assert (home / ".local" / "bin" / LINUX_LAUNCHER_NAME).resolve() == (
        target_b / LINUX_LAUNCHER_NAME
    ).resolve()
    assert "belongs to another installation" in result.stdout


@_NEEDS_POSIX_BASH
def test_a_commit_failure_restores_the_previous_linux_installation(tmp_path: Path) -> None:
    """A failure after target displacement restores every prior artefact."""

    source = _linux_payload(tmp_path)
    home = tmp_path / "home"
    first = _install_linux(tmp_path, arguments=["--no-launch", "--skip-checks"], home=home)
    assert first.returncode == 0, first.stdout + first.stderr
    share = home / ".local" / "share"
    target = share / LINUX_BUNDLE_DIRECTORY
    desktop = share / "applications" / LINUX_DESKTOP_ENTRY_NAME
    icon = share / "icons" / "hicolor" / "512x512" / "apps" / LINUX_ICON_NAME
    command = home / ".local" / "bin" / LINUX_LAUNCHER_NAME
    before = {
        "manifest": (target / "app" / "APP-MANIFEST.json").read_bytes(),
        "desktop": desktop.read_bytes(),
        "icon": icon.read_bytes(),
        "link": os.readlink(command),
    }
    (source / "app" / "APP-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "version": "9.9.9"}), encoding="utf-8"
    )
    (source / LINUX_ICON_NAME).write_bytes(b"new icon")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    failure_marker = tmp_path / "mv-failed-once"
    real_mv = shutil.which("mv")
    assert real_mv
    mv = fake_bin / "mv"
    mv.write_text(
        "#!/bin/bash\n"
        "if [ \"${1:-}\" = -- ]; then shift; fi\n"
        f"case \"${{1:-}}\" in */.waveguide-generator.*.desktop) "
        f"if [ \"${{2:-}}\" = \"{desktop}\" ] && [ ! -e \"{failure_marker}\" ]; then "
        f"touch \"{failure_marker}\"; echo injected desktop move failure >&2; exit 23; fi;; esac\n"
        f'exec "{real_mv}" "$@"\n',
        encoding="utf-8",
    )
    mv.chmod(0o755)

    again = _install_linux(
        tmp_path,
        arguments=["--no-launch", "--skip-checks"],
        home=home,
        environment={"PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"},
    )

    assert again.returncode == 1
    assert "Could not install the rendered desktop entry" in again.stdout
    assert "Restored the previous installation and desktop integration" in again.stdout
    assert (target / "app" / "APP-MANIFEST.json").read_bytes() == before["manifest"]
    assert desktop.read_bytes() == before["desktop"]
    assert icon.read_bytes() == before["icon"]
    assert os.readlink(command) == before["link"]
    assert not list(share.glob(".waveguide-generator.install.*"))
    assert not list(share.glob(".waveguide-generator.previous.*"))


@_NEEDS_POSIX_BASH
@pytest.mark.parametrize("failure_mode", ["restore", "remove"])
def test_a_failed_rollback_preserves_and_reports_the_backup_path(tmp_path: Path, failure_mode: str) -> None:
    _linux_payload(tmp_path)
    home = tmp_path / "home"
    first = _install_linux(tmp_path, arguments=["--no-launch", "--skip-checks"], home=home)
    assert first.returncode == 0, first.stdout + first.stderr
    share = home / ".local" / "share"
    target = share / LINUX_BUNDLE_DIRECTORY
    desktop = share / "applications" / LINUX_DESKTOP_ENTRY_NAME

    fake_bin = tmp_path / "fake-bin-restore"
    fake_bin.mkdir()
    real_mv = shutil.which("mv")
    assert real_mv
    mv = fake_bin / "mv"
    mv.write_text(
        "#!/bin/bash\n"
        "if [ \"${1:-}\" = -- ]; then shift; fi\n"
        f"case \"${{1:-}}\" in\n"
        f"  */.waveguide-generator.*.desktop) "
        f"if [ \"${{2:-}}\" = \"{desktop}\" ]; then exit 23; fi;;\n"
        f"  */.waveguide-generator.previous.*) "
        f"if [ \"${{2:-}}\" = \"{target}\" ]; then exit 24; fi;;\n"
        "esac\n"
        f'exec "{real_mv}" "$@"\n',
        encoding="utf-8",
    )
    mv.chmod(0o755)
    if failure_mode == "remove":
        # If removing the replacement fails, mv must not nest the backup
        # inside it and then falsely report that recovery succeeded.
        mv.write_text(mv.read_text().replace("then exit 24;", "then :;"))
        real_rm = shutil.which("rm")
        assert real_rm
        rm = fake_bin / "rm"
        rm.write_text(
            "#!/bin/bash\n"
            f'if [ "${{@: -1}}" = "{target}" ]; then exit 25; fi\n'
            f'exec "{real_rm}" "$@"\n', encoding="utf-8",
        )
        rm.chmod(0o755)

    again = _install_linux(
        tmp_path,
        arguments=["--no-launch", "--skip-checks"],
        home=home,
        environment={"PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"},
    )

    assert again.returncode == 1
    assert "rollback was incomplete" in again.stderr
    assert "Its backup remains at:" in again.stderr
    assert "Restored the previous installation" not in again.stdout
    backups = list(share.glob(".waveguide-generator.previous.*"))
    assert len(backups) == 1
    assert (backups[0] / "app" / "APP-MANIFEST.json").is_file()


@_NEEDS_POSIX_BASH
def test_staging_failure_leaves_the_previous_linux_installation_untouched(tmp_path: Path) -> None:
    source = _linux_payload(tmp_path)
    home = tmp_path / "home"
    first = _install_linux(tmp_path, arguments=["--no-launch", "--skip-checks"], home=home)
    assert first.returncode == 0, first.stdout + first.stderr
    target = home / ".local" / "share" / LINUX_BUNDLE_DIRECTORY
    manifest = target / "app" / "APP-MANIFEST.json"
    before = manifest.read_bytes()
    (source / LINUX_DESKTOP_ENTRY_NAME).unlink()

    again = _install_linux(tmp_path, arguments=["--no-launch", "--skip-checks"], home=home)

    assert again.returncode == 1
    assert "is incomplete" in again.stdout
    assert manifest.read_bytes() == before
    assert not list(target.parent.glob(".waveguide-generator.install.*"))


@_NEEDS_POSIX_BASH
def test_the_linux_installer_stops_before_installing_a_mesher_that_cannot_load(
    tmp_path: Path,
) -> None:
    """The system libraries the bundle does not bring, checked by importing gmsh.

    Measured 2026-09-04 on a bare ubuntu:24.04 with the pinned runtime:
    ``import gmsh`` raises ``OSError: libGLU.so.1: cannot open shared object
    file``, and then names the next missing library each time one is installed.
    gmsh is the single geometry authority here, so an install without them
    opens the interface and meshes nothing -- worth refusing before anything is
    copied, with the command that fixes it.

    The probe is the real import rather than a package-name lookup, so the
    message carries the library the loader actually failed on.
    """

    bundle = _linux_payload(tmp_path)
    interpreter = bundle / "runtime" / "bin" / "python3.13"
    interpreter.write_text(
        "#!/bin/sh\n"
        "echo 'OSError: libGLU.so.1: cannot open shared object file' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    interpreter.chmod(0o755)

    result = _install_linux(tmp_path, arguments=["--no-launch"])

    assert result.returncode == 1
    assert "libGLU.so.1" in result.stdout
    assert "sudo apt install libglu1-mesa libgl1 libgomp1" in result.stdout
    assert "Nothing has been installed yet." in result.stdout
    assert not (tmp_path / "home" / ".local" / "share" / LINUX_BUNDLE_DIRECTORY).exists()


def test_the_rc_build_offers_every_platform_the_release_page_does() -> None:
    """The hand-test gate has to cover what a user will actually download.

    rc-build.yml exists so Magnus tries the installers before a version is
    spent (hornlab-policy/PLAN.md step 5). A platform that ships on the release
    page and has no RC artifact is one nobody can try first, and that gap is
    invisible -- the workflow still goes green.
    """

    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "rc-build.yml"
    ).read_text(encoding="utf-8")

    for job in ("macos-bundle:", "windows-bundle:", "linux-bundle:"):
        assert job in workflow
    # One upload glob per user-facing download, spelled as the builder names it.
    for pattern in (
        "Waveguide.Generator-*-macos-arm64.dmg",
        "Waveguide.Generator-*-windows-x86_64-setup.exe",
        "Waveguide.Generator-*-linux-x86_64.tar.gz",
    ):
        assert pattern in workflow, f"the RC build does not publish {pattern}"
    # Every platform is one entry in one place; if that list grows, this fails.
    assert len(release_assets.user_download_names("1.2.3")) == 3
    # Same runner pin as the release job: an RC built against a different glibc
    # would be a hand-test of something that is not going to ship.
    assert "runs-on: ubuntu-24.04" in workflow


@pytest.mark.parametrize("health_failure", [False, True])
def test_windows_server_verification_retires_provisioning_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, health_failure: bool
) -> None:
    """The direct server probe can spawn Julia before the bare-launch gate runs."""
    bundle = tmp_path / "bundle"
    (bundle / "app").mkdir(parents=True)
    scratch = tmp_path / "verification"
    scratch.mkdir()
    process = _FakeLauncherProcess()
    child_alive = True

    def runner(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        nonlocal child_alive
        if "-c" in command:
            (scratch / "windows-launcher-probe.txt").write_text("ready")
        if command[0] == "taskkill":
            assert command == ["taskkill", "/PID", str(process.pid), "/T", "/F"]
            child_alive = False
            process.returncode = 0
        return subprocess.CompletedProcess(command, 0, "bempp (cross-platform): ready", "")

    def http_status(_url: str) -> int:
        if health_failure:
            raise BundleError("probe failure")
        return 200

    monkeypatch.setattr(BundleBuilder, "_free_port", staticmethod(lambda: 43110))
    monkeypatch.setattr(BundleBuilder, "_http_status", staticmethod(http_status))
    # This separate gate already kills its tree. It must not conceal a leak
    # from the earlier launch/serve.py probe, which has a different parent PID.
    monkeypatch.setattr(BundleBuilder, "verify_windows_bare_launch", lambda *_a, **_k: None)
    builder = BundleBuilder(
        tmp_path, runner=runner, process_factory=lambda *_a, **_k: process
    )
    if health_failure:
        with pytest.raises(BundleError, match="probe failure"):
            builder.verify_bundle(bundle, scratch, platform_name=WINDOWS_PLATFORM)
    else:
        builder.verify_bundle(bundle, scratch, platform_name=WINDOWS_PLATFORM)
    assert not child_alive, "parent-only termination leaves the provisioning child alive"
    assert process.waits, "cleanup must reap the verification parent"

"""Pure contracts for the macOS and Windows standalone bundle builder."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
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
from scripts.build_bundle import (
    BundleBuilder,
    BundleError,
    MSVC_RUNTIME_DLLS,
    PYTHON_BUILD,
    PRUNE_LIBRARY_GLOBS,
    PRUNE_RELATIVE_PATHS,
    RUNTIME_RECIPE,
    WINDOWS_CREATE_NEW_PROCESS_GROUP,
    WINDOWS_CREATE_NO_WINDOW,
    WINDOWS_ICON_NAME,
    WINDOWS_LAUNCHER_NAME,
    WINDOWS_PLATFORM,
    WINDOWS_PTH_NAME,
    WINDOWS_PRUNE_RELATIVE_PATHS,
    WINDOWS_PYVENV_NAME,
    WINDOWS_README_NAME,
    WINDOWS_RUNTIME_PTH_NAME,
    build,
    copy_tracked_app_files,
    deterministic_zip,
    install_spa_layer,
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

    assert workflow.count("softprops/action-gh-release@") == 1
    assert "needs: [spa, macos-bundle, windows-bundle]" in workflow
    assert "draft: true" in workflow
    assert 'gh release edit "$RELEASE_TAG" --draft=false' in workflow
    assert "Reuse a runtime already published" not in workflow
    assert "Validated seven release asset pairs." in workflow
    assert "Waveguide.Generator-*-macos-arm64.dmg" in workflow
    assert "Waveguide.Generator-*-windows-x86_64.zip" in workflow


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

    # The command must be exact and copy-pasteable; a wrong path is worse than none.
    assert 'xattr -dr com.apple.quarantine "/Applications/Waveguide Generator.app"' in readme
    # Say what they will actually see, including that the button is absent.
    assert "Not Opened" in readme
    assert "will NOT show an \"Open Anyway\" button" in readme
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

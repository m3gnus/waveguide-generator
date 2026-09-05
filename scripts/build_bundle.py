#!/usr/bin/env python3
"""Build relocatable Waveguide Generator release layers and desktop bundles."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping, Sequence
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, NamedTuple
import urllib.error
import urllib.request
import zipfile


_IMPORT_ROOT = (
    Path(os.environ.get("WG2_APP_ROOT") or Path(__file__).resolve().parents[1])
    .expanduser()
    .resolve()
)
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from scripts import fetch_spa  # noqa: E402
from launchers.macos import generate_icon  # noqa: E402
from server.platform.instance import PORT_ENV  # noqa: E402
from server.platform.paths import DATA_DIR_ENV, app_root  # noqa: E402
from shared.runtime_id import compute_runtime_id  # noqa: E402
from shared import release_assets  # noqa: E402
from shared.safe_names import UnsafeName, collision_key, validate_relative_name  # noqa: E402


REPO_ROOT = app_root()
# ``uv python list`` resolved the 3.13 request to this python-build-standalone
# patch release when the bundle implementation was written. Release builds use
# the full string so a later uv catalog update cannot silently change a layer.
PYTHON_VERSION = "3.13.12"
PYTHON_SERIES = "3.13"
# python-build-standalone records this release identifier in the root BUILD
# file. uv's version catalog may otherwise move a Python patch request to a
# newer standalone build without changing PYTHON_VERSION.
PYTHON_BUILD = "20260325"
RUNTIME_RECIPE = "wg2-bundle-runtime-v2"
MACOS_PLATFORM = "macos-arm64"
WINDOWS_PLATFORM = "windows-x86_64"
LINUX_PLATFORM = "linux-x86_64"
WINDOWS_TARGET = "x86_64-pc-windows-msvc"
LINUX_TARGET = "x86_64-unknown-linux-gnu"
APP_TEMPLATE = Path("launchers/macos/Waveguide Generator.app")
APP_SOURCE_DIRECTORIES = (
    "server",
    "launch",
    "launchers",
    "shared",
    "scripts",
    "integrations/wglink",
    "docs",
)
APP_SOURCE_FILES = ("LICENSE", "README.md")
APP_EXCLUDED_DIRECTORIES = frozenset({"test", "tests", "__pycache__"})
PRUNE_RELATIVE_PATHS = (
    "lib/python3.13/idlelib",
    "lib/python3.13/tkinter",
    "lib/python3.13/turtledemo",
    "lib/python3.13/ensurepip",
    "lib/python3.13/site-packages/pip",
)
PRUNE_LIBRARY_GLOBS = (
    "lib/tcl*",
    "lib/tk*",
    "lib/itcl*",
    "lib/python3.13/site-packages/pip-*.dist-info",
)
# Linux keeps Tcl/Tk, because on Linux the status window IS the desktop
# application: there is no native window there, so `launchers.desktop` falls
# back to the tkinter status window (see its `main`). Pruning Tk would have
# shipped a bundle whose only user interface cannot start.
#
# It costs nothing but size, which is the part that had to be measured rather
# than assumed. In a bare `ubuntu:24.04` container with no X11 package
# installed at all, the pinned python-build-standalone runtime imports
# `tkinter` (Tk 9.0) and reaches `couldn't connect to display ":99"` -- a
# Tcl-level error, so Tk_Init ran and its X11 client code is statically linked
# rather than resolved from the system. Measured 2026-09-04; the Tcl/Tk trees
# and the two shared objects are about 8 MB of a 102 MB runtime.
LINUX_PRUNE_RELATIVE_PATHS = (
    "lib/python3.13/idlelib",
    "lib/python3.13/turtledemo",
    "lib/python3.13/ensurepip",
    "lib/python3.13/site-packages/pip",
)
LINUX_PRUNE_LIBRARY_GLOBS = ("lib/python3.13/site-packages/pip-*.dist-info",)
WINDOWS_PRUNE_RELATIVE_PATHS = (
    "Lib/idlelib",
    "Lib/tkinter",
    "Lib/turtledemo",
    "Lib/ensurepip",
    "Lib/site-packages/pip",
    "Scripts",
)
WINDOWS_PRUNE_LIBRARY_GLOBS = (
    "tcl*",
    "tk*",
    "itcl*",
    "Lib/tcl*",
    "Lib/tk*",
    "Lib/itcl*",
    "DLLs/_tkinter.pyd",
    "DLLs/tcl*.dll",
    "DLLs/tk*.dll",
    "Lib/site-packages/pip-*.dist-info",
)
# The interpreter entries in bin/. Every other file there is a console-script
# wrapper whose shebang names the temporary build prefix; the bundle runs
# modules through python3.13 directly, so the wrappers would only ship a dead
# absolute path.
RUNTIME_BIN_KEEP = frozenset({"python", "python3", f"python{PYTHON_SERIES}"})
# The bundle is sealed by codesign, so nothing may be written into it after
# the build. Python would otherwise create __pycache__ beside every module it
# imports and numba would cache compiled kernels beside their source.
CACHE_ENVIRONMENT = ("PYTHONPYCACHEPREFIX", "NUMBA_CACHE_DIR")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
METAL_HELPER = Path(
    "lib/python3.13/site-packages/hornlab_metal_bem/metal/native_helper/"
    ".build/release/HornlabMetalBemNative"
)
MSVC_RUNTIME_DLLS = (
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
)
WINDOWS_LAUNCHER_NAME = "Waveguide Generator.exe"
WINDOWS_PTH_NAME = "Waveguide Generator._pth"
WINDOWS_PYVENV_NAME = "pyvenv.cfg"
WINDOWS_RUNTIME_PTH_NAME = "python._pth"
WINDOWS_ICON_NAME = "WaveguideGenerator.ico"
WINDOWS_README_NAME = "READ ME FIRST.txt"
WINDOWS_SETUP_SCRIPT = PurePosixPath("installers/windows/bundle-setup.iss")
#: The Linux bundle's entry point, at the root of the application folder --
#: the same position ``Waveguide Generator.exe`` holds on Windows, so
#: ``apply_update.bundle_from_app_layer`` resolves both with one rule.
LINUX_LAUNCHER_NAME = "waveguide-generator"
LINUX_ICON_NAME = "waveguide-generator.png"
#: Freedesktop icons are looked up by theme size, and 512 is the largest size
#: the hicolor theme defines for applications; a launcher that only ships 512
#: is downscaled by the shell rather than left blank.
LINUX_ICON_SIZE = 512
#: Shipped as a template with ``@INSTALL_DIR@`` in it rather than generated by
#: the installer, so the desktop entry is a reviewable file in the repository
#: and not a heredoc inside a shell script.
LINUX_DESKTOP_ENTRY_NAME = "waveguide-generator.desktop"
LINUX_INSTALLER_NAME = "install.sh"
LINUX_UNINSTALLER_NAME = "uninstall.sh"
LINUX_INSTALLER_SOURCE = "installers/linux/bundle-install.sh"
LINUX_UNINSTALLER_SOURCE = "installers/linux/bundle-uninstall.sh"
LINUX_README_NAME = "READ ME FIRST.txt"
#: The folder inside the tarball, and the name of the installed directory. Not
#: "Waveguide Generator": a path with a space in it is a lifetime of quoting
#: bugs in every shell script, .desktop Exec line and systemd unit that ever
#: touches it, and Linux has no Finder to make the pretty name worth that.
LINUX_BUNDLE_DIRECTORY = "waveguide-generator"
#: A bare launch has to start the interpreter, import the app layer and serve;
#: bounded for the same reason the Windows one is.
LINUX_BARE_LAUNCH_TIMEOUT = 180.0
#: Windows resolves most path operations against MAX_PATH of 260 *including* the
#: terminating null, so the longest path a build may produce is 259 characters.
WINDOWS_MAX_PATH = 259
#: ISCC is not on the GitHub windows runner image and not on PATH after a
#: default install, so the known per-user and per-machine locations are tried
#: before giving up. WG2_ISCC overrides all of it.
INNO_COMPILER_ENV = "WG2_ISCC"
INNO_COMPILER_CANDIDATES = (
    "%LOCALAPPDATA%/Programs/Inno Setup 6/ISCC.exe",
    "%ProgramFiles(x86)%/Inno Setup 6/ISCC.exe",
    "%ProgramFiles%/Inno Setup 6/ISCC.exe",
)
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS_CREATE_NO_WINDOW = 0x08000000
# A bare launch has to start the interpreter, import the app layer and let the
# desktop controller start its own server child, so it is allowed more than the
# plain server verification above -- but it is still hard-bounded, because a
# launcher that hangs must fail the build rather than the CI job's own clock.
WINDOWS_BARE_LAUNCH_TIMEOUT = 180.0
PROCESS_TREE_KILL_TIMEOUT = 30.0
PROCESS_EXIT_TIMEOUT = 15.0

RunCallable = Callable[..., subprocess.CompletedProcess[Any]]
ProcessFactory = Callable[..., subprocess.Popen[str]]


class BundleError(RuntimeError):
    """A release bundle could not be built or verified safely."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, object]) -> None:
    # newline="\n" is load-bearing: without it Windows text mode writes CRLF,
    # the manifests inside the app layer differ from the macOS ones byte for
    # byte, and the release workflow's cross-platform identity check fails.
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def windows_launcher_files(python_series: str = PYTHON_SERIES) -> tuple[tuple[str, str], ...]:
    """Name the runtime files that must sit beside the renamed ``pythonw.exe``.

    Windows has no bundle format, so the launcher and the interpreter DLLs it
    loads live in the application folder rather than inside the swappable
    ``runtime`` directory. One definition therefore serves two owners: the
    builder copies these files out of a fresh runtime, and the updater refreshes
    them from a newly installed runtime, which is what keeps a launcher from
    outliving the interpreter it was taken from.
    """

    tag = python_series.replace(".", "")
    return (
        ("pythonw.exe", WINDOWS_LAUNCHER_NAME),
        (f"python{tag}.dll", f"python{tag}.dll"),
        ("python3.dll", "python3.dll"),
        *((name, name) for name in MSVC_RUNTIME_DLLS),
    )


def write_runtime_manifest(
    runtime_root: Path,
    *,
    python_version: str,
    runtime_id: str,
    requirements: bytes,
    pins: bytes,
    lock: bytes,
    python_build: str,
    runtime_recipe: str,
    platform_name: str = MACOS_PLATFORM,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "python": python_version,
        "pythonBuild": python_build,
        "pythonDistribution": (
            f"cpython-{python_version}+{python_build}-python-build-standalone"
        ),
        "platform": platform_name,
        "requirementsSha256": sha256_bytes(requirements),
        "pinsSha256": sha256_bytes(pins),
        "lockSha256": sha256_bytes(lock),
        "runtimeRecipe": runtime_recipe,
        "runtimeId": runtime_id,
    }
    if platform_name == WINDOWS_PLATFORM:
        # The runtime describes its own launcher files so that an update can
        # refresh them without the updater carrying a second, driftable copy of
        # this list: a runtime built for a later Python names its own DLLs.
        payload["launcherFiles"] = [
            {
                "source": source,
                "destination": destination,
                "sha256": file_sha256(runtime_root / source),
            }
            for source, destination in windows_launcher_files(
                python_version.rsplit(".", 1)[0] if python_version.count(".") > 1 else PYTHON_SERIES
            )
        ]
    write_json(runtime_root / "RUNTIME-MANIFEST.json", payload)
    return payload


def tree_digest(
    root: Path,
    *,
    exclude: frozenset[str] = frozenset(),
    executables: frozenset[str] = frozenset(),
) -> str:
    """Digest a directory's content, independent of how it is packed.

    The two platforms must ship the same application layer, and comparing the
    archives byte for byte is a poor way to check that: it forces the canonical
    archive to be stored uncompressed, which cost ten times the download on
    every update, and it would still be defeated by any difference in the
    compressor. This digest covers what actually has to match -- the set of
    paths, symlink targets, and the bytes of each file -- so the archive is free
    to be compressed.

    It covers the executable bit, but takes it from ``executables`` -- the set of
    POSIX relative paths Git records as ``100755`` -- and never from the
    filesystem. NTFS has no POSIX executable bit and CPython fabricates one from
    the file extension, so ``.bat``, ``.cmd``, ``.exe`` and ``.com`` read back
    with ``S_IEXEC`` set on Windows and as plain ``0o644`` everywhere else.
    Reading the bit back made this digest depend on which host built the layer --
    the precise thing it exists to rule out -- and three ``.bat`` files were
    enough to fail the 0.2.5 release build on byte-identical content.

    Keying on Git's mode restores a real check and agrees across platforms by
    construction. Files that were never Git blobs -- the SPA and the Windows
    bootstrap -- are simply absent from ``executables``, which is accurate rather
    than a special case.

    The runtime layer is unaffected: it is described by RUNTIME-MANIFEST.json
    with a per-file digest of its own, so the real executables it ships keep
    their coverage.
    """

    digest = hashlib.sha256()
    # Sorted by the POSIX relative path, never by Path objects. Comparing
    # PurePath instances uses the platform's casing rule, and on Windows that is
    # case-insensitive -- so a layer holding LICENSE and README.md beside
    # lowercase directories enumerates in a different order there than on macOS,
    # and an order-dependent digest then differs on byte-identical content. It
    # cost the 0.2.5 and 0.2.6 release builds, and it is invisible to a per-file
    # comparison: the two layers have the same 308 paths with the same bytes,
    # diverging at the very first entry (macOS "LICENSE", Windows "docs/...").
    # scripts/fetch_spa.py's own tree_digest already keys on as_posix() for this
    # reason.
    entries = sorted(
        (path for path in root.rglob("*") if path.name not in exclude and not path.is_dir()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in entries:
        relative = PurePosixPath(path.relative_to(root).as_posix())
        # Symlinks are recorded by target rather than followed: a layer that
        # replaced a file with a link to it is not the same layer.
        if path.is_symlink():
            digest.update(f"l\x00{relative}\x00{os.readlink(path)}\x00".encode())
            continue
        digest.update(
            f"f{'x' if relative.as_posix() in executables else '-'}\x00{relative}\x00"
            f"{file_sha256(path)}\x00".encode()
        )
    return digest.hexdigest()


def assert_app_layer_modes_match_git(app_root: Path, executables: frozenset[str]) -> None:
    """Fail the build when the layer on disk disagrees with Git about modes.

    :func:`tree_digest` takes the executable bit from Git rather than from the
    filesystem, which is what makes the two build hosts agree. That leaves one
    way for the digest to describe something the layer is not: the materializer
    failing to apply a mode it reported. Both directions are checked, because a
    missing bit and a surplus one are equally a divergence between what shipped
    and what was digested.

    Windows cannot represent the bit, so there is nothing to compare there. The
    release builds its reference layer on macOS, which can.
    """

    if os.name == "nt":
        return
    on_disk = {
        path.relative_to(app_root).as_posix()
        for path in app_root.rglob("*")
        if not path.is_dir() and not path.is_symlink() and path.stat().st_mode & 0o111
    }
    if on_disk == executables:
        return
    missing = sorted(executables - on_disk)
    surplus = sorted(on_disk - executables)
    raise BundleError(
        "The app layer's executable bits disagree with Git. "
        f"Git marks these executable but the layer does not: {missing or 'none'}. "
        f"The layer marks these executable but Git does not: {surplus or 'none'}."
    )


def write_app_manifest(
    app_root: Path,
    *,
    version: str,
    commit: str,
    runtime_id: str,
    executables: frozenset[str] = frozenset(),
) -> dict[str, object]:
    assert_app_layer_modes_match_git(app_root, executables)
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "version": version,
        "commit": commit,
        "runtimeId": runtime_id,
        # Computed before the manifest exists, and therefore excluding it: the
        # manifest cannot contain a digest of itself. Consumers comparing two
        # layers compare this field, not the file that carries it.
        "treeSha256": tree_digest(
            app_root,
            exclude=frozenset({"APP-MANIFEST.json"}),
            executables=executables,
        ),
    }
    write_json(app_root / "APP-MANIFEST.json", payload)
    return payload


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def prune_runtime(runtime_root: Path, *, platform_name: str = MACOS_PLATFORM) -> list[str]:
    """Remove development-only runtime content and return removed relative paths."""

    candidates: set[Path] = set()
    windows = platform_name == WINDOWS_PLATFORM
    if windows:
        prune_globs = WINDOWS_PRUNE_LIBRARY_GLOBS
        prune_paths = WINDOWS_PRUNE_RELATIVE_PATHS
    elif platform_name == LINUX_PLATFORM:
        prune_globs = LINUX_PRUNE_LIBRARY_GLOBS
        prune_paths = LINUX_PRUNE_RELATIVE_PATHS
    else:
        prune_globs = PRUNE_LIBRARY_GLOBS
        prune_paths = PRUNE_RELATIVE_PATHS
    for pattern in prune_globs:
        candidates.update(runtime_root.glob(pattern))
    candidates.update(runtime_root / relative for relative in prune_paths)

    bin_dir = runtime_root / "bin"
    if not windows and bin_dir.is_dir():
        candidates.update(
            entry for entry in bin_dir.iterdir() if entry.name not in RUNTIME_BIN_KEEP
        )

    site_packages = (
        runtime_root / "Lib" / "site-packages"
        if windows
        else runtime_root / "lib" / f"python{PYTHON_SERIES}" / "site-packages"
    )
    if site_packages.is_dir():
        for base, directories, _files in os.walk(site_packages, topdown=True):
            for name in list(directories):
                if name in {"test", "tests"}:
                    candidate = Path(base) / name
                    candidates.add(candidate)
                    directories.remove(name)

    candidates.update(runtime_root.rglob("__pycache__"))
    removed: list[str] = []
    for candidate in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        if candidate.exists() or candidate.is_symlink():
            removed.append(candidate.relative_to(runtime_root).as_posix())
            _remove(candidate)
    return sorted(removed)


def _allowed_app_path(path: PurePosixPath) -> bool:
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return False
    value = path.as_posix()
    if value in APP_SOURCE_FILES:
        return True
    return any(value.startswith(directory + "/") for directory in APP_SOURCE_DIRECTORIES)


def _excluded_app_path(path: PurePosixPath) -> bool:
    return any(part in APP_EXCLUDED_DIRECTORIES for part in path.parts) or path.suffix == ".pyc"


def _validate_app_paths(paths: Iterable[PurePosixPath]) -> None:
    siblings: dict[PurePosixPath, dict[str, str]] = {}
    for path in paths:
        parent = PurePosixPath()
        for component in path.parts:
            try:
                component.encode("utf-8")
                validate_relative_name(component, what=f"app path component in {path.as_posix()!r}")
            except (UnicodeEncodeError, UnsafeName) as exc:
                raise BundleError(f"Git names an unsafe platform-neutral app path: {exc}") from exc
            key = collision_key(component)
            existing = siblings.setdefault(parent, {}).setdefault(key, component)
            if existing != component:
                raise BundleError(
                    "Git app paths collide on a case-insensitive filesystem: "
                    f"{(parent / existing).as_posix()!r} and "
                    f"{(parent / component).as_posix()!r}"
                )
            parent /= component


class AppLayerFiles(NamedTuple):
    """What the materializer wrote, and which of it Git marks executable."""

    paths: tuple[PurePosixPath, ...]
    executables: frozenset[str]


def _git_tracked_app_entries(
    repo_root: Path,
    *,
    commit: str,
    runner: RunCallable,
) -> tuple[tuple[PurePosixPath, str, str], ...]:
    command = [
        "git",
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit,
        "--",
        *APP_SOURCE_DIRECTORIES,
        *APP_SOURCE_FILES,
    ]
    result = runner(command, cwd=repo_root, check=False, capture_output=True)
    if result.returncode != 0:
        stderr = os.fsdecode(result.stderr or b"").strip()
        raise BundleError(f"git ls-tree failed: {stderr or f'exit {result.returncode}'}")
    stdout = result.stdout
    encoded = stdout if isinstance(stdout, bytes) else stdout.encode()
    entries: list[tuple[PurePosixPath, str, str]] = []
    refused: list[str] = []
    for record in encoded.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = os.fsdecode(metadata).split()
        except ValueError as exc:
            raise BundleError(f"git ls-tree returned a malformed entry: {record!r}") from exc
        path = PurePosixPath(os.fsdecode(raw_path))
        if not _allowed_app_path(path):
            refused.append(path.as_posix())
            continue
        if _excluded_app_path(path):
            continue
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise BundleError(
                f"Tracked app source is not a regular Git blob: {path.as_posix()} "
                f"({mode} {object_type})"
            )
        entries.append((path, object_id, mode))
    if refused:
        raise BundleError(f"git ls-tree returned paths outside the app filter: {refused}")
    entries.sort(key=lambda item: item[0].as_posix())
    _validate_app_paths(path for path, _object_id, _mode in entries)
    return tuple(entries)


def git_tracked_app_files(
    repo_root: Path,
    *,
    commit: str = "HEAD",
    runner: RunCallable = subprocess.run,
) -> tuple[PurePosixPath, ...]:
    return tuple(
        path
        for path, _object_id, _mode in _git_tracked_app_entries(
            repo_root,
            commit=commit,
            runner=runner,
        )
    )


def copy_tracked_app_files(
    repo_root: Path,
    destination: Path,
    *,
    commit: str = "HEAD",
    runner: RunCallable = subprocess.run,
) -> AppLayerFiles:
    """Materialize exact app blobs from ``commit``, never checkout-filtered bytes.

    Git's mode travels with the bytes. ``write_bytes`` alone does not carry it,
    so seven tracked files that are ``100755`` in the tree -- ``scripts/install.sh``
    and ``launchers/linux/launch-wg.sh`` among them -- used to ship at ``0o644``.
    That made "the app layer has no executable files" a property of this function
    rather than of the source, and it is the reason the layer digest had to drop
    the executable bit entirely.
    """

    entries = _git_tracked_app_entries(repo_root, commit=commit, runner=runner)
    executables: list[str] = []
    for relative, object_id, mode in entries:
        target = destination.joinpath(*relative.parts)
        result = runner(
            ["git", "cat-file", "blob", object_id],
            cwd=repo_root,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            stderr = os.fsdecode(result.stderr or b"").strip()
            raise BundleError(
                f"git cat-file failed for {relative.as_posix()}: "
                f"{stderr or f'exit {result.returncode}'}"
            )
        payload = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        if mode == "100755":
            executables.append(relative.as_posix())
            # A no-op on Windows, which has no executable bit to set. The digest
            # is keyed on Git's mode rather than on this call precisely so the
            # two hosts still agree.
            target.chmod(target.stat().st_mode | 0o111)
    return AppLayerFiles(
        paths=tuple(path for path, _object_id, _mode in entries),
        executables=frozenset(executables),
    )


def _validated_existing_spa(repo_root: Path, version: str) -> Path:
    dist = repo_root / "frontend" / "dist"
    index = dist / "index.html"
    stamp_path = dist / fetch_spa.STAMP_NAME
    if not index.is_file():
        raise BundleError(
            "frontend/dist is missing. Pass --spa with a release SPA tarball or "
            "install a stamped release SPA first."
        )
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleError(
            "frontend/dist is not checksum-stamped. Pass --spa with a release SPA "
            "tarball instead of packaging a local build."
        ) from exc
    digest = stamp.get("sha256") if isinstance(stamp, dict) else None
    try:
        expected_tree = (dist / fetch_spa.TREE_STAMP_NAME).read_text(
            encoding="ascii"
        ).strip()
    except OSError:
        expected_tree = None
    if (
        not isinstance(stamp, dict)
        or stamp.get("version") != version
        or not isinstance(digest, str)
        or fetch_spa.DIGEST_RE.fullmatch(digest.lower()) is None
        or not isinstance(expected_tree, str)
        or fetch_spa.DIGEST_RE.fullmatch(expected_tree.lower()) is None
    ):
        raise BundleError(
            f"frontend/dist does not contain a verified release stamp for {version}. "
            "Pass --spa with the matching release SPA tarball."
        )
    actual_tree = fetch_spa.tree_digest(dist)
    if actual_tree != expected_tree.lower():
        raise BundleError(
            "frontend/dist changed after its release archive was verified: "
            f"tree digest {actual_tree} does not match its stamp. Pass --spa with "
            "the matching release SPA tarball."
        )
    return dist


def install_spa_layer(
    app_root: Path,
    *,
    version: str,
    archive: Path | None,
    repo_root: Path,
) -> None:
    if archive is None:
        source = _validated_existing_spa(repo_root, version)
        shutil.copytree(source, app_root / "frontend" / "dist")
        return

    archive = archive.resolve()
    if not archive.is_file():
        raise BundleError(f"No such SPA archive: {archive}")
    try:
        digest = fetch_spa._resolve_local_checksum(archive, None)
        fetch_spa.verify_archive(archive, digest)
        fetch_spa.install_archive(
            archive,
            version=version,
            digest=digest,
            source=f"release asset {archive.name}",
            root=app_root,
        )
    except fetch_spa.SpaError as exc:
        raise BundleError(str(exc)) from exc
    lock_path = app_root / "frontend" / fetch_spa.LOCK_NAME
    if lock_path.exists():
        lock_path.unlink()


def substitute_info_plist(template: Path, output: Path, version: str) -> None:
    with template.open("rb") as handle:
        payload = plistlib.load(handle)
    payload["CFBundleShortVersionString"] = version
    payload["CFBundleVersion"] = version
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)


MACOS_LAUNCHER_SOURCE = "launchers/macos/launcher.c"


def write_launcher_stub(path: Path, *, repo_root: Path, runner: RunCallable = subprocess.run) -> None:
    """Compile the bundle's main executable. It must be Mach-O, not a script.

    A shell-script main executable makes the bundle a "script bundle" -- codesign
    reports ``Format=app bundle with generic`` -- and Gatekeeper does not give
    one the ordinary unsigned-app treatment. Measured against a locally built
    .dmg with a quarantine attribute applied: launched directly it was SIGKILLed
    (exit 137), through ``open`` it was blocked with no process, and it ran only
    once the quarantine was cleared. The app was therefore unopenable exactly as
    downloaded, which is the only way a user ever receives it.

    Failing loudly when no compiler is present is deliberate. Falling back to the
    script would produce a bundle that builds, signs, uploads and installs, and
    then cannot be opened -- the failure this exists to remove.
    """

    source = repo_root / MACOS_LAUNCHER_SOURCE
    if not source.is_file():
        raise BundleError(f"The macOS launcher source is missing: {source}")
    result = runner(
        [
            "cc",
            "-arch",
            "arm64",
            "-O2",
            "-Wall",
            "-Werror",
            "-o",
            str(path),
            str(source),
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = os.fsdecode(result.stderr or b"").strip()
        raise BundleError(
            f"Could not compile the macOS launcher: {stderr or f'cc exit {result.returncode}'}"
        )
    path.chmod(0o755)


def windows_pth() -> str:
    """Return the isolated import path used by the renamed ``pythonw.exe``."""

    return "runtime\\Lib\nruntime\\DLLs\nruntime\\Lib\\site-packages\napp\nimport site\n"


def windows_pyvenv_cfg() -> str:
    """Switch the per-user site directory off before the interpreter starts.

    The ``._pth`` must contain ``import site`` for ``sitecustomize`` -- and so
    the bootstrap -- to run at all, and that also re-enables the per-user site
    directory.  Anything done from ``sitecustomize`` is far too late: CPython's
    ``site.main()`` calls ``addusersitepackages()`` before it imports
    ``sitecustomize``, so by then every ``.pth`` file in ``%APPDATA%\\Python``
    has already been *executed*.  Pruning ``sys.path`` afterwards hides the
    symptom and undoes none of it.

    ``site.venv()`` runs earlier still, ahead of ``addusersitepackages()``, and
    sets ``ENABLE_USER_SITE = False`` when it reads
    ``include-system-site-packages = false``.  A ``pyvenv.cfg`` beside the
    launcher therefore gives real isolation with no launcher of our own to
    build and maintain, and every worker subprocess inherits it for free
    because they run the same executable from the same directory.

    ``home`` is deliberately omitted: with a ``._pth`` present the interpreter
    already knows where its standard library lives, and a wrong ``home`` is a
    fatal error in ``getpath`` before any of our code can report it.
    """

    return "include-system-site-packages = false\n"


def windows_runtime_pth() -> str:
    """Pin the runtime interpreter's own search path inside its layer.

    ``pyvenv.cfg`` sits beside the launcher so it reaches the interpreter
    before start-up, but ``site.venv()`` also rewrites ``PREFIXES`` to that
    directory -- and ``runtime\\python.exe``, which has no path file of
    its own, resolves ``site-packages`` through exactly that prefix.  Left
    alone it loses its own ``site-packages`` and cannot import so much as
    ``fastapi``, which is how ``scripts/check_backends.py`` and the build's
    own backend gate break.  Spelling the paths out makes the plain
    interpreter independent of the prefix, and it inherits the user-site
    switch as a bonus: the documented support command now diagnoses the
    bundle instead of a mixture of the bundle and whatever the user
    pip-installed.

    Deliberately no ``..\\app`` entry: the app and runtime layers are
    swapped independently, and the runtime must not reach into the other.
    """

    return "Lib\nDLLs\nLib\\site-packages\nimport site\n"


def windows_desktop_bootstrap() -> str:
    """Start the desktop only for a direct launch of the renamed GUI executable.

    The same executable is deliberately usable as ``sys.executable`` by server
    workers, so the bootstrap has to tell a double-click apart from every other
    invocation.  CPython sets ``sys.argv[0]`` to the empty string when it is
    given no script, no ``-c`` and no ``-m`` -- it is never the launcher
    filename -- so an empty ``argv[0]`` beside the launcher's own
    ``sys.executable`` is exactly "the user double-clicked this".  For a
    script, ``-m`` or ``-c`` invocation importing this module only establishes
    the path.
    """

    return '''"""Bootstrap the double-clickable Windows executable."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import sys
import traceback


def _is_direct_launch() -> bool:
    return (
        sys.argv[0] == ""
        and PureWindowsPath(sys.executable).name.casefold() == "waveguide generator.exe"
    )


# Per-user site packages are switched off by the pyvenv.cfg beside the
# launcher, which takes effect before the interpreter runs any of this. There
# is deliberately nothing to do here: removing sys.path entries at this point
# would be theatre, because site.main() has already executed every .pth file
# in the user site directory by the time sitecustomize is imported.


if _is_direct_launch():
    bundle_root = Path(sys.executable).resolve().parent
    app_root = bundle_root / "app"
    os.environ["WG2_BUNDLE"] = "1"
    os.environ["WG2_APP_ROOT"] = str(app_root)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        cache_root = Path(local_app_data) / "WaveguideGenerator" / "cache"
        os.environ.setdefault("PYTHONPYCACHEPREFIX", str(cache_root / "pycache"))
        os.environ.setdefault("NUMBA_CACHE_DIR", str(cache_root / "numba"))
        # PYTHONPYCACHEPREFIX is read at interpreter start-up, long before a
        # sitecustomize import, so setting it here only ever reaches child
        # processes.  Assigning sys.pycache_prefix is what stops *this* process
        # from writing __pycache__ into the swappable app and runtime layers.
        sys.pycache_prefix = os.environ["PYTHONPYCACHEPREFIX"]
    try:
        # Deliberately not app_root.  Windows keeps an open handle on a
        # process's current directory, and a failed update has to rename
        # ``app`` out of the way while this very process asks for the rollback.
        # The bundle root is never renamed.
        os.chdir(bundle_root)
        from launchers.desktop import main

        result = main(sys.argv[1:])
    except Exception as exc:
        from launchers.statusapp.__main__ import _report_startup_failure

        _report_startup_failure(
            "Waveguide Generator could not start: "
            f"{type(exc).__name__}: {exc}",
            detail=traceback.format_exc(),
        )
        result = 1
    raise SystemExit(result)
'''


def write_windows_bootstrap(app_root: Path) -> None:
    (app_root / "wg_desktop_bootstrap.py").write_text(
        windows_desktop_bootstrap(), encoding="utf-8", newline="\n"
    )
    # CPython permits only ``import site`` in an isolated ._pth file. The site
    # hook is the supported one-line bridge to the real bootstrap module.
    (app_root / "sitecustomize.py").write_text(
        "import wg_desktop_bootstrap\n", encoding="utf-8", newline="\n"
    )


def linux_launcher() -> str:
    """The Linux bundle's entry point: what the .desktop entry and PATH run.

    It is the shell counterpart of ``launchers/macos/launcher.c`` and does the
    same four things -- resolve the bundle around itself, export ``WG2_BUNDLE``
    and ``WG2_APP_ROOT``, redirect the caches out of the installation, and exec
    the bundled interpreter on ``launchers.desktop``. A shell script is fine
    here where it was not on macOS: nothing on Linux assesses the launcher's
    format, so the reason the macOS stub had to be compiled does not exist.

    ``readlink -f`` matters. The installer puts a symlink on ``PATH``, so
    ``$0`` is routinely the link and not the file, and a launcher that resolved
    the bundle from the link would look for ``app`` in ``~/.local/bin``.

    The cache redirection is not about a seal, as it is on macOS -- there is
    nothing to break here -- but about the updater: a layer swap renames
    ``app`` and ``runtime`` aside, and ``__pycache__`` directories written into
    them by the running application are exactly the content that makes a
    directory rename fail or a rollback leave debris behind.
    """

    return r'''#!/bin/sh
# Waveguide Generator, installed. Do not edit: this file is generated by
# scripts/build_bundle.py and replaced wholesale by every update.
set -eu

self=$0
# `readlink -f` is coreutils and BSD-incompatible, which is fine: this file
# only ever runs on the Linux bundle it was built into.
if command -v readlink >/dev/null 2>&1; then
    resolved=$(readlink -f "$self" 2>/dev/null || printf '%s' "$self")
else
    resolved=$self
fi
here=$(CDPATH= cd -- "$(dirname -- "$resolved")" && pwd -P)

app=$here/app
python=$here/runtime/bin/python3.13

if [ ! -d "$app" ] || [ ! -x "$python" ]; then
    printf 'Waveguide Generator launcher: the installation at %s is incomplete.\n' "$here" >&2
    printf 'Reinstall it by running install.sh from the release tarball.\n' >&2
    exit 71
fi

WG2_BUNDLE=1
WG2_APP_ROOT=$app
export WG2_BUNDLE WG2_APP_ROOT

cache=${XDG_CACHE_HOME:-$HOME/.cache}/waveguide-generator
PYTHONPYCACHEPREFIX=$cache/pycache
NUMBA_CACHE_DIR=$cache/numba
export PYTHONPYCACHEPREFIX NUMBA_CACHE_DIR

cd -- "$app" || exit 71
exec "$python" -m launchers.desktop "$@"
'''


def linux_desktop_entry() -> str:
    """The freedesktop entry, with the install location left to the installer.

    ``@INSTALL_DIR@`` is substituted by ``install.sh``, which is the only party
    that knows where the user put the application. Shipping the template in the
    bundle rather than generating the file inside the shell script keeps the
    entry reviewable as a file, and keeps the installer's job to substitution.

    ``Categories`` names exactly one *registered main* category. ``Engineering``
    is an additional category and needs a main one beside it; a second main
    category makes ``desktop-file-validate`` warn that the entry may appear
    twice in the menu, which it does on GNOME.
    """

    return """[Desktop Entry]
Type=Application
Version=1.5
Name=Waveguide Generator
GenericName=Acoustic waveguide designer
Comment=Design and simulate acoustic waveguides locally
Exec=@INSTALL_DIR@/waveguide-generator %U
Icon=waveguide-generator
Terminal=false
Categories=Science;Engineering;
Keywords=acoustics;waveguide;horn;BEM;simulation;
StartupNotify=true
StartupWMClass=Waveguide Generator
"""


def _where_candidates(
    filename: str,
    *,
    runner: RunCallable,
    environ: dict[str, str],
) -> tuple[Path, ...]:
    try:
        result = runner(
            ["where.exe", filename],
            env=environ,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ()
    if result.returncode != 0:
        return ()
    return tuple(
        Path(line.strip()) for line in str(result.stdout or "").splitlines() if line.strip()
    )


def _env_value(environ: dict[str, str], name: str, default: str) -> str:
    """Read an environment variable the way Windows itself reads them.

    ``os.environ`` upper-cases its keys on Windows, so ``dict(os.environ)``
    holds ``SYSTEMROOT`` and an exact-case lookup for ``SystemRoot`` quietly
    misses and takes the default instead.
    """

    if name in environ:
        return environ[name]
    wanted = name.casefold()
    for key, value in environ.items():
        if key.casefold() == wanted:
            return value
    return default


IMAGE_FILE_MACHINE_AMD64 = 0x8664
RESOURCE_TYPE_VERSION = 16
VS_FIXEDFILEINFO_SIGNATURE = b"\xbd\x04\xef\xfe"
RESOURCE_SUBDIRECTORY_FLAG = 0x80000000


def is_x64_pe(path: Path) -> bool:
    """Report whether ``path`` is an x86-64 PE image.

    A 32-bit DLL copied beside a 64-bit interpreter fails at load time with an
    error that names neither the file nor the reason, so it is worth ruling out
    while the build still has the file in its hands.
    """

    try:
        with path.open("rb") as handle:
            if handle.read(2) != b"MZ":
                return False
            handle.seek(0x3C)
            header = handle.read(4)
            if len(header) != 4:
                return False
            handle.seek(int.from_bytes(header, "little"))
            if handle.read(4) != b"PE\0\0":
                return False
            machine = handle.read(2)
            if len(machine) != 2:
                return False
            return int.from_bytes(machine, "little") == IMAGE_FILE_MACHINE_AMD64
    except OSError:
        return False


def _pe_resource_blob(data: bytes, resource_type: int) -> bytes | None:
    """Return the first resource of ``resource_type`` in a PE image, or ``None``."""

    if data[:2] != b"MZ":
        return None
    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        return None
    coff = pe_offset + 4
    section_count = int.from_bytes(data[coff + 2 : coff + 4], "little")
    optional_size = int.from_bytes(data[coff + 16 : coff + 18], "little")
    optional = coff + 20
    magic = int.from_bytes(data[optional : optional + 2], "little")
    if magic == 0x20B:  # PE32+
        directories = optional + 112
    elif magic == 0x10B:  # PE32
        directories = optional + 96
    else:
        return None
    # The resource table is the third entry of the data directory.
    entry = directories + 16
    resource_rva = int.from_bytes(data[entry : entry + 4], "little")
    if not resource_rva:
        return None

    sections: list[tuple[int, int, int]] = []
    table = optional + optional_size
    for index in range(section_count):
        header = table + 40 * index
        virtual_size = int.from_bytes(data[header + 8 : header + 12], "little")
        virtual_address = int.from_bytes(data[header + 12 : header + 16], "little")
        raw_size = int.from_bytes(data[header + 16 : header + 20], "little")
        raw_offset = int.from_bytes(data[header + 20 : header + 24], "little")
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset))

    def file_offset(rva: int) -> int | None:
        for virtual_address, span, raw_offset in sections:
            if virtual_address <= rva < virtual_address + span:
                return rva - virtual_address + raw_offset
        return None

    def child_offset(directory: int, wanted: int | None) -> int | None:
        named = int.from_bytes(data[directory + 12 : directory + 14], "little")
        identified = int.from_bytes(data[directory + 14 : directory + 16], "little")
        for index in range(named + identified):
            record = directory + 16 + 8 * index
            name = int.from_bytes(data[record : record + 4], "little")
            if wanted is not None and name != wanted:
                continue
            return int.from_bytes(data[record + 4 : record + 8], "little")
        return None

    root = file_offset(resource_rva)
    if root is None:
        return None
    cursor = root
    for wanted in (resource_type, None):  # type directory, then name directory
        child = child_offset(cursor, wanted)
        if child is None or not child & RESOURCE_SUBDIRECTORY_FLAG:
            return None
        cursor = root + (child & ~RESOURCE_SUBDIRECTORY_FLAG)
    leaf = child_offset(cursor, None)  # the first language of that resource
    if leaf is None or leaf & RESOURCE_SUBDIRECTORY_FLAG:
        return None
    described = root + leaf
    payload = file_offset(int.from_bytes(data[described : described + 4], "little"))
    size = int.from_bytes(data[described + 4 : described + 8], "little")
    if payload is None or not size:
        return None
    return data[payload : payload + size]


def pe_file_version(path: Path) -> tuple[int, int, int, int] | None:
    """Read a PE image's ``VS_FIXEDFILEINFO`` file version, or ``None``.

    Not every PE carries a version resource, and the build must not reject a
    DLL merely because it could not read one, so an unreadable or absent
    version is reported as unknown rather than as a mismatch.
    """

    try:
        data = path.read_bytes()
        resource = _pe_resource_blob(data, RESOURCE_TYPE_VERSION)
    except (OSError, IndexError, ValueError):
        return None
    if not resource:
        return None
    marker = resource.find(VS_FIXEDFILEINFO_SIGNATURE)
    if marker < 0 or len(resource) < marker + 16:
        return None
    most = int.from_bytes(resource[marker + 8 : marker + 12], "little")
    least = int.from_bytes(resource[marker + 12 : marker + 16], "little")
    return (most >> 16, most & 0xFFFF, least >> 16, least & 0xFFFF)


def _format_version(version: tuple[int, int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _visual_studio_x64_directories(visual_studio: Path) -> list[Path]:
    """List the x64 CRT directories of an installed Visual Studio, newest first."""

    if not visual_studio.is_dir():
        return []
    wanted = {name.casefold() for name in MSVC_RUNTIME_DLLS}
    found: set[Path] = set()
    for base, _directories, files in os.walk(visual_studio):
        directory = Path(base)
        if "x64" not in {part.casefold() for part in directory.parts}:
            continue
        if any(name.casefold() in wanted for name in files):
            found.add(directory)
    # The redistributable directories are version-named (``14.40``, ``14.44``),
    # so a reverse path sort puts the newest toolset first.
    return sorted(found, key=lambda path: path.as_posix().casefold(), reverse=True)


def _path_directories(*, runner: RunCallable, environ: dict[str, str]) -> list[Path]:
    """List the directories ``where.exe`` reports, in the order PATH gives them."""

    directories: list[Path] = []
    for filename in MSVC_RUNTIME_DLLS:
        for candidate in _where_candidates(filename, runner=runner, environ=environ):
            parent = candidate.parent
            if parent not in directories:
                directories.append(parent)
    return directories


def _require_matched_versions(located: dict[str, Path]) -> None:
    """Reject a directory whose three DLLs plainly did not ship together."""

    versions = {name: pe_file_version(path) for name, path in located.items()}
    known = {name: version for name, version in versions.items() if version is not None}
    # The revision is the one field Microsoft services on its own; a difference
    # in major, minor or build is a mixture of two redistributables.
    if len({version[:3] for version in known.values()}) <= 1:
        return
    directory = next(iter(located.values())).parent
    detail = ", ".join(f"{name} {_format_version(known[name])}" for name in sorted(known))
    raise BundleError(
        f"The MSVC runtime DLLs in {directory} report mismatched versions ({detail}). "
        "The redistributable is serviced only as a matched set, so this combination "
        "has never been tested. Repair or reinstall the Microsoft Visual C++ x64 "
        "Redistributable and rebuild."
    )


def locate_msvc_runtime_dlls(
    *,
    runner: RunCallable = subprocess.run,
    environ: dict[str, str] | None = None,
) -> dict[str, Path]:
    """Choose one directory holding a complete, matched x64 MSVC runtime set.

    The redistributable is serviced as a set: Microsoft ships and tests
    ``vcruntime140``, ``vcruntime140_1`` and ``msvcp140`` together, and no one
    tests a current ``vcruntime140`` beside a year-old ``msvcp140``.  Resolving
    each file on its own is what produces that mixture -- on a developer machine
    ``where.exe`` answers with whatever unrelated application happens to sit
    first on ``PATH`` (a JDK, a vendor tool, sometimes a 32-bit build), so the
    three names can easily come from three installations.  This therefore picks
    a *directory* that holds all three, never a file at a time: an incomplete
    directory is skipped whole rather than topped up from the next source.

    The preference order is the machine's own installed redistributable in
    ``%SystemRoot%\\System32``, then an installed Visual Studio's x64 CRT, then
    a directory on ``PATH`` that happens to hold all three itself.
    """

    env = dict(os.environ if environ is None else environ)
    system_root = Path(_env_value(env, "SystemRoot", r"C:\Windows"))
    program_files = Path(_env_value(env, "ProgramFiles", r"C:\Program Files"))
    visual_studio = program_files / "Microsoft Visual Studio"

    def candidates() -> Iterable[Path]:
        # A generator, so that a complete System32 set costs neither a walk of
        # an installed Visual Studio nor a where.exe call.
        yield system_root / "System32"
        yield from _visual_studio_x64_directories(visual_studio)
        yield from _path_directories(runner=runner, environ=env)

    rejected: list[str] = []
    seen: set[str] = set()
    for directory in candidates():
        key = os.path.normcase(str(directory))
        if key in seen:
            continue
        seen.add(key)
        located = {name: directory / name for name in MSVC_RUNTIME_DLLS}
        missing = [
            name for name, path in located.items() if not (path.is_file() and is_x64_pe(path))
        ]
        if missing:
            rejected.append(f"{directory} (no x64 {', '.join(missing)})")
            continue
        _require_matched_versions(located)
        return {name: path.resolve() for name, path in located.items()}
    searched = "; ".join(rejected) if rejected else "no candidate directory exists"
    raise BundleError(
        "No single directory holds a complete x64 MSVC runtime set "
        f"({', '.join(MSVC_RUNTIME_DLLS)}). The redistributable is only supported as a "
        "matched set, so the build refuses to assemble one from several directories. "
        f"Searched {system_root / 'System32'}, the Visual Studio x64 CRT directories "
        f"under {visual_studio}, and the where.exe hits on PATH: {searched}. Install the "
        "Microsoft Visual C++ x64 Redistributable and rebuild."
    )


def _iter_zip_entries(root: Path, *, archive_root: str | None = None) -> Iterable[tuple[Path, str]]:
    discovered: list[tuple[Path, str]] = []

    def visit(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            if entry.name.startswith("._"):
                continue
            member = f"{prefix}{entry.name}"
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                discovered.append((path, member + "/"))
                visit(path, member + "/")
            else:
                discovered.append((path, member))

    if archive_root is None:
        visit(root, "")
    else:
        prefix = archive_root.rstrip("/") + "/"
        discovered.append((root, prefix))
        visit(root, prefix)
    return sorted(discovered, key=lambda item: item[1])


def deterministic_zip(
    source: Path,
    output: Path,
    *,
    canonical_modes: bool = False,
    compression: int = zipfile.ZIP_DEFLATED,
    archive_root: str | None = None,
    extra_root_files: Mapping[str, str] | None = None,
) -> None:
    """Archive a layer in stable path order without unsafe link members.

    ``extra_root_files`` maps a member name to its text and writes it beside
    ``archive_root`` rather than inside it. It exists so the Windows .zip can
    carry instructions that have to be read before extracting, without copying
    a 200 MB bundle into a staging directory to gain one text file.
    """

    output.parent.mkdir(parents=True, exist_ok=True)
    source_root = source.resolve()
    with zipfile.ZipFile(
        output,
        "w",
        compression=compression,
        compresslevel=9,
    ) as archive:
        for member, text in sorted((extra_root_files or {}).items()):
            info = zipfile.ZipInfo(member, ZIP_TIMESTAMP)
            info.create_system = 3
            info.compress_type = compression
            info.external_attr = 0o100644 << 16
            # Notepad is the default handler and renders a lone LF as one line.
            archive.writestr(info, "\r\n".join(text.splitlines()) + "\r\n")
        for path, member in _iter_zip_entries(source, archive_root=archive_root):
            if path.is_symlink():
                resolved = path.resolve()
                if not resolved.is_relative_to(source_root) or not resolved.is_file():
                    raise BundleError(
                        f"Layer symlink does not resolve to a file inside the layer: {path}"
                    )
                metadata = resolved.stat()
            else:
                metadata = path.lstat()
            info = zipfile.ZipInfo(member, ZIP_TIMESTAMP)
            info.create_system = 3
            info.compress_type = compression
            mode = metadata.st_mode & 0xFFFF
            if canonical_modes:
                mode = 0o40755 if member.endswith("/") else 0o100644
            info.external_attr = mode << 16
            if member.endswith("/"):
                info.external_attr |= 0x10
                archive.writestr(info, b"")
            else:
                with path.open("rb") as source_handle, archive.open(info, "w") as target:
                    shutil.copyfileobj(source_handle, target, length=1 << 20)


#: The same instant ``ZIP_TIMESTAMP`` names, as a POSIX time. Every member of a
#: release archive gets it so two builds of one commit are byte-identical.
TAR_TIMESTAMP = 315532800


def deterministic_tar_gz(
    source: Path,
    output: Path,
    *,
    archive_root: str,
    extra_root_files: Mapping[str, tuple[str, int]] | None = None,
    executable_members: frozenset[str] = frozenset(),
) -> None:
    """Archive the Linux bundle reproducibly, modes intact and links resolved.

    A ``.tar.gz`` rather than a ``.zip`` because tar carries the executable bit
    and every Linux desktop unpacks it by double-click, while a ``.zip`` drops
    the bit and hands the user a launcher and an installer they must ``chmod``
    before either will run. That is the same failure the macOS ``.command``
    already taught: a payload whose executable bit did not survive the trip
    looks like nothing happening at all.

    Symlinks are resolved into their content under the same rule
    :func:`deterministic_zip` applies, so an archive can never carry a link out
    of the tree it claims to describe. Ownership, order and timestamps are
    fixed, so rebuilding one commit produces the same bytes.

    ``extra_root_files`` maps a member name to its text and mode and writes it
    *beside* ``archive_root`` -- the installer and the instructions, which have
    to be reachable without entering the application folder.

    ``executable_members`` names paths, relative to ``archive_root``, that ship
    0755 whatever the build host recorded. Every other member keeps the host's
    executable bit, which is deliberate rather than lazy: the interpreter under
    ``runtime/bin`` has to stay executable and this function has no way to know
    which of a CPython tree's files those are. So the rule is "force the modes
    we can name, preserve the rest" -- a build host that loses the bit on the
    runtime is a separate defect, and forcing 0644 on everything unnamed would
    ship a bundle whose launcher cannot start its own interpreter.
    """

    output.parent.mkdir(parents=True, exist_ok=True)
    source_root = source.resolve()

    def entry(member: str, *, mode: int, size: int, kind: bytes) -> tarfile.TarInfo:
        info = tarfile.TarInfo(member)
        info.mode = mode
        info.mtime = TAR_TIMESTAMP
        info.size = size
        info.type = kind
        # Numeric root with empty names: a tarball that records the build
        # account's uid would differ between runners and, unpacked as root,
        # would try to restore an owner the target machine has never heard of.
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with output.open("wb") as raw:
        # mtime=0 and an empty filename keep the gzip header itself stable; the
        # default writes the current time and the output's name into it.
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for member, (text, mode) in sorted((extra_root_files or {}).items()):
                    payload = ("\n".join(text.splitlines()) + "\n").encode("utf-8")
                    archive.addfile(
                        entry(member, mode=mode, size=len(payload), kind=tarfile.REGTYPE),
                        io.BytesIO(payload),
                    )
                for path, raw_member in _iter_zip_entries(source, archive_root=archive_root):
                    member = raw_member.rstrip("/")
                    if raw_member.endswith("/"):
                        archive.addfile(entry(member, mode=0o755, size=0, kind=tarfile.DIRTYPE))
                        continue
                    if path.is_symlink():
                        resolved = path.resolve()
                        if not resolved.is_relative_to(source_root) or not resolved.is_file():
                            raise BundleError(
                                "Bundle symlink does not resolve to a file inside the "
                                f"bundle: {path}"
                            )
                        metadata = resolved.stat()
                        content = resolved
                    else:
                        metadata = path.lstat()
                        content = path
                    relative_member = member[len(archive_root) + 1 :]
                    if relative_member in executable_members:
                        # Named, so the archive says 0755 even when the build
                        # host has no executable bit to read -- NTFS records
                        # none, and Path.chmod cannot set one there.
                        mode = 0o755
                    else:
                        mode = 0o755 if metadata.st_mode & 0o111 else 0o644
                    info = entry(
                        member, mode=mode, size=metadata.st_size, kind=tarfile.REGTYPE
                    )
                    with content.open("rb") as handle:
                        archive.addfile(info, handle)


def max_payload_depth(root: Path) -> int:
    """Length of the deepest path the bundle contributes, relative to its root.

    The installer refuses an install root that would push this over
    ``WINDOWS_MAX_PATH``. Measuring it here rather than writing a number into
    the .iss is the point: bempp's kernel filenames are what currently set it,
    and a dependency gaining a deeper file would otherwise silently turn a
    correct limit into a wrong one that still looks deliberate.
    """

    deepest = 0
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        deepest = max(deepest, len(path.relative_to(root).as_posix()))
    if deepest == 0:
        raise BundleError(f"Bundle has no files to measure: {root}")
    return deepest


def locate_inno_compiler(environment: Mapping[str, str]) -> Path:
    """Find ISCC.exe, preferring an explicit override."""

    override = environment.get(INNO_COMPILER_ENV)
    if override:
        candidate = Path(override)
        if not candidate.is_file():
            raise BundleError(f"{INNO_COMPILER_ENV} does not name a file: {candidate}")
        return candidate
    for template in INNO_COMPILER_CANDIDATES:
        expanded = Path(os.path.expandvars(template))
        if expanded.is_file():
            return expanded
    raise BundleError(
        "Inno Setup's compiler (ISCC.exe) was not found. It is not part of the "
        "GitHub windows runner image; install it (choco install innosetup) or "
        f"point {INNO_COMPILER_ENV} at ISCC.exe."
    )


def write_checksum(asset: Path) -> Path:
    sidecar = asset.with_name(asset.name + ".sha256")
    # sha256sum format is one LF-terminated line on every platform; Windows
    # text mode would otherwise ship CRLF sidecars beside the release assets.
    sidecar.write_text(
        f"{file_sha256(asset)}  {asset.name}\n",
        encoding="ascii",
        newline="\n",
    )
    return sidecar


def prepare_output_directory(output: Path) -> None:
    """Create a new output directory or refuse one containing stale assets."""

    if output.exists():
        if not output.is_dir():
            raise BundleError(f"Bundle output is not a directory: {output}")
        existing = sorted(path.name for path in output.iterdir())
        if existing:
            preview = ", ".join(existing[:5])
            suffix = " ..." if len(existing) > 5 else ""
            raise BundleError(
                f"Bundle output directory must be empty: {output} contains {preview}{suffix}"
            )
        return
    output.mkdir(parents=True)


def require_python_build(runtime_root: Path, expected: str) -> None:
    """Require uv's managed Python to carry the pinned standalone build marker."""

    try:
        installed = (runtime_root / "BUILD").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise BundleError(
            "The managed Python has no readable python-build-standalone BUILD marker"
        ) from exc
    if installed != expected:
        raise BundleError(
            "uv installed python-build-standalone build "
            f"{installed!r}, expected the pinned build {expected!r}."
        )


def declared_version(repo_root: Path) -> str:
    try:
        value = json.loads((repo_root / "shared" / "version.json").read_text(encoding="utf-8"))[
            "version"
        ]
    except (OSError, ValueError, KeyError) as exc:
        raise BundleError(f"Could not read shared/version.json: {exc}") from exc
    if not isinstance(value, str) or not value:
        raise BundleError("shared/version.json has no non-empty string version")
    return value


class BundleBuilder:
    def __init__(
        self,
        repo_root: Path,
        *,
        runner: RunCallable = subprocess.run,
        process_factory: ProcessFactory = subprocess.Popen,
        machine: Callable[[], str] = platform.machine,
        system: Callable[[], str] = platform.system,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.runner = runner
        self.process_factory = process_factory
        self.machine = machine
        self.system = system
        self.command_environment = dict(os.environ)
        self.command_environment["COPYFILE_DISABLE"] = "1"
        # uv-managed python-build-standalone installations carry PEP 668's
        # EXTERNALLY-MANAGED marker. This build intentionally owns its isolated
        # temporary interpreter, so permit the required direct layer install.
        self.command_environment["UV_BREAK_SYSTEM_PACKAGES"] = "1"

    def run_command(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[Any]:
        print("+ " + shlex.join(command), flush=True)
        result = self.runner(
            list(command),
            cwd=cwd or self.repo_root,
            env=self.command_environment,
            check=False,
            capture_output=capture,
            text=capture,
        )
        if result.returncode != 0:
            detail = ""
            if capture:
                detail = str(result.stderr or result.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            raise BundleError(
                f"Command failed with exit {result.returncode}: {shlex.join(command)}{suffix}"
            )
        return result

    def git_commit(self) -> str:
        result = self.run_command(["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture=True)
        commit = str(result.stdout).strip()
        if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
            raise BundleError(f"git rev-parse returned an invalid commit: {commit!r}")
        return commit

    def require_clean_worktree(self) -> None:
        result = self.runner(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            stderr = os.fsdecode(result.stderr or b"").strip()
            raise BundleError(f"git status failed: {stderr or f'exit {result.returncode}'}")
        stdout = result.stdout
        dirty = stdout if isinstance(stdout, bytes) else stdout.encode()
        if dirty:
            entries = [
                os.fsdecode(entry).strip()
                for entry in dirty.split(b"\0")
                if entry
            ]
            preview = ", ".join(entries[:5])
            suffix = " ..." if len(entries) > 5 else ""
            raise BundleError(
                "Release bundles require a clean Git worktree; found: "
                f"{preview}{suffix}"
            )

    def git_blob(self, commit: str, relative: PurePosixPath) -> bytes:
        result = self.runner(
            ["git", "cat-file", "blob", f"{commit}:{relative.as_posix()}"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            stderr = os.fsdecode(result.stderr or b"").strip()
            raise BundleError(
                f"Could not read {relative.as_posix()} from commit {commit}: "
                f"{stderr or f'exit {result.returncode}'}"
            )
        return result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()

    def build_runtime(
        self,
        destination: Path,
        *,
        python_version: str,
        python_build: str,
        runtime_recipe: str,
        runtime_id: str,
        requirements: bytes,
        pins: bytes,
        lock: bytes,
        platform_name: str = MACOS_PLATFORM,
    ) -> None:
        install_parent = destination.parent / "python-install"
        install_parent.mkdir()
        python_request = python_version
        if platform_name == WINDOWS_PLATFORM:
            python_request = f"cpython-{python_version}-windows-x86_64-none"
        elif platform_name == LINUX_PLATFORM:
            # Spelled out rather than left to the host so a build on a musl or
            # arm64 Linux cannot silently produce a runtime this asset name
            # does not describe.
            python_request = f"cpython-{python_version}-linux-x86_64-gnu"
        install_command = [
            "uv",
            "python",
            "install",
            "--install-dir",
            str(install_parent),
            "--no-bin",
            python_request,
        ]
        self.run_command(install_command)
        relative_python = (
            Path("python.exe") if platform_name == WINDOWS_PLATFORM else Path("bin") / "python3.13"
        )
        candidates = {
            candidate.resolve()
            for pattern in (
                f"*/{relative_python.as_posix()}",
                f"*/install/{relative_python.as_posix()}",
            )
            for candidate in install_parent.glob(pattern)
            if candidate.is_file()
        }
        if len(candidates) != 1:
            raise BundleError(
                f"uv did not install exactly one relocatable {relative_python} tree under "
                f"{install_parent}: {sorted(map(str, candidates))}"
            )
        installed_python = next(iter(candidates))
        installed_root = (
            installed_python.parent
            if platform_name == WINDOWS_PLATFORM
            else installed_python.parents[1]
        )
        if not installed_root.is_relative_to(install_parent.resolve()):
            raise BundleError(f"uv resolved the installed Python outside {install_parent}")
        require_python_build(installed_root, python_build)
        shutil.move(str(installed_root), destination)
        runtime_python = destination / relative_python
        inputs = destination.parent / "runtime-inputs"
        inputs.mkdir()
        requirements_path = inputs / "requirements-runtime.txt"
        pins_path = inputs / "requirements-pins.txt"
        lock_path = inputs / "requirements-lock.txt"
        requirements_path.write_bytes(requirements)
        pins_path.write_bytes(pins)
        lock_path.write_bytes(lock)
        self.run_command(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(runtime_python),
                "--no-cache",
                "-c",
                str(lock_path),
                "-r",
                str(requirements_path),
                "-r",
                str(pins_path),
            ]
        )
        self.run_command(["uv", "pip", "check", "--python", str(runtime_python)])
        helper = destination / METAL_HELPER
        if platform_name == MACOS_PLATFORM and self.machine() == "arm64" and not helper.is_file():
            raise BundleError(
                "The Apple Silicon Metal native helper is missing after installation: "
                f"{helper}. Ensure Swift is available and rebuild the runtime."
            )
        if platform_name == WINDOWS_PLATFORM:
            for filename, source in locate_msvc_runtime_dlls(
                runner=self.runner, environ=self.command_environment
            ).items():
                shutil.copy2(source, destination / filename)
            (destination / WINDOWS_RUNTIME_PTH_NAME).write_text(
                windows_runtime_pth(), encoding="utf-8", newline="\n"
            )
        removed = prune_runtime(destination, platform_name=platform_name)
        print(f"Pruned {len(removed)} runtime directories.")
        write_runtime_manifest(
            destination,
            python_version=python_version,
            runtime_id=runtime_id,
            requirements=requirements,
            pins=pins,
            lock=lock,
            python_build=python_build,
            runtime_recipe=runtime_recipe,
            platform_name=platform_name,
        )

    def build_app(
        self,
        destination: Path,
        *,
        version: str,
        runtime_id: str,
        spa: Path | None,
        commit: str,
    ) -> dict[str, object]:
        destination.mkdir()
        tracked = copy_tracked_app_files(
            self.repo_root,
            destination,
            commit=commit,
            runner=self.runner,
        )
        print(
            f"Copied {len(tracked.paths)} tracked app files "
            f"({len(tracked.executables)} executable)."
        )
        install_spa_layer(
            destination,
            version=version,
            archive=spa,
            repo_root=self.repo_root,
        )
        # This module is part of the platform-neutral app layer. It is inert on
        # macOS, and the top-level Windows executable imports it from its _pth.
        write_windows_bootstrap(destination)
        return write_app_manifest(
            destination,
            version=version,
            commit=commit,
            runtime_id=runtime_id,
            executables=tracked.executables,
        )

    def assemble_bundle(
        self,
        destination: Path,
        *,
        runtime_root: Path,
        app_root: Path,
        version: str,
    ) -> None:
        template = self.repo_root / APP_TEMPLATE
        if not template.is_dir():
            raise BundleError(f"macOS app template is missing: {template}")
        shutil.copytree(template, destination, symlinks=True)
        contents = destination / "Contents"
        substitute_info_plist(
            template / "Contents" / "Info.plist",
            contents / "Info.plist",
            version,
        )
        resources = contents / "Resources"
        shutil.copytree(runtime_root, resources / "runtime", symlinks=True)
        shutil.copytree(app_root, resources / "app", symlinks=True)
        write_launcher_stub(
            contents / "MacOS" / "Waveguide Generator",
            repo_root=self.repo_root,
            runner=self.runner,
        )
        self.run_command(["codesign", "--force", "--deep", "--sign", "-", str(destination)])

    def assemble_windows_bundle(
        self,
        destination: Path,
        *,
        runtime_root: Path,
        app_root: Path,
        icon_writer: Callable[[Path], None] = generate_icon.build_ico,
    ) -> None:
        """Assemble the folder users extract and launch from Explorer."""

        destination.mkdir()
        shutil.copytree(runtime_root, destination / "runtime", symlinks=True)
        shutil.copytree(app_root, destination / "app", symlinks=True)
        launcher_files = windows_launcher_files()
        missing = [source for source, _ in launcher_files if not (runtime_root / source).is_file()]
        if missing:
            raise BundleError(
                "The Windows runtime is missing files required beside the renamed "
                f"pythonw launcher: {', '.join(missing)}"
            )
        for source, target in launcher_files:
            shutil.copy2(runtime_root / source, destination / target)
        (destination / WINDOWS_PTH_NAME).write_text(windows_pth(), encoding="utf-8", newline="\n")
        (destination / WINDOWS_PYVENV_NAME).write_text(
            windows_pyvenv_cfg(), encoding="utf-8", newline="\n"
        )
        icon_writer(destination / WINDOWS_ICON_NAME)

    def assemble_linux_bundle(
        self,
        destination: Path,
        *,
        runtime_root: Path,
        app_root: Path,
        icon_writer: Callable[[Path, int], None] = generate_icon.write_png,
    ) -> None:
        """Assemble the folder ``install.sh`` copies into place.

        Deliberately the Windows shape and not the macOS one: ``app`` and
        ``runtime`` directly under a single root, with the launcher beside
        them. That is what ``apply_update.bundle_from_app_layer`` already
        resolves for every non-macOS platform, so the in-app updater's layer
        swap, rollback and relaunch work here without a third layout rule.

        The desktop entry and icon templates travel inside the bundle so they
        are reviewable build outputs. They sit beside ``app`` and ``runtime``,
        so the layer updater does not replace the installed desktop copies;
        those are refreshed the next time ``install.sh`` runs.
        """

        destination.mkdir()
        shutil.copytree(runtime_root, destination / "runtime", symlinks=True)
        shutil.copytree(app_root, destination / "app", symlinks=True)
        launcher = destination / LINUX_LAUNCHER_NAME
        # Written and chmod-ed here rather than copied from the checkout, for
        # the reason the .command in the disk image is: a payload that arrives
        # without its executable bit fails by doing nothing at all.
        launcher.write_text(linux_launcher(), encoding="utf-8", newline="\n")
        launcher.chmod(0o755)
        (destination / LINUX_DESKTOP_ENTRY_NAME).write_text(
            linux_desktop_entry(), encoding="utf-8", newline="\n"
        )
        icon_writer(destination / LINUX_ICON_NAME, LINUX_ICON_SIZE)

    LINUX_TARBALL_INSTRUCTIONS = """Waveguide Generator - installing on Linux
=========================================

Supported and tested target: Ubuntu 24.04 LTS on x86-64.
The application brings its own Python, its own Tcl/Tk and every Python
package it needs; nothing is installed system-wide and nothing asks for root.

What it does not bring is the system OpenGL and X11 libraries the mesher loads
when it starts. Any desktop system already has them; a server install may not.
install.sh checks before it copies anything, and prints the exact command if
something is missing. On Ubuntu 24.04 that command is:

    sudo apt install libglu1-mesa libgl1 libgomp1 libfontconfig1 \
                     libxrender1 libxcursor1 libxft2 libxinerama1 \
                     libxi6 libxext6


INSTALL
-------

Open a terminal in this folder and run:

    ./install.sh

It copies the application to ~/.local/share/waveguide-generator, adds a
launcher to your applications menu, and puts `waveguide-generator` on your
PATH at ~/.local/bin. Run it again to upgrade in place; your designs, job
history and settings are kept elsewhere and are not touched.

Pass a directory to install somewhere else:

    ./install.sh --prefix ~/opt

Everything is per-user on purpose. Waveguide Generator updates itself by
replacing files inside its own installation, which it can only do where it
does not need root -- a system-wide copy under /opt or /usr would break every
later in-app update.


UNINSTALL
---------

    ./uninstall.sh

The same script is installed alongside the application, so you do not need to
keep this tarball. Add --data to also remove your designs, job history,
meshes and logs.


WHAT IT SHOULD DO
-----------------

Waveguide Generator starts a local server on 127.0.0.1 and opens its
interface. Nothing is sent anywhere; it runs entirely on your machine.

Linux gets the status window and your browser rather than the single native
window macOS and Windows have. That is the documented behaviour, not a
failure: the native window needs a system webview this build does not depend
on.
"""

    def linux_readme(self) -> str:
        """Instructions at the root of the tarball, beside the folder."""

        return self.LINUX_TARBALL_INSTRUCTIONS

    def _linux_script(self, relative: str, what: str) -> str:
        source = self.repo_root / relative
        try:
            return source.read_text(encoding="utf-8")
        except OSError as exc:
            raise BundleError(f"The Linux {what} is missing: {source}") from exc

    def linux_installer(self) -> str:
        return self._linux_script(LINUX_INSTALLER_SOURCE, "bundle installer")

    def linux_uninstaller(self) -> str:
        return self._linux_script(LINUX_UNINSTALLER_SOURCE, "bundle uninstaller")

    def create_linux_tarball(self, bundle: Path, output: Path) -> None:
        """Pack the bundle with its installer, uninstaller and instructions.

        The scripts are read from the checkout and written into the archive at
        0755 by :func:`deterministic_tar_gz`, so a checkout that lost the
        executable bit -- a Windows one, or a release runner that is not
        Linux -- cannot ship an installer the user has to ``chmod`` first.

        The launcher gets the same treatment by name. It is written into the
        bundle and chmod-ed there, and that chmod is a no-op on Windows, so its
        mode used to be read back off the build host: a bundle packed on
        Windows shipped a launcher at 0644, which a Linux user cannot run.
        """

        deterministic_tar_gz(
            bundle,
            output,
            archive_root=LINUX_BUNDLE_DIRECTORY,
            extra_root_files={
                LINUX_INSTALLER_NAME: (self.linux_installer(), 0o755),
                LINUX_UNINSTALLER_NAME: (self.linux_uninstaller(), 0o755),
                LINUX_README_NAME: (self.linux_readme(), 0o644),
            },
            executable_members=frozenset({LINUX_LAUNCHER_NAME}),
        )

    # Windows has the same shape of first-launch wall as macOS, and it is just
    # as much of a dead end when you meet it cold. The launcher is unsigned --
    # an Authenticode certificate is a paid, identity-verified purchase, the
    # same trade as the Apple Developer ID -- so an extracted copy that still
    # carries the download mark gets SmartScreen's "Windows protected your PC",
    # whose visible button is "Don't run"; proceeding is behind an unlabelled
    # "More info". Measured 2026-08-27 on Windows 11: Explorer's Extract All
    # copies the Zone.Identifier stream onto every file it writes, so the mark
    # reaches the .exe -- but clearing it on the .zip first leaves the whole
    # extraction unmarked and SmartScreen never runs. That makes Unblock the
    # Windows counterpart of `xattr -dr com.apple.quarantine`, and a better one:
    # it is a checkbox in a Properties dialog, it is done before extracting
    # rather than after a refusal, and it needs no terminal. It only works if
    # the user is told before they extract, which is why this sits at the root
    # of the .zip, beside the folder, and not inside the installed app.
    def windows_readme(self) -> str:
        """Instructions read from inside the .zip, before anything is extracted."""

        return r"""Waveguide Generator - installing on Windows
==========================================

Steps 1 and 2 have to happen BEFORE you extract this .zip. Both take one
click, and skipping either one produces a confusing failure rather than a
clear one.


1. UNBLOCK THE ZIP
------------------

Right-click the downloaded .zip > Properties. At the bottom of the General
tab, tick "Unblock", then OK.

If you skip this, Windows copies its "downloaded from the internet" mark onto
every extracted file, and the first launch is refused with:

    Windows protected your PC
    Microsoft Defender SmartScreen prevented an unrecognised app from starting

The only button on that dialog is "Don't run". You can still proceed - click
"More info", then "Run anyway" - but nothing on the dialog says so, which is
why it is easier to clear the mark once, up front, on the .zip.

This app is not signed with an Authenticode certificate, which is a paid,
identity-verified purchase. Unsigned is why SmartScreen has nothing to
recognise. It is a statement about a missing certificate, not a finding about
this app.


2. EXTRACT TO A SHORT PATH
--------------------------

Extract to something like C:\wg - NOT into Downloads, and not onto a Desktop
that OneDrive has redirected.

Paths inside this bundle reach 132 characters. Windows still limits many
operations to 260, so an install folder longer than about 127 characters
fails partway through extraction, with an error that names one deep file and
gives no hint that length is the problem.

C:\wg leaves the whole budget free. The app can live anywhere; only the
length matters.


3. RUN IT
---------

Open the extracted "Waveguide Generator" folder and double-click
"Waveguide Generator.exe".

The first start takes a few seconds longer than later ones. It should not
raise a Windows Firewall prompt: the server listens only on 127.0.0.1, which
never leaves your machine. If something does ask for network access, you can
refuse it and the app still works.


WHAT IT SHOULD DO
-----------------

Waveguide Generator starts a local server on 127.0.0.1 and opens its
interface in a window. Nothing is sent anywhere; it runs entirely on your
machine.


UNINSTALLING
------------

Delete the folder you extracted. Nothing is written outside it except a
cache under %LOCALAPPDATA%\WaveguideGenerator, which is safe to delete too.
"""

    def build_windows_setup(
        self,
        bundle: Path,
        output: Path,
        *,
        version: str,
        environment: Mapping[str, str] | None = None,
    ) -> Path:
        """Compile the assembled bundle into an installer.

        Every define is passed rather than defaulted in the .iss, so a missing
        one is a compile error instead of an installer built against a stale
        assumption. ``MaxPayloadDepth`` in particular is measured from this
        exact bundle: it is what the installer refuses an over-long install
        root against, and a hardcoded copy would rot silently.
        """

        environment = os.environ if environment is None else environment
        compiler = locate_inno_compiler(environment)
        script = self.repo_root / WINDOWS_SETUP_SCRIPT
        if not script.is_file():
            raise BundleError(f"Installer script is missing: {script}")
        depth = max_payload_depth(bundle)
        if depth >= WINDOWS_MAX_PATH:
            raise BundleError(
                f"The bundle's own deepest path is {depth} characters, which leaves "
                f"no room under the {WINDOWS_MAX_PATH}-character limit for any "
                "install root at all."
            )
        self.run_command(
            [
                str(compiler),
                f"/DAppVersion={version}",
                f"/DPayloadDir={bundle}",
                f"/DMaxPayloadDepth={depth}",
                f"/DOutputDir={output.parent}",
                f"/DOutputBaseFilename={output.stem}",
                str(script),
            ]
        )
        if not output.is_file():
            raise BundleError(f"Inno Setup reported success but produced no {output.name}")
        return output

    #: Put the first-launch instruction where the wall is, not on a web page the
    #: user has already left. macOS refuses this app on first launch and offers
    #: NO way to proceed from the dialog: the app is ad-hoc signed, so Gatekeeper
    #: has no developer identity to attach an exception to, and Privacy & Security
    #: therefore lists nothing to allow. Measured 2026-08-27 and re-measured
    #: 2026-09-02 on macOS 26.5.2 against a genuinely quarantined download; an
    #: unsigned build gets `source=no usable signature` and would be allowed, but
    #: an unsigned arm64 binary cannot execute at all. See
    #: docs/validation/2026-09/MACOS-GATEKEEPER.md for the transcripts.
    #:
    #: The installer script beside it exists because a *script* is unsigned in a
    #: way Gatekeeper can offer an exception for, so it can be approved where the
    #: app cannot, and it then does the copy and the quarantine removal itself.
    DMG_README_NAME = "READ ME FIRST.txt"
    DMG_INSTALLER_NAME = "Install Waveguide Generator.command"
    DMG_INSTALLER_SOURCE = "installers/macos/dmg-install.command"

    def dmg_readme(self) -> str:
        return """Waveguide Generator - first launch on macOS
===========================================

macOS will refuse to open this app the first time, with:

    "Waveguide Generator" Not Opened
    Apple could not verify "Waveguide Generator" is free of malware ...

Only "Done" and "Move to Bin" are offered, and System Settings > Privacy &
Security will NOT list the app. That is expected. It is a statement about a
missing Apple signature, not a finding about this app.

TO OPEN IT - TRY THIS FIRST
---------------------------

1. Double-click "Install Waveguide Generator.command" in this window.
2. macOS refuses that too, with the same "Apple could not verify" wording.
   Click Done.
3. Open System Settings > Privacy & Security and scroll to Security.
   Unlike the app, the installer SHOULD be listed there, as

       "Install Waveguide Generator.command" was blocked to protect your Mac.

   Click "Open Anyway", authenticate, and click Open in the confirmation.
4. The installer copies the app to Applications, removes the download flag,
   and starts it. You never open Terminal.

IF THE INSTALLER IS NOT LISTED IN PRIVACY & SECURITY
----------------------------------------------------

Then do it by hand instead:

1. Drag Waveguide Generator to Applications, as usual.
2. Open Terminal (Applications > Utilities > Terminal).
3. Paste this line and press Return:

     xattr -dr com.apple.quarantine "/Applications/Waveguide Generator.app"

4. Open the app normally. This is needed once, not on every launch.

WHY
---

Apps distributed outside the App Store need a paid Apple Developer ID to be
notarized. This build is signed ad-hoc instead, which lets it run but gives
macOS no developer identity to offer you an exception for - which is why the
dialog is a dead end rather than a prompt. A plain script has no signature at
all, which is a state macOS does offer an override for; that is the only
difference between the two files, and the whole reason the installer is here.

Either route removes the "downloaded from the internet" flag that macOS put on
the file. Nothing else about the app changes.

WHAT IT SHOULD DO
-----------------

Waveguide Generator starts a local server on 127.0.0.1 and opens its interface.
Nothing is sent anywhere; it runs entirely on your machine.
"""

    def dmg_installer(self) -> str:
        source = self.repo_root / self.DMG_INSTALLER_SOURCE
        try:
            return source.read_text(encoding="utf-8")
        except OSError as exc:
            raise BundleError(f"The macOS disk-image installer is missing: {source}") from exc

    def create_dmg(self, bundle: Path, output: Path, staging: Path) -> None:
        staging.mkdir()
        shutil.copytree(bundle, staging / bundle.name, symlinks=True)
        (staging / "Applications").symlink_to("/Applications")
        (staging / self.DMG_README_NAME).write_text(self.dmg_readme(), encoding="utf-8")
        # Written rather than copied so the mode is set here and cannot arrive
        # from a checkout that lost the executable bit -- a non-executable
        # .command opens in a text editor instead of running, which is exactly
        # the silent failure this file exists to avoid. Windows checkouts
        # fabricate that bit, and the release runner is not always macOS.
        installer = staging / self.DMG_INSTALLER_NAME
        installer.write_text(self.dmg_installer(), encoding="utf-8")
        installer.chmod(0o755)
        self.run_command(
            [
                "hdiutil",
                "create",
                "-volname",
                "Waveguide Generator",
                "-srcfolder",
                str(staging),
                "-format",
                "UDZO",
                str(output),
            ]
        )

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    @staticmethod
    def _http_status(url: str) -> int | None:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:  # noqa: S310
                return int(response.status)
        except (OSError, urllib.error.URLError):
            return None

    def _terminate_process_tree(
        self,
        process: subprocess.Popen[str],
        *,
        platform_name: str,
    ) -> None:
        """Stop a verification process together with everything it started.

        The Windows launcher runs the server in a child process, so terminating
        only the launcher leaves that child holding the verification port and an
        open handle on the scratch directory -- a leak that outlives the build.
        ``taskkill /T`` is the documented way to end the whole tree, and every
        wait here is bounded so a wedged child cannot hang the build.
        """

        if process.poll() is not None:
            return
        if platform_name == WINDOWS_PLATFORM:
            try:
                self.runner(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=PROCESS_TREE_KILL_TIMEOUT,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        else:
            process.terminate()
        try:
            process.wait(timeout=PROCESS_EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass

    def verify_windows_bare_launch(
        self,
        launcher: Path,
        *,
        scratch: Path,
        environment: dict[str, str],
        timeout: float = WINDOWS_BARE_LAUNCH_TIMEOUT,
    ) -> None:
        """Start the launcher exactly as a double-click does and require a live app.

        The ``-c`` probe proves a different contract: that the adjacent Python
        and MSVC DLLs and the isolated ``._pth`` load. It cannot prove the app
        starts, because ``-c`` is precisely the invocation the bootstrap stands
        down for -- the same executable is deliberately reusable as
        ``sys.executable`` by the server's own workers. A probe like that passes
        happily while a double-click opens nothing but a REPL, so the only gate
        that covers what users do is an argument-free start.

        Arguments cannot be passed to this executable at all without turning it
        back into a plain interpreter, so the isolated data directory and the
        free port travel in the environment instead.
        """

        port = self._free_port()
        data_dir = scratch / "bare-launch-data"
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path = scratch / "bare-launcher-verification.log"
        launch_environment = dict(environment)
        # Setting these here would hide the very failure this gate exists to
        # catch: it is the bundled bootstrap's job to establish them.
        launch_environment.pop("WG2_BUNDLE", None)
        launch_environment.pop("WG2_APP_ROOT", None)
        launch_environment[DATA_DIR_ENV] = str(data_dir)
        launch_environment[PORT_ENV] = str(port)
        print(
            f"+ {shlex.join([str(launcher)])}  "
            f"[no arguments, {DATA_DIR_ENV}={data_dir}, {PORT_ENV}={port}]",
            flush=True,
        )
        with log_path.open("w+", encoding="utf-8") as log_handle:
            process = self.process_factory(
                # No arguments at all. Anything here, even a flag the app would
                # understand, gives CPython a non-empty argv[0] and the bootstrap
                # correctly declines to start the desktop.
                [str(launcher)],
                cwd=launcher.parent,
                env=launch_environment,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=getattr(
                    subprocess,
                    "CREATE_NEW_PROCESS_GROUP",
                    WINDOWS_CREATE_NEW_PROCESS_GROUP,
                )
                | getattr(subprocess, "CREATE_NO_WINDOW", WINDOWS_CREATE_NO_WINDOW),
            )
            root_status: int | None = None
            health_status: int | None = None
            exit_code: int | None = None
            deadline = time.monotonic() + timeout
            try:
                while time.monotonic() < deadline:
                    exit_code = process.poll()
                    if exit_code is not None:
                        break
                    health_status = self._http_status(f"http://127.0.0.1:{port}/health")
                    root_status = self._http_status(f"http://127.0.0.1:{port}/")
                    if root_status == 200 and health_status == 200:
                        break
                    time.sleep(0.25)
                if root_status != 200 or health_status != 200:
                    log_handle.flush()
                    log_handle.seek(0)
                    detail = log_handle.read()[-8000:]
                    exited = "" if exit_code is None else f"; the launcher exited with {exit_code}"
                    raise BundleError(
                        "Starting the Windows launcher with no arguments did not serve the "
                        f"application on 127.0.0.1:{port} within {timeout:.0f}s: "
                        f"/health={health_status}, /={root_status}{exited}. A double-click "
                        "must run the bundled bootstrap through the generated ._pth and "
                        "sitecustomize, not open a Python REPL.\n" + detail
                    )
                print(
                    "Windows launcher verification: an argument-free start served / and "
                    "/health through the bundled bootstrap."
                )
            finally:
                self._terminate_process_tree(process, platform_name=WINDOWS_PLATFORM)

    def verify_linux_bare_launch(
        self,
        launcher: Path,
        *,
        scratch: Path,
        environment: dict[str, str],
        timeout: float = LINUX_BARE_LAUNCH_TIMEOUT,
    ) -> None:
        """Start the shipped launcher and require it to serve the application.

        The server check above runs the interpreter directly, so it proves the
        app layer and the runtime and says nothing at all about the file the
        desktop entry actually executes. Everything specific to Linux lives in
        that script -- resolving the bundle through the PATH symlink, exporting
        ``WG2_BUNDLE`` and ``WG2_APP_ROOT``, redirecting the caches -- and a
        bundle whose launcher does none of it looks perfect until a user
        double-clicks it.

        Unlike the Windows launcher this one takes arguments, so the run is
        headless by request: ``--no-gui`` reaches the server through
        ``launchers.desktop``'s Linux fallback without needing a display. What
        it deliberately does not prove is the status window, which needs one.

        ``WG2_BUNDLE`` and ``WG2_APP_ROOT`` are removed from the environment
        first for the reason the Windows gate removes them: setting them here
        would hide the failure this exists to catch.
        """

        port = self._free_port()
        data_dir = scratch / "bare-launch-data"
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path = scratch / "bare-launcher-verification.log"
        launch_environment = dict(environment)
        launch_environment.pop("WG2_BUNDLE", None)
        launch_environment.pop("WG2_APP_ROOT", None)
        command = [
            str(launcher),
            "--no-gui",
            "--no-browser",
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
        ]
        print("+ " + shlex.join(command), flush=True)
        with log_path.open("w+", encoding="utf-8") as log_handle:
            process = self.process_factory(
                command,
                # From the parent of the bundle, so a launcher that resolved
                # its layers relative to the working directory rather than to
                # itself fails here rather than in someone's home directory.
                cwd=launcher.parent.parent,
                env=launch_environment,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            root_status: int | None = None
            health_status: int | None = None
            exit_code: int | None = None
            deadline = time.monotonic() + timeout
            try:
                while time.monotonic() < deadline:
                    exit_code = process.poll()
                    if exit_code is not None:
                        break
                    health_status = self._http_status(f"http://127.0.0.1:{port}/health")
                    root_status = self._http_status(f"http://127.0.0.1:{port}/")
                    if root_status == 200 and health_status == 200:
                        break
                    time.sleep(0.25)
                if root_status != 200 or health_status != 200:
                    log_handle.flush()
                    log_handle.seek(0)
                    detail = log_handle.read()[-8000:]
                    exited = "" if exit_code is None else f"; the launcher exited with {exit_code}"
                    raise BundleError(
                        f"The Linux launcher {launcher.name} did not serve the application "
                        f"on 127.0.0.1:{port} within {timeout:.0f}s: "
                        f"/health={health_status}, /={root_status}{exited}.\n" + detail
                    )
                print(
                    "Linux launcher verification: the shipped launcher served / and "
                    "/health from the bundled runtime."
                )
            finally:
                self._terminate_process_tree(process, platform_name=LINUX_PLATFORM)

    def verify_bundle(
        self,
        bundle: Path,
        scratch: Path,
        *,
        platform_name: str = MACOS_PLATFORM,
    ) -> None:
        copied_bundle = scratch / bundle.name
        shutil.copytree(bundle, copied_bundle, symlinks=True)
        resources = (
            copied_bundle / "Contents" / "Resources"
            if platform_name == MACOS_PLATFORM
            else copied_bundle
        )
        app = resources / "app"
        python = (
            resources / "runtime" / "python.exe"
            if platform_name == WINDOWS_PLATFORM
            else resources / "runtime" / "bin" / "python3.13"
        )
        environment = dict(self.command_environment)
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        environment["WG2_BUNDLE"] = "1"
        environment["WG2_APP_ROOT"] = str(app)
        # The same redirection the launcher stub performs, so that the seal
        # check below proves the bundle can run without modifying itself.
        for name in CACHE_ENVIRONMENT:
            environment[name] = str(scratch / "caches" / name.lower())

        if platform_name == WINDOWS_PLATFORM:
            launcher = copied_bundle / WINDOWS_LAUNCHER_NAME
            sentinel = scratch / "windows-launcher-probe.txt"
            probe = (
                "from pathlib import Path; "
                f"Path({str(sentinel)!r}).write_text('ready', encoding='utf-8')"
            )
            print("+ " + shlex.join([str(launcher), "-c", probe]), flush=True)
            launcher_result = self.runner(
                [str(launcher), "-c", probe],
                cwd=app,
                env=environment,
                check=False,
            )
            if launcher_result.returncode != 0 or not sentinel.is_file():
                raise BundleError(
                    "The renamed pythonw.exe launcher could not load its adjacent "
                    "Python/MSVC DLLs and isolated _pth."
                )
            print("Windows launcher verification: adjacent DLLs and _pth loaded.")

        print("+ " + shlex.join([str(python), "scripts/check_backends.py"]), flush=True)
        backends = self.runner(
            [str(python), "scripts/check_backends.py"],
            cwd=app,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        output = (str(backends.stdout or "") + str(backends.stderr or "")).strip()
        print(output)
        if backends.returncode != 0:
            raise BundleError("Bundled backend verification failed")
        if platform_name == MACOS_PLATFORM and "Metal (Apple Silicon): ready" not in output:
            raise BundleError("Bundled backend verification did not report Metal ready")
        if platform_name == WINDOWS_PLATFORM and "bempp (cross-platform): ready" not in output:
            raise BundleError(
                "Bundled backend verification did not report the Windows bempp/numba backend ready"
            )
        if platform_name == LINUX_PLATFORM and "bempp (cross-platform): ready" not in output:
            # The same claim Windows is held to, and for the same reason: bempp
            # is the portable CPU backend, it does not need an OpenCL ICD to be
            # available, and a Linux bundle that cannot run it can solve
            # nothing but axisymmetric geometries.
            raise BundleError(
                "Bundled backend verification did not report the Linux bempp/numba backend ready"
            )

        port = self._free_port()
        data_dir = scratch / "data"
        log_path = scratch / "server-verification.log"
        command = [
            str(python),
            "launch/serve.py",
            "--no-browser",
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
        ]
        print("+ " + shlex.join(command), flush=True)
        with log_path.open("w+", encoding="utf-8") as log_handle:
            process_options: dict[str, object] = {
                "cwd": app,
                "env": environment,
                "stdin": subprocess.DEVNULL,
                "stdout": log_handle,
                "stderr": subprocess.STDOUT,
                "text": True,
            }
            if platform_name == WINDOWS_PLATFORM:
                process_options["creationflags"] = getattr(
                    subprocess,
                    "CREATE_NEW_PROCESS_GROUP",
                    WINDOWS_CREATE_NEW_PROCESS_GROUP,
                ) | getattr(
                    subprocess,
                    "CREATE_NO_WINDOW",
                    WINDOWS_CREATE_NO_WINDOW,
                )
            else:
                process_options["start_new_session"] = True
            process = self.process_factory(command, **process_options)
            root_status: int | None = None
            health_status: int | None = None
            deadline = time.monotonic() + 120.0
            try:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    root_status = self._http_status(f"http://127.0.0.1:{port}/")
                    health_status = self._http_status(f"http://127.0.0.1:{port}/health")
                    if root_status == 200 and health_status == 200:
                        break
                    time.sleep(0.25)
                if root_status != 200 or health_status != 200:
                    log_handle.flush()
                    log_handle.seek(0)
                    detail = log_handle.read()[-8000:]
                    raise BundleError(
                        "Bundled server verification failed: "
                        f"/={root_status}, /health={health_status}\n{detail}"
                    )
                print("Server verification: / -> 200; /health -> 200")
            finally:
                if platform_name == WINDOWS_PLATFORM:
                    # The production server can start Julia provisioning before
                    # serving /health. Kill its tree while the parent is alive;
                    # terminating only Python can leave a child holding app/.
                    self._terminate_process_tree(process, platform_name=platform_name)
                elif process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5.0)

        if platform_name == WINDOWS_PLATFORM:
            # Last, because it is the widest gate: it needs the launcher, the
            # _pth, sitecustomize, the bootstrap, the app layer and the server
            # all at once, and the narrower failures above diagnose themselves.
            self.verify_windows_bare_launch(
                copied_bundle / WINDOWS_LAUNCHER_NAME,
                scratch=scratch,
                environment=environment,
            )

        if platform_name == LINUX_PLATFORM:
            # Last, and widest, for the same reason as the Windows gate: it
            # needs the launcher, the app layer and the server at once, and the
            # narrower failures above diagnose themselves.
            self.verify_linux_bare_launch(
                copied_bundle / LINUX_LAUNCHER_NAME,
                scratch=scratch,
                environment=environment,
            )

        if platform_name == MACOS_PLATFORM:
            # Running the bundle must not have written into it: a Gatekeeper
            # assessment of a modified bundle fails on every launch after the first.
            self.run_command(
                ["codesign", "--verify", "--deep", "--strict", str(copied_bundle)],
                capture=True,
            )
            print("Seal verification: the bundle is unchanged after running.")


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def _register_asset(assets: list[Path], asset: Path) -> None:
    assets.append(asset)
    assets.append(write_checksum(asset))


def print_asset_sizes(assets: Iterable[Path]) -> None:
    print("Assets:")
    for asset in sorted(assets, key=lambda item: item.name):
        size = asset.stat().st_size
        print(f"  {asset.name}: {size} bytes ({_format_size(size)})")


def build(args: argparse.Namespace, *, builder: BundleBuilder | None = None) -> list[Path]:
    builder = builder or BundleBuilder(REPO_ROOT)
    if args.platform == "macos":
        platform_name = MACOS_PLATFORM
        if builder.system() != "Darwin":
            raise BundleError(
                "The macOS bundle must be built on macOS; uv cannot cross-install "
                "the macos-arm64 python-build-standalone runtime."
            )
        if builder.machine().casefold() != "arm64":
            raise BundleError("This step builds only the macos-arm64 runtime and app bundle")
    elif args.platform == "windows":
        platform_name = WINDOWS_PLATFORM
        if builder.system() != "Windows":
            raise BundleError(
                "The Windows bundle must be built on a Windows host; uv cannot "
                f"cross-install the {WINDOWS_TARGET} runtime. Run this build on "
                "windows-latest or another x86-64 Windows machine."
            )
        if builder.machine().casefold() not in {"amd64", "x86_64"}:
            raise BundleError(
                "This step builds only the windows-x86_64 runtime and application folder"
            )
    elif args.platform == "linux":
        platform_name = LINUX_PLATFORM
        if builder.system() != "Linux":
            # uv *can* unpack the Linux interpreter on any host -- it is a
            # tarball. What cannot be cross-produced is the layer installed on
            # top of it: the locked set is compiled manylinux x86-64 wheels and
            # a source build of the pinned HornLab modules, neither of which a
            # macOS or Windows pip resolves. A bundle built that way would
            # carry the right interpreter and the wrong extensions.
            raise BundleError(
                "The Linux bundle must be built on Linux; the locked dependency "
                f"set installs {LINUX_TARGET} binary wheels that no other host "
                "resolves. Run this build on ubuntu-24.04 or another glibc "
                "x86-64 Linux machine."
            )
        if builder.machine().casefold() not in {"amd64", "x86_64"}:
            raise BundleError(
                "This step builds only the linux-x86_64 runtime and application folder"
            )
    else:
        raise BundleError("Choose --platform macos, --platform windows or --platform linux")
    if not args.python_version.startswith(PYTHON_SERIES + "."):
        raise BundleError(
            f"The desktop layout requires a {PYTHON_SERIES}.x Python version, "
            f"not {args.python_version!r}"
        )
    if args.runtime_only or args.app_only:
        raise BundleError(
            "Layer-only builds are disabled because they cannot receive the relocated "
            "runtime and server verification required for publishable assets."
        )

    builder.require_clean_worktree()
    commit = builder.git_commit()
    version = declared_version(builder.repo_root)
    requirements = builder.git_blob(commit, PurePosixPath("server/requirements-runtime.txt"))
    pins = builder.git_blob(commit, PurePosixPath("server/requirements-pins.txt"))
    lock = builder.git_blob(commit, PurePosixPath("server/requirements-lock.txt"))
    runtime_id = compute_runtime_id(
        requirements,
        pins,
        lock,
        args.python_version,
        PYTHON_BUILD,
        RUNTIME_RECIPE,
    )
    output = args.output
    if not output.is_absolute():
        output = builder.repo_root / output
    prepare_output_directory(output)
    assets: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="wg-bundle-") as raw_scratch:
        scratch = Path(raw_scratch)
        runtime_root = scratch / "runtime"
        app_root = scratch / "app"

        if not args.app_only:
            builder.build_runtime(
                runtime_root,
                python_version=args.python_version,
                python_build=PYTHON_BUILD,
                runtime_recipe=RUNTIME_RECIPE,
                runtime_id=runtime_id,
                requirements=requirements,
                pins=pins,
                lock=lock,
                platform_name=platform_name,
            )
            runtime_asset = output / release_assets.runtime_layer_name(
                platform_name, runtime_id
            )
            deterministic_zip(runtime_root, runtime_asset)
            _register_asset(assets, runtime_asset)

        if not args.runtime_only:
            builder.build_app(
                app_root,
                version=version,
                runtime_id=runtime_id,
                spa=args.spa,
                commit=commit,
            )
            app_asset = output / release_assets.app_layer_name(version)
            # Canonical modes avoid NTFS/POSIX checkout differences. The archive
            # is compressed: the two platforms are held to the same *content*
            # through the manifest's treeSha256, which no compressor can
            # perturb, so there is no reason to make every update download ten
            # times what it needs to.
            deterministic_zip(
                app_root,
                app_asset,
                canonical_modes=True,
            )
            _register_asset(assets, app_asset)
            manifest_asset = output / release_assets.app_manifest_name(version)
            shutil.copy2(app_root / "APP-MANIFEST.json", manifest_asset)
            _register_asset(assets, manifest_asset)

        if not args.runtime_only and not args.app_only:
            if platform_name == MACOS_PLATFORM:
                bundle = scratch / "Waveguide Generator.app"
                builder.assemble_bundle(
                    bundle,
                    runtime_root=runtime_root,
                    app_root=app_root,
                    version=version,
                )
                installer_asset = output / release_assets.installer_name(
                    release_assets.MACOS_PLATFORM, version
                )
                if installer_asset.exists():
                    installer_asset.unlink()
                builder.create_dmg(bundle, installer_asset, scratch / "dmg-staging")
            elif platform_name == LINUX_PLATFORM:
                bundle = scratch / LINUX_BUNDLE_DIRECTORY
                builder.assemble_linux_bundle(
                    bundle,
                    runtime_root=runtime_root,
                    app_root=app_root,
                )
                installer_asset = output / release_assets.installer_name(
                    release_assets.LINUX_PLATFORM, version
                )
                builder.create_linux_tarball(bundle, installer_asset)
            else:
                bundle = scratch / "Waveguide Generator"
                builder.assemble_windows_bundle(
                    bundle,
                    runtime_root=runtime_root,
                    app_root=app_root,
                )
                installer_asset = output / release_assets.installer_name(
                    release_assets.WINDOWS_PLATFORM, version
                )
                deterministic_zip(
                    bundle,
                    installer_asset,
                    archive_root=bundle.name,
                    extra_root_files={WINDOWS_README_NAME: builder.windows_readme()},
                )
                setup_asset = output / release_assets.windows_setup_name(version)
                if setup_asset.exists():
                    setup_asset.unlink()
                builder.build_windows_setup(bundle, setup_asset, version=version)
                _register_asset(assets, setup_asset)
                output_bundle = output / bundle.name
                if output_bundle.exists() or output_bundle.is_symlink():
                    _remove(output_bundle)
                shutil.copytree(bundle, output_bundle, symlinks=True)
            _register_asset(assets, installer_asset)
            if args.skip_verify:
                print("Verification skipped by --skip-verify.")
            else:
                try:
                    builder.verify_bundle(
                        bundle,
                        scratch / "verification",
                        platform_name=platform_name,
                    )
                except (BundleError, OSError):
                    print_asset_sizes(assets)
                    raise
                print("Bundle verification passed.")
        elif not args.skip_verify:
            print("Bundle verification is not applicable to a layer-only build.")

    print_asset_sizes(assets)
    return assets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        choices=("macos", "windows", "linux"),
        default=(
            "linux"
            if sys.platform.startswith("linux")
            else {"darwin": "macos", "win32": "windows"}.get(sys.platform)
        ),
        help="bundle platform (defaults to the current macOS, Windows or Linux host)",
    )
    parser.add_argument("--output", type=Path, default=Path("build/bundle"))
    parser.add_argument("--spa", type=Path, help="verified release SPA tarball to install")
    layers = parser.add_mutually_exclusive_group()
    layers.add_argument("--runtime-only", action="store_true")
    layers.add_argument("--app-only", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--python-version", default=PYTHON_VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.platform is None:
        parser.error("--platform is required outside macOS, Windows and Linux")
    if args.runtime_only and args.spa is not None:
        parser.error("--spa cannot be used with --runtime-only")
    try:
        build(args)
    except (BundleError, OSError, plistlib.InvalidFileException) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

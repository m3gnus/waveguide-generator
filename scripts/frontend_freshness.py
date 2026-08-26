#!/usr/bin/env python3
"""Detect whether ``frontend/dist`` represents the current local UI sources.

Release installs carry no local-build stamp, but they do carry the one
``scripts/fetch_spa.py`` writes, and that stamp answers this question exactly:
see :func:`release_interface_freshness`.  Only a checkout that builds its own
interface falls through to the content digest the macOS review launcher writes
after a successful build, or -- with no digest to read -- to comparing source
and build mtimes.

:func:`refresh_hint` lives here too, so that every caller that reports a stale
interface offers the one remedy the install in front of it can actually carry
out.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType


STAMP_NAME = ".wg-local-source.sha256"
_DIGEST_SCHEMA = b"wg-frontend-source-v1\0"


def build_inputs(repo_root: Path) -> tuple[Path, ...]:
    """Return every repository file that can affect the Vite build."""

    candidates: set[Path] = set()
    frontend = repo_root / "frontend"
    for directory_name in ("src", "public"):
        directory = frontend / directory_name
        if directory.is_dir():
            candidates.update(path for path in directory.rglob("*") if path.is_file())
    for pattern in (
        "index.html",
        "package*.json",
        "tsconfig*.json",
        "vite.config.*",
    ):
        candidates.update(path for path in frontend.glob(pattern) if path.is_file())
    version = repo_root / "shared" / "version.json"
    if version.is_file():
        candidates.add(version)
    return tuple(sorted(candidates))


def source_digest(repo_root: Path) -> str:
    """Hash build input names and bytes without depending on file mtimes."""

    digest = hashlib.sha256(_DIGEST_SCHEMA)
    for path in build_inputs(repo_root):
        digest.update(path.relative_to(repo_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


_FETCH_SPA: list[ModuleType | None] = []


def _fetch_spa_module() -> ModuleType | None:
    """Load the sibling ``fetch_spa`` for its release-stamp vocabulary.

    By file path, not by package name: this module is also run as a plain
    script by the macOS launcher, where ``scripts`` is not an importable
    package. Only the stamp *format* is borrowed -- which files a release
    install writes, and how their digest is computed. The stamp itself is
    always read out of the tree being judged.
    """

    if _FETCH_SPA:
        return _FETCH_SPA[0]
    module: ModuleType | None = None
    source = Path(__file__).resolve().parent / "fetch_spa.py"
    spec = importlib.util.spec_from_file_location("wg_fetch_spa_for_freshness", source)
    if source.is_file() and spec is not None and spec.loader is not None:
        candidate = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(candidate)
        except Exception:  # noqa: BLE001 - a broken checkout must not break the check
            module = None
        else:
            module = candidate
    _FETCH_SPA.append(module)
    return module


def release_interface_freshness(repo_root: Path) -> tuple[bool, str] | None:
    """Judge a checksum-stamped release interface by its own stamp.

    A release install downloads its interface and never builds one, so the
    mtime fallback below compares a tree written at install time against an
    archive that kept its build times: the sources are always newer, every
    fresh install therefore reports itself stale, and the remedy it is offered
    is the installer it has just finished running.

    ``fetch_spa`` already records what the mtimes cannot -- which release this
    ``dist`` is, and the digest of the bytes that were verified -- so where
    that stamp exists it is the answer, and the only two ways it can be wrong
    are worth reporting in their own words.

    Returns ``None`` when there is no release stamp to read, leaving the
    developer-build rules in charge.
    """

    dist = repo_root / "frontend" / "dist"
    fetch_spa = _fetch_spa_module()
    if fetch_spa is None:
        return None
    try:
        stamp = json.loads((dist / fetch_spa.STAMP_NAME).read_text(encoding="utf-8"))
        recorded_tree = (dist / fetch_spa.TREE_STAMP_NAME).read_text(encoding="ascii").strip()
        tree_version = fetch_spa.declared_version(repo_root)
    except (OSError, ValueError, UnicodeError, RuntimeError):
        # RuntimeError covers fetch_spa's own SpaError, raised when the tree
        # cannot say which version it is. Any of these means there is no
        # release stamp to judge, not that the interface is stale.
        return None
    if not isinstance(stamp, dict) or not isinstance(stamp.get("version"), str):
        return None
    installed_version = stamp["version"]
    if installed_version != tree_version:
        return False, (
            f"the installed release interface is v{installed_version}, "
            f"but this install is v{tree_version}"
        )
    try:
        if fetch_spa.tree_digest(dist) != recorded_tree:
            return False, "the installed release interface no longer matches its checksum"
    except OSError as exc:
        return False, f"frontend freshness check failed: {exc}"
    return True, f"the verified release interface v{installed_version} is installed"


def frontend_freshness(repo_root: Path) -> tuple[bool, str]:
    """Return whether the built UI matches, or at least postdates, its inputs."""

    repo_root = repo_root.resolve()
    dist_index = repo_root / "frontend" / "dist" / "index.html"
    if not dist_index.is_file():
        return False, "frontend/dist is missing"

    # Only where this install cannot build the interface itself. A checkout
    # that fetched a release SPA and then edited its sources is a developer
    # build again, and has to be told so.
    if not vite_executable(repo_root).is_file():
        release = release_interface_freshness(repo_root)
        if release is not None:
            return release

    inputs = build_inputs(repo_root)
    stamp = dist_index.parent / STAMP_NAME
    if stamp.is_file():
        try:
            recorded = stamp.read_text(encoding="ascii").strip()
            current = source_digest(repo_root)
        except (OSError, UnicodeError) as exc:
            return False, f"frontend freshness check failed: {exc}"
        if recorded == current:
            return True, "local frontend build matches its sources"
        return False, "local frontend sources changed after the last review build"

    try:
        built_at = dist_index.stat().st_mtime_ns
        newer = [path for path in inputs if path.stat().st_mtime_ns > built_at]
    except OSError as exc:
        return False, f"frontend freshness check failed: {exc}"
    if newer:
        return False, "frontend source is newer than frontend/dist"
    return True, "frontend/dist postdates its sources"


def mark_fresh(repo_root: Path) -> Path:
    """Record a successful local build atomically and return the stamp path."""

    repo_root = repo_root.resolve()
    dist = repo_root / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise FileNotFoundError("frontend/dist/index.html is missing after the build")
    stamp = dist / STAMP_NAME
    temporary = dist / f"{STAMP_NAME}.tmp"
    temporary.write_text(source_digest(repo_root) + "\n", encoding="ascii")
    temporary.replace(stamp)
    return stamp


def installer_hint() -> str:
    """The platform installer, spelled the way the user would type it."""

    if sys.platform == "darwin":
        return "installers/macos/install-wg.command"
    if os.name == "nt":
        return r"installers\windows\install-and-update.bat"
    return "installers/linux/install.sh"


def vite_executable(repo_root: Path) -> Path:
    """The Vite binary that ``npm run build`` in this checkout would run."""

    name = "vite.cmd" if os.name == "nt" else "vite"
    return repo_root / "frontend" / "node_modules" / ".bin" / name


def refresh_hint(repo_root: Path) -> str:
    """How *this* install can replace a stale ``frontend/dist``.

    Running v2 deliberately requires no Node runtime: a release install obtains
    ``frontend/dist`` as a checksum-verified download and carries neither
    node_modules nor npm.  Advising a Vite build there names a command that
    cannot run, and sends the user to install a whole toolchain to fix what
    re-running the installer fixes -- which, for the mtime fallback above, is
    usually not even a real staleness.  So only a checkout that could actually
    perform the build is told to perform it.
    """

    if not vite_executable(repo_root).is_file():
        return f"{installer_hint()} to reinstall the matching interface"
    if sys.platform == "darwin":
        # The macOS launcher rebuilds a stale frontend on the way up, so
        # relaunching is the shortest correct advice. This warning is only
        # reachable when that rebuild was skipped or could not run, in which
        # case the manual build is the fallback either way.
        return "cd frontend && npm run build, or quit and reopen Waveguide Generator"
    return "cd frontend && npm run build"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="exit 0 when the UI is fresh")
    action.add_argument("--mark", action="store_true", help="stamp a successful local build")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mark:
        try:
            stamp = mark_fresh(args.repo_root)
        except OSError as exc:
            if not args.quiet:
                print(f"Could not record the frontend build: {exc}")
            return 2
        if not args.quiet:
            print(f"Recorded current frontend sources in {stamp}")
        return 0

    fresh, reason = frontend_freshness(args.repo_root)
    if not args.quiet:
        print(reason)
    return 0 if fresh else 1


if __name__ == "__main__":
    raise SystemExit(main())

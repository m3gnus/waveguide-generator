#!/usr/bin/env python3
"""Best-effort frontend rebuild shared by the Linux and Windows launchers.

Release installs normally have no frontend sources and simply keep their
packaged ``dist``.  Source checkouts rebuild only when the content stamp says
the interface is stale and the local Node installation is already usable.
Failure is advisory: the launcher can still serve its previous build.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Mapping

from scripts.frontend_freshness import (
    frontend_freshness,
    mark_fresh,
    refresh_hint,
    vite_executable,
)


def _newest_nvm_npm(home: Path) -> Path | None:
    candidates = [
        path
        for path in (home / ".nvm" / "versions" / "node").glob("*/bin/npm")
        if path.is_file() and os.access(path, os.X_OK)
    ]
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    except OSError:
        return None


def _find_npm(environ: Mapping[str, str]) -> Path | None:
    found = shutil.which("npm", path=environ.get("PATH"))
    if found:
        return Path(found)
    home = environ.get("USERPROFILE") or environ.get("HOME")
    return _newest_nvm_npm(Path(home)) if home else None


def _npm_command(npm: Path, *, windows: bool) -> list[str]:
    if windows and npm.suffix.lower() in {".bat", ".cmd"}:
        command = os.environ.get("COMSPEC", "cmd.exe")
        return [command, "/d", "/c", str(npm), "run", "build"]
    return [str(npm), "run", "build"]


def build_frontend_if_stale(
    repo_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the existing or newly built frontend is current."""

    environment = dict(os.environ if environ is None else environ)
    if environment.get("WG2_SKIP_FRONTEND_BUILD") == "1":
        print("Skipping the frontend freshness check (WG2_SKIP_FRONTEND_BUILD=1).")
        return True

    fresh, reason = frontend_freshness(repo_root)
    if fresh:
        return True

    npm = _find_npm(environment)
    frontend = repo_root / "frontend"
    vite = vite_executable(repo_root)
    if npm is None or not vite.is_file():
        print(f"WARNING: {reason}.")
        if npm is None:
            print(f"         Run {refresh_hint(repo_root)}.")
        else:
            # Node is present, so this is a checkout whose dependencies were
            # never installed rather than a release install, which has no local
            # build to enable in the first place.
            print("         Run 'cd frontend && npm ci' to enable local builds,")
            print(f"         or {refresh_hint(repo_root)}.")
        print("         Starting with the existing build.")
        return False

    print("Frontend sources changed; building the current local interface...")
    child_environment = environment.copy()
    npm_directory = str(npm.parent)
    child_environment["PATH"] = os.pathsep.join(
        part for part in (npm_directory, child_environment.get("PATH", "")) if part
    )
    try:
        completed = subprocess.run(
            _npm_command(npm, windows=os.name == "nt"),
            cwd=frontend,
            env=child_environment,
            check=False,
        )
    except OSError as exc:
        print(f"WARNING: The frontend build could not start: {exc}.")
        print("         Starting with the previous build.")
        return False
    if completed.returncode != 0:
        print("WARNING: The frontend build failed. Starting with the previous build.")
        return False
    try:
        mark_fresh(repo_root)
    except OSError as exc:
        print(f"WARNING: The frontend built, but its source stamp could not be recorded: {exc}.")
    return True


def main() -> int:
    build_frontend_if_stale(Path(__file__).resolve().parents[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

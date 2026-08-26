"""Which build is this, not just which release it descends from.

``shared/version.json`` is bumped only when a release is cut, so it names the
*last tag*, not the code that is running. The installer
(``scripts/install.bat`` / ``scripts/install.sh``) fast-forwards to the tracked
branch rather than checking out a tag, so every build cut from ``main`` between
two releases reports the same version string. Between v0.2.4 and 2026-08-26 that
was 380 commits, and a user bug report of "it changed in 0.2.4" could not be
attributed to any of them -- nor could two installs that disagreed be told apart.

This module resolves the commit alongside the version so both are always
reported together. Resolution order:

1. A ``git`` probe of the source tree. An install *is* a checkout -- the
   installer fast-forwards it -- so the probe is ground truth and cannot go
   stale.
2. ``shared/build.json``, written by the installer at update time, for the case
   the probe cannot answer: a bundled or copied install with no ``.git`` at all.
3. ``None`` -- reported honestly as ``unknown`` rather than guessed.

The stamp is deliberately the *fallback*, not the preference. A stamp records
where the tree was when the installer last ran; anything that moves HEAD
afterwards (a developer checkout, a manual fetch, a rolled-back update) leaves
it describing a commit the running code no longer is. Trusting it over a
working probe would reintroduce exactly the misreporting this module exists to
remove, just with more precision and therefore more credibility.

The version alone stays available as ``version()`` because release-matching
logic (``launch/serve.py`` comparing the SPA stamp against the backend tree)
compares semver and must not see a build suffix.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any


#: Written by the installer next to ``version.json``; absent in a bare checkout.
BUILD_STAMP_NAME = "build.json"

#: A git probe must never delay startup on a slow or networked filesystem.
_GIT_TIMEOUT_SECONDS = 5.0

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _git(root: Path, *args: str) -> str | None:
    """Run one read-only git command, or return None if git cannot answer."""

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _stamped_identity(root: Path) -> tuple[str | None, bool, str] | None:
    stamp = _read_json(root / "shared" / BUILD_STAMP_NAME)
    if stamp is None:
        return None
    commit = stamp.get("commit")
    if not isinstance(commit, str) or not commit.strip():
        return None
    return commit.strip(), bool(stamp.get("dirty", False)), "stamp"


def _probed_identity(root: Path) -> tuple[str | None, bool, str] | None:
    commit = _git(root, "rev-parse", "HEAD")
    if commit is None:
        return None
    # An empty porcelain listing means clean. A failed probe is reported as
    # clean rather than dirty: claiming modification we did not observe would
    # make every bundled install look tampered with.
    status = _git(root, "status", "--porcelain", "-uno")
    return commit, bool(status), "git"


@lru_cache(maxsize=1)
def build_identity(root: Path | None = None) -> dict[str, Any]:
    """Resolve version, commit and provenance for the running build."""

    base = _REPOSITORY_ROOT if root is None else Path(root)
    manifest = _read_json(base / "shared" / "version.json") or {}
    raw_version = manifest.get("version")
    version = str(raw_version) if isinstance(raw_version, str) and raw_version else "unknown"

    resolved = _probed_identity(base) or _stamped_identity(base)
    if resolved is None:
        return {
            "version": version,
            "commit": None,
            "commit_short": None,
            "dirty": False,
            "source": "unavailable",
        }
    commit, dirty, source = resolved
    return {
        "version": version,
        "commit": commit,
        "commit_short": (commit or "")[:8] or None,
        "dirty": dirty,
        "source": source,
    }


def version(root: Path | None = None) -> str:
    """The release version alone, for semver comparisons."""

    return str(build_identity(root)["version"])


def build_label(root: Path | None = None) -> str:
    """Version and commit as one string safe to print, log and paste.

    ``0.2.4+g8a6078c7``, ``0.2.4+g8a6078c7.dirty``, or ``0.2.4+unknown`` when
    the commit genuinely cannot be resolved. The ``g`` prefix follows
    ``git describe`` so the suffix never reads as a semver prerelease.
    """

    identity = build_identity(root)
    short = identity["commit_short"]
    if short is None:
        return f"{identity['version']}+unknown"
    suffix = f"g{short}.dirty" if identity["dirty"] else f"g{short}"
    return f"{identity['version']}+{suffix}"


__all__ = [
    "BUILD_STAMP_NAME",
    "build_identity",
    "build_label",
    "version",
]

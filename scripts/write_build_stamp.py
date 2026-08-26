"""Record which commit this install is actually running.

``shared/version.json`` names the last release tag. The installer fast-forwards
to a tracked branch, so every build between two releases reports that same
string and a user bug report cannot be attributed to a commit. This writes
``shared/build.json`` next to it so the running build always identifies itself.

Standard library only, on purpose: the installer runs this on the bootstrap
interpreter before the virtual environment exists, exactly like ``fetch_spa.py``.

Failure here is never fatal. A missing or unreadable stamp degrades to a live
git probe and then to "unknown" (``shared/build_identity.py``); refusing to
install because provenance could not be recorded would be the wrong trade.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GIT_TIMEOUT_SECONDS = 15.0


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def write_stamp(root: Path) -> int:
    commit = _git(root, "rev-parse", "HEAD")
    if commit is None:
        print("  Skipped: this folder is not a Git clone, so there is no commit to record.")
        return 0

    # -uno: untracked files are not a modification of the build.
    status = _git(root, "status", "--porcelain", "-uno")
    stamp = {
        "commit": commit,
        "dirty": bool(status),
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "described": _git(root, "describe", "--tags", "--always", "--dirty"),
    }
    path = root / "shared" / "build.json"
    try:
        path.write_text(
            json.dumps(stamp, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"  Skipped: could not write {path.name} ({exc}).")
        return 0
    suffix = " (modified)" if stamp["dirty"] else ""
    print(f"  Recorded build {commit[:8]}{suffix}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="project folder to stamp (default: this checkout)",
    )
    args = parser.parse_args(argv)
    return write_stamp(args.root.resolve())


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Bump the pinned HornLab module dependencies to each repo's latest ``main``.

Waveguide-Generator pins its own modules — ``hornlab-waveguide-mesher``,
``hornlab-metal-bem`` and ``hornlab-bempp-bem`` — to exact commits in
``server/requirements*.txt`` (``git+https://…@<sha>``). The pins keep a public
``pip install -r`` reproducible: a fresh clone gets a known-good mesher +
solver + WG combination instead of whatever the module ``main`` happens to be
that minute. The price is that advancing a module means re-pinning by hand.

This script removes that chore. It resolves the current tip of each module's
``origin/main`` straight from GitHub (exactly what a fresh ``@main`` install
would pull) and rewrites the pins in place, so "bump to latest" is one command.

Local development is unaffected either way — WG's venv installs the modules as
editable checkouts, so it already runs the workspace source; the pins only
govern fresh / public installs.

Usage
-----
    python scripts/bump_module_pins.py            # rewrite stale pins, print summary
    python scripts/bump_module_pins.py --check    # exit 1 if any pin is stale (CI)
    python scripts/bump_module_pins.py --commit    # rewrite, then git-commit the change
    python scripts/bump_module_pins.py --branch foo  # track a branch other than main

Or via npm:  ``npm run deps:bump-pins``
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GITHUB_OWNER = "m3gnus"

# Each module and the requirements file that pins it. The mesher and the Metal
# backend ship in the default install; the bempp backend is the optional
# non-Metal fallback in its own file.
MODULES: tuple[tuple[str, str], ...] = (
    ("hornlab-waveguide-mesher", "server/requirements.txt"),
    ("hornlab-metal-bem", "server/requirements.txt"),
    ("hornlab-bempp-bem", "server/requirements-bempp.txt"),
)


def _pin_pattern(repo: str) -> re.Pattern[str]:
    """Match ``git+https://github.com/<owner>/<repo>(.git)?@<sha>`` capturing the sha."""

    return re.compile(
        r"(git\+https://github\.com/"
        + re.escape(GITHUB_OWNER)
        + "/"
        + re.escape(repo)
        + r"(?:\.git)?@)([0-9a-fA-F]{7,40})"
    )


def latest_main_sha(repo: str, branch: str) -> str:
    """Return the full commit sha at the tip of ``repo``'s ``branch`` on GitHub."""

    url = f"https://github.com/{GITHUB_OWNER}/{repo}.git"
    result = subprocess.run(
        ["git", "ls-remote", url, f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=True,
    )
    line = result.stdout.strip()
    if not line:
        raise SystemExit(f"error: {url} has no branch {branch!r}")
    return line.split()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--branch", default="main", help="branch to track on each module (default: main)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if any pin is behind its branch tip (for CI)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="git-commit the updated requirements files after rewriting",
    )
    args = parser.parse_args()

    # Group modules by the file that pins them so each file is read/written once.
    files: dict[str, list[str]] = {}
    for repo, rel in MODULES:
        files.setdefault(rel, []).append(repo)

    changes: list[tuple[str, str, str]] = []  # (repo, old_short, new_short)
    touched_files: list[str] = []

    for rel, repos in files.items():
        path = REPO_ROOT / rel
        text = path.read_text()
        updated = text
        for repo in repos:
            pattern = _pin_pattern(repo)
            match = pattern.search(updated)
            if match is None:
                raise SystemExit(f"error: no pinned line for {repo} in {rel}")
            old = match.group(2)
            new = latest_main_sha(repo, args.branch)
            if old.lower() != new.lower():
                updated = pattern.sub(lambda m: m.group(1) + new, updated, count=1)
                changes.append((repo, old[:9], new[:9]))
        if updated != text:
            touched_files.append(rel)
            if not args.check:
                path.write_text(updated)

    if not changes:
        print(f"All module pins already at the tip of {args.branch}. Nothing to do.")
        return 0

    width = max(len(repo) for repo, _, _ in changes)
    for repo, old, new in changes:
        print(f"  {repo:<{width}}  {old} -> {new}")

    if args.check:
        print(f"\n{len(changes)} pin(s) behind {args.branch}. Run without --check to update.", file=sys.stderr)
        return 1

    print(f"\nRewrote {len(touched_files)} file(s): {', '.join(touched_files)}")

    if args.commit:
        subprocess.run(["git", "-C", str(REPO_ROOT), "add", *touched_files], check=True)
        subject = f"Bump pinned module deps to latest {args.branch}"
        body = "\n".join(f"{repo}: {old} -> {new}" for repo, old, new in changes)
        subprocess.run(["git", "-C", str(REPO_ROOT), "commit", "-m", subject, "-m", body], check=True)
        print("Committed.")
    else:
        print("Review and commit when ready (or re-run with --commit).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

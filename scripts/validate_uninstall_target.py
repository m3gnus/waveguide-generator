#!/usr/bin/env python3
"""Refuse dangerously broad application-data targets before uninstalling."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


class UnsafeTarget(ValueError):
    """The configured data directory is too broad to delete recursively."""


def validate(target: Path, repo_root: Path, *, home: Path | None = None) -> Path:
    """Return an absolute target, unless deleting it could erase user/repo roots."""

    absolute = target.expanduser().absolute()
    resolved = absolute.resolve(strict=False)
    anchor = Path(resolved.anchor)
    if not resolved.anchor or resolved == anchor:
        raise UnsafeTarget("it is a filesystem root")

    # A one-component target such as /tmp, /data, or C:\\Users is still far too
    # broad for an application uninstaller, even when it is not one of the
    # specifically protected directories below.
    if len(resolved.parts) - len(anchor.parts) < 2:
        raise UnsafeTarget("it is fewer than two directories below a filesystem root")

    home_path = (Path.home() if home is None else home).resolve(strict=False)
    if resolved == home_path or resolved in home_path.parents:
        raise UnsafeTarget("it is the user home directory, or one of its parents")

    # Check both spellings: resolving catches an outside symlink into the
    # checkout, while the lexical path catches a symlink *inside* the checkout
    # whose destination happens to be elsewhere. Neither is application data an
    # uninstaller may remove.
    repo = repo_root.resolve(strict=False)
    for candidate in (absolute, resolved):
        if candidate == repo or candidate in repo.parents or repo in candidate.parents:
            raise UnsafeTarget("it is inside the Waveguide Generator checkout, or contains it")
    return absolute


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        safe = validate(args.target, args.repo_root)
    except (OSError, UnsafeTarget) as exc:
        print(
            f"ERROR: Refusing to delete configured WG2 data directory {args.target}: {exc}.",
            file=sys.stderr,
        )
        return 2
    print(safe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

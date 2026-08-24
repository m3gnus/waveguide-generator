#!/usr/bin/env python3
"""Run an installer while exclusively owning WG's data-directory lock."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


_IMPORT_ROOT = Path(
    os.environ.get("WG2_APP_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from server.platform.instance import (  # noqa: E402
    DEFAULT_PORT,
    InstanceAlreadyRunning,
    InstanceLock,
    InstanceLockError,
)
from server.platform.paths import app_root, ensure_data_layout  # noqa: E402


REPO_ROOT = app_root()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        print("Update guard needs an installer command.", file=sys.stderr)
        return 2

    try:
        paths = ensure_data_layout()
        lock = InstanceLock(paths.locks)
        lock.acquire(DEFAULT_PORT)
    except InstanceAlreadyRunning as exc:
        print(
            "Waveguide Generator must be closed before it can be updated.\n"
            f"{exc}",
            file=sys.stderr,
        )
        return 2
    except (InstanceLockError, OSError) as exc:
        print(f"Could not secure the update: {exc}", file=sys.stderr)
        return 1

    environment = dict(os.environ)
    environment["WG_UPDATE_LOCK_HELD"] = "1"
    try:
        result = subprocess.run(command, env=environment, check=False)
    finally:
        lock.release()

    if result.returncode != 0 or args.launch is None:
        return int(result.returncode)
    try:
        return int(subprocess.run([str(args.launch)], check=False).returncode)
    except OSError as exc:
        print(f"Update completed, but Waveguide Generator could not start: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run pytest on exactly the tests you name, and refuse to run on none.

``pytest`` with no path arguments runs the default collection, so a targeted
run whose file list came out empty (a glob that matched nothing, an unset shell
variable, a ``git diff`` with no changes) silently becomes the whole suite. This
launcher expands its arguments itself and stops before pytest is called when the
resolved target list is empty or any single argument resolves to nothing.

    python scripts/run_tests.py server/tests/test_design_*.py
    python scripts/run_tests.py server/tests/test_x.py::test_one -q -x

Known pytest value options accept a separate value or an attached value
(``--ignore=path``, ``-kEXPR``). Values must be nonempty and must not start with
``-``. Known flags (such as ``-q``, ``--co``, ``--lf`` and ``--ff``) pass through.
Other long options must use ``--opt=value``; unknown bare options are refused
because their arity is ambiguous. Every other argument is a target: a file, a
directory, a glob, or a ``path::node`` id. Relative targets resolve against the
repository root. An optional ``--`` ends option parsing; the emitted pytest
command always puts ``--`` before the resolved targets. Refusals exit with 2.
"""

from __future__ import annotations

import glob
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


#: pytest options whose next argument is their value, not a target.
_VALUE_OPTIONS = frozenset({
    "-p", "-k", "-m", "-c", "-o", "-W", "-n", "-r",
    "--ignore", "--ignore-glob", "--deselect", "--confcutdir", "--rootdir",
    "--basetemp", "--maxfail", "--durations", "--junitxml", "--junit-xml",
    "--log-file", "--log-level", "--tb", "--timeout", "--dist", "--import-mode",
    "--override-ini", "--capture", "--cov", "--cov-report",
})
_SHORT_VALUE_OPTIONS = frozenset(opt for opt in _VALUE_OPTIONS if len(opt) == 2)
_FLAGS = frozenset({
    "-q", "-v", "-vv", "-x", "-s", "-l", "--lf", "--ff", "--sw", "--pdb",
    "--no-header", "--co", "--collect-only", "--disable-warnings",
})


def refuse(reason: str) -> None:
    """Explain why invoking pytest would be unsafe and exit with usage status."""

    print(f"run_tests: {reason}", file=sys.stderr)
    raise SystemExit(2)


def check_value(option: str, value: str) -> None:
    if not value or value.startswith("-"):
        refuse(f"{option} requires a nonempty value that does not start with '-'.")


def resolve_target(arg: str) -> list[str]:
    """Expand one target to concrete paths (node ids kept), or [] when none exist."""

    path_part, sep, node = arg.partition("::")
    if not path_part:
        return []
    pattern = path_part if Path(path_part).is_absolute() else str(REPO_ROOT / path_part)
    found = sorted(glob.glob(pattern, recursive=True))
    resolved: list[str] = []
    for item in found:
        p = Path(item)
        if p.is_dir():
            # A directory with nothing pytest could collect is an empty target.
            if not any(p.rglob("test_*.py")) and not any(p.rglob("*_test.py")):
                continue
        elif not p.is_file():
            continue
        resolved.append(item + sep + node if sep else item)
    return resolved


def build_command(argv: list[str]) -> list[str]:
    """Return the pytest command, or raise SystemExit(2) with the reason."""

    options: list[str] = []
    targets: list[str] = []
    args = iter(argv)
    for arg in args:
        if arg == "--":
            targets.extend(args)
            break
        if arg in _VALUE_OPTIONS:
            value = next(args, "")
            check_value(arg, value)
            options.extend([arg, value])
        elif arg in _FLAGS:
            options.append(arg)
        elif arg.startswith("--") and "=" in arg:
            option, _, value = arg.partition("=")
            check_value(option, value)
            options.append(arg)
        elif arg[:2] in _SHORT_VALUE_OPTIONS and len(arg) > 2:
            check_value(arg[:2], arg[2:])
            options.append(arg)
        elif arg.startswith("-") and arg != "-":
            refuse(
                f"unknown option {arg!r}. Use --opt=value for other long options; "
                "bare --flag options must be in the known flags set."
            )
        else:
            targets.append(arg)
    if not targets:
        refuse(
            "no test targets given. Refusing to run, because pytest with "
            "no paths runs the whole default suite. Name files, directories or globs."
        )
    paths: list[str] = []
    for target in targets:
        matched = resolve_target(target)
        if not matched:
            refuse(f"{target!r} resolves to no test files. Refusing to run.")
        paths.extend(matched)
    return [sys.executable, "-m", "pytest", *options, "--", *paths]


def main(argv: list[str] | None = None) -> int:
    command = build_command(sys.argv[1:] if argv is None else argv)
    return subprocess.call(command, cwd=REPO_ROOT)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Run pytest on exactly the tests you name, and refuse to run on none.

``pytest`` with no path arguments runs the default collection, so a targeted
run whose file list came out empty (a glob that matched nothing, an unset shell
variable, a ``git diff`` with no changes) silently becomes the whole suite. This
launcher expands its arguments itself and stops before pytest is called when the
resolved target list is empty or any single argument resolves to nothing.

    python scripts/run_tests.py server/tests/test_design_*.py
    python scripts/run_tests.py server/tests/test_x.py::test_one -q -x
    python scripts/run_tests.py --changed main -m "not slow" -q
    python scripts/run_tests.py --full -q

``--changed [base]`` selects the checked-in area map plus working-tree changes;
unknown/shared paths fall back to the full suite. An empty diff is refused.
``--full`` explicitly selects server/tests and scripts/tests, including slow
tests, with WG_TEST_WORKERS (default 4, range 0-6). Named targets stay serial
unless given -n. Parallel runs always use --dist=loadgroup.

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
import re
import os
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
FULL_SUITE = ["server/tests", "scripts/tests"]
TEST_MAP = Path(__file__).with_name("test_map.toml")


def changed_paths(base: str = "HEAD") -> list[str]:
    """Include branch changes, staged/unstaged changes, and untracked files."""
    paths: set[str] = set()
    for args in (
        ["diff", "--name-only", "--no-renames", "-z", f"{base}...HEAD", "--"],
        ["diff", "--name-only", "--no-renames", "-z", "HEAD", "--"],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    ):
        try:
            output = subprocess.check_output(["git", *args], cwd=REPO_ROOT)
        except subprocess.CalledProcessError:
            refuse(f"cannot resolve changed files against {base!r}.")
        paths.update(os.fsdecode(path) for path in output.split(b"\0") if path)
    return sorted(paths)


def tests_for_changes(paths: list[str]) -> list[str]:
    areas = tomllib.loads(TEST_MAP.read_text(encoding="utf-8"))["areas"]
    selected: set[str] = set()
    for path in paths:
        if (
            Path(path).name == "conftest.py"
            or "requirements" in Path(path).name
            or path.startswith("shared/")
            or path in {"pytest.ini", "scripts/test_map.toml", "scripts/run_tests.py"}
        ):
            return FULL_SUITE.copy()
        if path.startswith(("server/tests/", "scripts/tests/")):
            if Path(path).name.startswith("test_") and (REPO_ROOT / path).is_file():
                selected.add(path)
                continue
            return FULL_SUITE.copy()
        matches = [prefix for prefix in areas if path.startswith(prefix)]
        if not matches:
            return FULL_SUITE.copy()
        prefix = max(matches, key=len)
        selected.update(areas[prefix])
        selected.update(_tests_referencing(path, prefix))
        if path.endswith(".py") and path.startswith(SPINE_SOURCES):
            selected.update(t for t in SPINE_TESTS if (REPO_ROOT / t).is_file())
    return sorted(selected)


#: The real pipeline test exercises the app end to end through its public
#: entry points, so it names almost no module; select it for any change to
#: the code it runs.
SPINE_SOURCES = ("server/", "launch/")
SPINE_TESTS = ("server/tests/test_real_pipeline.py",)
_IMPORT_LINE = re.compile(r"^\s*(?:from\s+[\w.]+\s+)?import\s", re.M)


def _tests_referencing(path: str, prefix: str) -> list[str]:
    """Test files that name the changed module or its area, found at run time.

    The map's globs are the hand-kept part. This keeps a test that imports the
    changed file (by dotted name, by path, or by bare module name on an import
    line) from being missed without anyone editing the map. Over-selection is
    harmless; missing a direct consumer is not.
    """

    changed = PurePosixPath(path)
    needles = {prefix.rstrip("/").replace("/", ".")}
    if changed.suffix == ".py":
        dotted = ".".join(changed.with_suffix("").parts)
        needles |= {dotted, changed.with_suffix("").as_posix()}
        stem = changed.stem
        stem_re = re.compile(rf"\b{re.escape(stem)}\b") if stem != "__init__" else None
    else:
        stem_re = None
    found = []
    for tests_dir in ("server/tests", "scripts/tests"):
        for test in sorted((REPO_ROOT / tests_dir).glob("test_*.py")):
            text = test.read_text(encoding="utf-8", errors="replace")
            hit = any(needle in text for needle in needles)
            if not hit and stem_re is not None:
                hit = any(
                    stem_re.search(line)
                    for line in text.splitlines()
                    if _IMPORT_LINE.match(line)
                )
            if hit:
                found.append(str(test.relative_to(REPO_ROOT)))
    return found


def launcher_targets(argv: list[str]) -> tuple[list[str], bool]:
    """Resolve explicit suite modes before the existing strict pytest parser."""
    args = list(argv)
    mode: str | None = None
    base = "HEAD"
    for index, arg in enumerate(args):
        if arg == "--":
            break
        if arg == "--full" or arg == "--changed" or arg.startswith("--changed="):
            mode = arg
            del args[index]
            if arg == "--changed" and index < len(args) and not args[index].startswith("-"):
                base = args.pop(index)
            elif arg.startswith("--changed="):
                base = arg.split("=", 1)[1]
                check_value("--changed", base)
            break
    if mode is None:
        return args, False
    # The parser below still rejects mixing a mode with another mode or bad options.
    targets = FULL_SUITE if mode == "--full" else tests_for_changes(changed_paths(base))
    if not targets:
        refuse("no changed files; no test targets selected.")
    return [*args, *targets], mode == "--full"


def bounded_workers(value: str) -> str:
    if not value.isascii() or not value.isdecimal() or not 0 <= int(value) <= 6:
        refuse("worker count must be an integer from 0 to 6; never use -n auto.")
    return str(int(value))


#: pytest options whose next argument is their value, not a target.
_VALUE_OPTIONS = frozenset({
    "-p", "-k", "-m", "-c", "-o", "-W", "-n", "-r",
    "--ignore", "--ignore-glob", "--deselect", "--confcutdir", "--rootdir",
    "--basetemp", "--maxfail", "--durations", "--junitxml", "--junit-xml",
    "--log-file", "--log-level", "--tb", "--timeout", "--dist", "--import-mode",
    "--override-ini", "--capture", "--cov", "--cov-report", "--numprocesses",
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

    argv, full = launcher_targets(argv)
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
    worker_count: str | None = None
    dist: str | None = None
    for index, option in enumerate(options):
        if option in {"-n", "--numprocesses"}:
            worker_count = bounded_workers(options[index + 1])
        elif option.startswith("-n") and len(option) > 2:
            worker_count = bounded_workers(option[2:])
        elif option.startswith("--numprocesses="):
            worker_count = bounded_workers(option.split("=", 1)[1])
        elif option == "--dist":
            dist = options[index + 1]
        elif option.startswith("--dist="):
            dist = option.split("=", 1)[1]
    if full and worker_count is None and not {"--co", "--collect-only"}.intersection(options):
        worker_count = bounded_workers(os.environ.get("WG_TEST_WORKERS", "4"))
        options.extend(["-n", worker_count])
    if worker_count is not None and int(worker_count):
        if dist is not None and dist != "loadgroup":
            refuse("parallel tests require --dist=loadgroup to honour serial groups.")
        if dist is None:
            options.append("--dist=loadgroup")
    paths: list[str] = []
    for target in targets:
        matched = resolve_target(target)
        if not matched:
            refuse(f"{target!r} resolves to no test files. Refusing to run.")
        paths.extend(matched)
    return [sys.executable, "-m", "pytest", *options, "--", *dict.fromkeys(paths)]


def main(argv: list[str] | None = None) -> int:
    command = build_command(sys.argv[1:] if argv is None else argv)
    return subprocess.call(command, cwd=REPO_ROOT)


if __name__ == "__main__":
    sys.exit(main())

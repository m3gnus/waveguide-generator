"""Static rules for the shipped POSIX installer scripts, checked on every OS."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    ROOT / "installers" / "macos" / "dmg-install.command",
    ROOT / "installers" / "linux" / "bundle-install.sh",
)
# `return` with no operand, as its own command: at the end of a line, before
# `;`, `}` or `)`, or after `||` / `&&`.
BARE_RETURN = re.compile(r"(?:^|[;&|{(]|\bthen|\bdo|\belse)\s*return\s*(?:$|[;})])")


def code_lines(path: Path) -> list[tuple[int, str]]:
    lines = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.split("#", 1)[0] if not line.lstrip().startswith("#!") else ""
        if stripped.strip():
            lines.append((number, stripped))
    return lines


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_every_return_states_its_status(script: Path) -> None:
    """Bash 5 makes a bare `return` inside a trap handler (cleanup runs in the
    EXIT trap) report the status from before the trap, not the last test's.
    Bash 3.2, macOS's /bin/sh, does not, so only Linux showed it: every
    identity check in rollback failed there. An explicit status is safe in
    every shell."""

    bare = [f"{script.name}:{number}: {line.strip()}" for number, line in code_lines(script)
            if BARE_RETURN.search(line)]
    assert bare == [], "bare return:\n" + "\n".join(bare)


def test_the_rule_finds_each_bare_return_form() -> None:
    for line in ("    return", "    [ -e x ] || return", "  if x; then return; fi", "{ y; return; }", "f() ( return )"):
        assert BARE_RETURN.search(line), line
    for line in ("    return 0", "    return 1", '    return "$status"', "    return $?", "# return"):
        assert not BARE_RETURN.search(line.split("#", 1)[0]), line


def test_the_stress_series_run_on_demand_only() -> None:
    """The full-count installer series are an on-demand evidence run; the
    default suite and the landing gate keep a few runs of each."""

    import yaml

    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "installer-stress.yml").read_text(encoding="utf-8"))
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["stress"]["env"] == {"WG_STRESS": "1"}
    source = (ROOT / "scripts" / "tests" / "test_installer_review_followups.py").read_text(encoding="utf-8")
    assert 'SERIES_RUNS = 50 if STRESS else 5' in source
    assert 'ROLLBACK_BATCHES = 10 if STRESS else 1' in source


def test_the_default_installer_sample_keeps_every_function_and_both_ends() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("scripts_tests_conftest", ROOT / "scripts" / "tests" / "conftest.py")
    conftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conftest)
    assert conftest._spread(1, 2) == {0}
    assert conftest._spread(40, 2) == {0, 39}
    assert conftest._spread(3, 3) == {0, 1, 2}
    assert conftest._spread(5, 1) == {4}
    assert conftest.INSTALLER_DEFAULT_CASES >= 2


def test_hosted_macos_runs_one_case_per_installer_function(monkeypatch) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("scripts_tests_conftest", ROOT / "scripts" / "tests" / "conftest.py")
    conftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conftest)
    monkeypatch.setattr(conftest.sys, "platform", "darwin")
    monkeypatch.setenv("CI", "true")
    assert conftest._installer_cases_per_function() == conftest.INSTALLER_HOSTED_MACOS_CASES == 1
    monkeypatch.setattr(conftest.sys, "platform", "linux")
    assert conftest._installer_cases_per_function() == conftest.INSTALLER_DEFAULT_CASES
    monkeypatch.delenv("CI")
    monkeypatch.setattr(conftest.sys, "platform", "darwin")
    assert conftest._installer_cases_per_function() == conftest.INSTALLER_DEFAULT_CASES

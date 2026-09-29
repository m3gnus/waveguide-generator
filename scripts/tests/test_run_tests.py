"""The targeted-test launcher refuses to widen a run into the default suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_tests  # noqa: E402


def test_no_targets_is_refused():
    with pytest.raises(SystemExit) as refused:
        run_tests.build_command([])
    assert "no test targets" in str(refused.value)


def test_only_options_is_refused():
    with pytest.raises(SystemExit):
        run_tests.build_command(["-q", "-x"])


def test_a_glob_matching_nothing_is_refused():
    with pytest.raises(SystemExit) as refused:
        run_tests.build_command(["scripts/tests/test_no_such_file_*.py"])
    assert "resolves to no test files" in str(refused.value)


def test_one_empty_argument_refuses_even_beside_a_good_one():
    with pytest.raises(SystemExit):
        run_tests.build_command(["scripts/tests/test_run_tests.py", "nope/*.py"])


def test_an_empty_string_argument_is_refused():
    with pytest.raises(SystemExit):
        run_tests.build_command([""])


def test_globs_expand_and_options_pass_through():
    command = run_tests.build_command(["-q", "scripts/tests/test_run_tests.p*"])
    assert command[1:4] == ["-m", "pytest", "-q"]
    assert command[4:] == [str(run_tests.REPO_ROOT / "scripts/tests/test_run_tests.py")]


def test_node_ids_keep_their_node_part():
    command = run_tests.build_command(["scripts/tests/test_run_tests.py::test_x"])
    assert command[-1].endswith("test_run_tests.py::test_x")


def test_a_directory_without_tests_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        run_tests.build_command([str(tmp_path)])


def test_value_options_are_not_taken_for_targets():
    command = run_tests.build_command(
        ["scripts/tests/test_run_tests.py", "-p", "no:cacheprovider", "-k", "glob"]
    )
    assert command[3:7] == ["-p", "no:cacheprovider", "-k", "glob"]
    assert "-p" in command and "no:cacheprovider" in command
    assert command[-1].endswith("test_run_tests.py")

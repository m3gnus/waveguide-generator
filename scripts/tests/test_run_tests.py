"""The targeted-test launcher refuses to widen a run into the default suite."""

from __future__ import annotations

import sys
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_tests  # noqa: E402


def test_no_targets_is_refused(capsys):
    with pytest.raises(SystemExit) as refused:
        run_tests.build_command([])
    assert refused.value.code == 2
    assert "no test targets" in capsys.readouterr().err


def test_only_options_is_refused():
    with pytest.raises(SystemExit):
        run_tests.build_command(["-q", "-x"])


def test_a_glob_matching_nothing_is_refused(capsys):
    with pytest.raises(SystemExit) as refused:
        run_tests.build_command(["scripts/tests/test_no_such_file_*.py"])
    assert refused.value.code == 2
    assert "resolves to no test files" in capsys.readouterr().err


def test_one_empty_argument_refuses_even_beside_a_good_one():
    with pytest.raises(SystemExit):
        run_tests.build_command(["scripts/tests/test_run_tests.py", "nope/*.py"])


def test_an_empty_string_argument_is_refused():
    with pytest.raises(SystemExit):
        run_tests.build_command([""])


def test_globs_expand_and_options_pass_through():
    command = run_tests.build_command(["-q", "scripts/tests/test_run_tests.p*"])
    assert command[1:4] == ["-m", "pytest", "-q"]
    assert command[4:] == [
        "--", str(run_tests.REPO_ROOT / "scripts/tests/test_run_tests.py")
    ]


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


@pytest.mark.parametrize("argv, message", [
    (["--ignore", "server/tests/test_cli_solve.py"], "no test targets"),
    (["server/tests", "--confcutdir"], "--confcutdir requires"),
])
def test_review_repros_exit_with_usage_error(argv, message):
    completed = subprocess.run(
        [sys.executable, str(run_tests.REPO_ROOT / "scripts/run_tests.py"), *argv],
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert message in completed.stderr
    assert not completed.stdout, "pytest must not start on a refused invocation"


@pytest.mark.parametrize("option", [
    "-p", "-k", "-m", "-c", "-o", "-W", "-r",
    "--ignore", "--ignore-glob", "--deselect", "--confcutdir", "--rootdir",
    "--basetemp", "--maxfail", "--durations", "--junitxml", "--junit-xml",
    "--log-file", "--log-level", "--tb", "--timeout", "--dist", "--import-mode",
    "--override-ini", "--capture", "--cov", "--cov-report",
])
def test_value_options_consume_their_separate_value(option):
    target = "scripts/tests/test_run_tests.py"
    command = run_tests.build_command([target, option, target])
    assert command[3:] == [option, target, "--", str(run_tests.REPO_ROOT / target)]


@pytest.mark.parametrize("option", ["-k", "--ignore", "--confcutdir"])
@pytest.mark.parametrize("value", [[], [""], ["-q"]])
def test_missing_or_dash_prefixed_value_is_refused(option, value, capsys):
    with pytest.raises(SystemExit) as refused:
        run_tests.build_command(["scripts/tests/test_run_tests.py", option, *value])
    assert refused.value.code == 2
    assert f"{option} requires a nonempty value" in capsys.readouterr().err


@pytest.mark.parametrize("option", [
    "--ignore=server/tests", "--custom-plugin-option=setting", "-kEXPR", "-rA",
])
def test_attached_value_options_pass_through(option):
    command = run_tests.build_command(["scripts/tests/test_run_tests.py", option])
    assert command[3:-1] == [option, "--"]


@pytest.mark.parametrize("option", [
    "--ignore=", "--ignore=-q", "--custom-plugin-option=", "-k-EXPR", "-r-A",
])
def test_invalid_attached_values_are_refused(option, capsys):
    with pytest.raises(SystemExit) as refused:
        run_tests.build_command(["scripts/tests/test_run_tests.py", option])
    assert refused.value.code == 2
    assert "requires a nonempty value" in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--custom-option", "--custom-flag", "-z"])
def test_unknown_bare_options_are_refused(option, capsys):
    with pytest.raises(SystemExit) as refused:
        run_tests.build_command(["scripts/tests/test_run_tests.py", option])
    assert refused.value.code == 2
    message = capsys.readouterr().err
    assert "unknown option" in message
    assert "--opt=value" in message
    assert "--flag" in message


@pytest.mark.parametrize("flag", [
    "-q", "-v", "-vv", "-x", "-s", "-l", "--lf", "--ff", "--sw", "--pdb",
    "--no-header", "--co", "--collect-only", "--disable-warnings",
])
def test_known_flags_do_not_consume_targets(flag):
    target = "scripts/tests/test_run_tests.py"
    command = run_tests.build_command([flag, target])
    assert command[3:] == [flag, "--", str(run_tests.REPO_ROOT / target)]


def test_emitted_command_ends_with_separator_and_only_resolved_paths():
    command = run_tests.build_command([
        "scripts/tests/test_run_tests.p*", "-q", "-k", "glob",
        "scripts/tests/test_server_test_prerequisite.py::test_with_dist_the_app_mounting_test_passes",
    ])
    assert command[:7] == [sys.executable, "-m", "pytest", "-q", "-k", "glob", "--"]
    assert command[7:] == [
        str(run_tests.REPO_ROOT / "scripts/tests/test_run_tests.py"),
        str(run_tests.REPO_ROOT / "scripts/tests/test_server_test_prerequisite.py")
        + "::test_with_dist_the_app_mounting_test_passes",
    ]


def test_input_separator_ends_option_parsing(tmp_path, monkeypatch):
    monkeypatch.setattr(run_tests, "REPO_ROOT", tmp_path)
    target = tmp_path / "--custom-flag"
    target.write_text("", encoding="utf-8")
    command = run_tests.build_command(["-q", "--", target.name])
    assert command[3:] == ["-q", "--", str(target)]


def test_launcher_collects_only_the_named_real_test_file():
    target = "scripts/tests/test_installer_tag_relaunch.py"
    completed = subprocess.run(
        [sys.executable, str(run_tests.REPO_ROOT / "scripts/run_tests.py"),
         target, "--co", "-q", "-p", "no:cacheprovider"],
        cwd=run_tests.REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    collected = [line for line in completed.stdout.splitlines() if "::" in line]
    assert collected == [
        f"{target}::test_shell_tag_checkout_execs_the_fresh_installer",
        f"{target}::test_windows_tag_checkout_requests_a_fresh_staged_copy",
    ], output
    assert "2 tests collected" in output, output


@pytest.mark.parametrize("args", [["-n", "auto"], ["-n7"], ["--numprocesses=logical"], ["-n", "-1"]])
def test_worker_count_cannot_expand_to_the_shared_machine(args):
    with pytest.raises(SystemExit) as refused:
        run_tests.build_command(["scripts/tests/test_run_tests.py", *args])
    assert refused.value.code == 2


@pytest.mark.parametrize("args", [["-n", "4"], ["-n4"], ["--numprocesses=4"], ["--numprocesses", "4"]])
def test_parallel_options_use_loadgroup(args):
    command = run_tests.build_command(["scripts/tests/test_run_tests.py", *args])
    assert "--dist=loadgroup" in command


def test_parallel_cannot_bypass_serial_groups():
    with pytest.raises(SystemExit):
        run_tests.build_command(["scripts/tests/test_run_tests.py", "-n4", "--dist=load"])


def test_full_has_bounded_configurable_default_and_explicit_override(monkeypatch):
    monkeypatch.delenv("WG_TEST_WORKERS", raising=False)
    command = run_tests.build_command(["--full", "-q"])
    assert command[3:7] == ["-q", "-n", "4", "--dist=loadgroup"]
    assert command[-2:] == [str(run_tests.REPO_ROOT / path) for path in run_tests.FULL_SUITE]
    monkeypatch.setenv("WG_TEST_WORKERS", "6")
    assert "6" in run_tests.build_command(["--full"])
    assert "-n0" in run_tests.build_command(["--full", "-n0"])
    monkeypatch.setenv("WG_TEST_WORKERS", "auto")
    with pytest.raises(SystemExit):
        run_tests.build_command(["--full"])


def test_full_collection_does_not_start_workers():
    assert "-n" not in run_tests.build_command(["--full", "--co"])


@pytest.mark.parametrize("path", [
    "conftest.py", "server/tests/conftest.py", "server/requirements-dev.txt",
    "server/requirements-lock.txt", "shared/js/frame.mjs", "server/app.py", "unknown/file.py",
    "server/tests/test_deleted.py", "scripts/test_map.toml", "scripts/run_tests.py", "pytest.ini",
])
def test_shared_unknown_or_deleted_tests_fall_back_to_full(path):
    assert run_tests.tests_for_changes([path]) == run_tests.FULL_SUITE


@pytest.mark.parametrize("area", [
    "server/cadlink", "server/jobs", "server/solver", "server/mesh", "server/updates",
    "frontend", "launch", "launchers", "installers", "scripts",
])
def test_every_mapped_area_resolves_to_existing_tests(area):
    targets = run_tests.tests_for_changes([f"{area}/source.py"])
    assert targets and targets != run_tests.FULL_SUITE
    for target in targets:
        assert run_tests.resolve_target(target), target


def test_changed_tests_select_themselves_and_union_areas():
    path = "scripts/tests/test_run_tests.py"
    assert run_tests.tests_for_changes([path]) == [path]
    assert set(run_tests.tests_for_changes(["server/jobs/a.py", "server/mesh/a.py"])) == (
        set(run_tests.tests_for_changes(["server/jobs/a.py"]))
        | set(run_tests.tests_for_changes(["server/mesh/a.py"]))
    )


@pytest.mark.parametrize("area", ["cadlink", "jobs", "solver", "mesh", "updates"])
def test_map_covers_tests_that_directly_reference_each_server_area(area):
    targets = run_tests.tests_for_changes([f"server/{area}/source.py"])
    covered = {Path(path) for target in targets for path in run_tests.resolve_target(target)}
    tests = [* (run_tests.REPO_ROOT / "server/tests").glob("test_*.py"),
             * (run_tests.REPO_ROOT / "scripts/tests").glob("test_*.py")]
    missing = [str(test.relative_to(run_tests.REPO_ROOT)) for test in tests
               if f"server.{area}" in test.read_text() and test not in covered]
    assert not missing, f"add these tests to the {area} map: {missing}"


def test_changed_empty_diff_is_refused(monkeypatch):
    monkeypatch.setattr(run_tests, "changed_paths", lambda base: [])
    with pytest.raises(SystemExit):
        run_tests.build_command(["--changed"])


def test_changed_base_and_pytest_options(monkeypatch):
    bases = []

    def changed(base):
        bases.append(base)
        return ["scripts/tests/test_run_tests.py"]

    monkeypatch.setattr(run_tests, "changed_paths", changed)
    for args in (["--changed", "main", "-m", "not slow"], ["--changed=main", "-q"], ["--changed", "-q"]):
        assert run_tests.build_command(args)[-1].endswith("test_run_tests.py")
    assert bases == ["main", "main", "HEAD"]


def test_changed_git_includes_branch_staged_unstaged_and_untracked(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    (tmp_path / "base.py").write_text("base")
    git("add", "base.py")
    git("commit", "-qm", "Base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "branch.py").write_text("branch")
    git("add", "branch.py")
    git("commit", "-qm", "Branch")
    (tmp_path / "staged.py").write_text("staged")
    git("add", "staged.py")
    (tmp_path / "base.py").write_text("working")
    (tmp_path / "untracked name.py").write_text("untracked")
    monkeypatch.setattr(run_tests, "REPO_ROOT", tmp_path)
    assert run_tests.changed_paths(base) == ["base.py", "branch.py", "staged.py", "untracked name.py"]
    with pytest.raises(SystemExit):
        run_tests.changed_paths("missing-revision")

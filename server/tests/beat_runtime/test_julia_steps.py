from __future__ import annotations

import io
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from server.solver.beat_runtime import julia_steps, paths


@pytest.fixture(autouse=True)
def isolated_steps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path / "wg"))


def test_guard_wraps_once_and_survives_broken_stderr(monkeypatch):
    lines = []
    report = julia_steps.guarded_status(lines.append)
    assert julia_steps.guarded_status(report) is report
    report("one")
    assert lines == ["one"]

    def fail(*args):
        raise RuntimeError("broken console")

    monkeypatch.setattr(julia_steps.sys, "stderr", SimpleNamespace(write=fail))
    report = julia_steps.guarded_status(fail)
    report("first")
    report("second")


@pytest.mark.parametrize("returncode", [0, 2])
def test_streaming_command_environment_and_bounded_failure_tail(returncode):
    process = SimpleNamespace(stdout=io.StringIO("\n" + "\n".join(f"line {i}" for i in range(30))),
                              wait=lambda: returncode, poll=lambda: returncode)
    captured = []
    lines = []

    def popen(command, **kwargs):
        captured.append((command, kwargs))
        return process

    options = dict(project=Path("CPU project"), environment={"JULIA_NUM_THREADS": "3", paths.RUNTIME_DIR_ENV: str(paths.runtime_dir().parent)},
                   label="Instantiating", status_cb=lines.append, popen=popen)
    if returncode:
        with pytest.raises(RuntimeError, match="exit 2") as error:
            julia_steps.run_julia_step("selected Julia", "using Pkg", **options)
        assert "line 19\n" not in str(error.value)
        assert "line 20\n" in str(error.value) and "line 29" in str(error.value)
    else:
        julia_steps.run_julia_step("selected Julia", "using Pkg", **options)
    command, kw = captured[0]
    assert command == ["selected Julia", f"--project={Path('CPU project').resolve()}", "--startup-file=no", "-e", "using Pkg"]
    assert kw["env"] == dict(options["environment"], JULIA_DEPOT_PATH=str(paths.runtime_dir() / "depot"))
    assert kw["errors"] == "replace"
    assert kw["stderr"] == subprocess.STDOUT
    assert lines[0] == "Instantiating" and len(lines) == 31
    assert process.stdout.closed


def test_callback_failure_does_not_interrupt_child(capsys):
    seen = []

    def fail(message):
        seen.append(message)
        raise RuntimeError("console")

    process = SimpleNamespace(stdout=io.StringIO("progress\n"), wait=lambda: 0, poll=lambda: 0)
    julia_steps.run_julia_step("fake", "code", project=Path("project"), environment={paths.RUNTIME_DIR_ENV: str(paths.runtime_dir().parent)},
                              label="setup", status_cb=fail, popen=lambda *a, **kw: process)
    assert seen == ["setup", "progress"]
    assert capsys.readouterr().err.count("status callback failed") == 1


def test_interrupted_output_retires_only_owned_child():
    events = []

    class BrokenOutput:
        def __iter__(self):
            raise OSError("broken stream")

        def close(self):
            events.append("close")

    def wait(timeout=None):
        events.append(("wait", timeout))
        if timeout is not None:
            raise subprocess.TimeoutExpired("fake", timeout)

    process = SimpleNamespace(stdout=BrokenOutput(), poll=lambda: None, wait=wait,
                              terminate=lambda: events.append("terminate"),
                              kill=lambda: events.append("kill"))
    with pytest.raises(OSError, match="broken stream"):
        julia_steps.run_julia_step("fake", "code", project=Path("project"), environment={paths.RUNTIME_DIR_ENV: str(paths.runtime_dir().parent)},
                                  label="setup", status_cb=lambda _: None,
                                  popen=lambda *a, **kw: process)
    assert events == ["terminate", ("wait", 5), "kill", ("wait", None), "close"]


def test_julia_subprocess_stdin_is_devnull(tmp_path):
    captured = []
    process = SimpleNamespace(stdout=io.StringIO(), wait=lambda: 0, poll=lambda: 0)

    def popen(command, **kwargs):
        captured.append(kwargs)
        return process

    julia_steps.run_julia_step("fake", "code", project=tmp_path / "project",
                              environment={"JULIA_DEPOT_PATH": str(tmp_path / "depot")},
                              label="setup", status_cb=lambda _: None, popen=popen)
    assert captured[0]["stdin"] == subprocess.DEVNULL


def test_subprocess_resolves_all_relative_depots_against_launch_cwd(tmp_path):
    captured = []
    process = SimpleNamespace(stdout=io.StringIO(), wait=lambda: 0, poll=lambda: 0)

    def popen(command, **kwargs):
        captured.append((command, kwargs))
        return process

    separator = julia_steps.os.pathsep
    julia_steps.run_julia_step("fake", "code", project=Path("project"),
                              environment={"JULIA_DEPOT_PATH": separator.join(("first", "second"))},
                              label="setup", status_cb=lambda _: None, popen=popen)
    command, kwargs = captured[0]
    assert command[1] == f"--project={tmp_path / 'project'}"
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"]["JULIA_DEPOT_PATH"] == separator.join(str(tmp_path / entry) for entry in ("first", "second"))


@pytest.mark.parametrize("destination", ["project", "depot", "later_depot", "depot_symlink"])
def test_subprocess_destination_isolation_precedes_popen(tmp_path, monkeypatch, destination):
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(legacy))
    project, depot = tmp_path / "project", tmp_path / "depot"
    if destination == "project":
        project = legacy / "project"
    elif destination == "depot":
        depot = legacy / "depot"
    elif destination == "depot_symlink":
        depot.symlink_to(legacy, target_is_directory=True)
    effective = str(depot)
    if destination == "later_depot":
        effective += julia_steps.os.pathsep + str(legacy / "depot")

    def forbidden(*args, **kwargs):
        raise AssertionError("unsafe subprocess launched")

    with pytest.raises(paths.RootConflict):
        julia_steps.run_julia_step("fake", "code", project=project,
                                  environment={"JULIA_DEPOT_PATH": effective},
                                  label="setup", status_cb=lambda _: None, popen=forbidden)
    assert list(legacy.iterdir()) == []


def test_implicit_depot_list_entries_refused_before_subprocess(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("implicit depot subprocess launched")

    with pytest.raises(ValueError, match="Empty JULIA_DEPOT_PATH entries refused"):
        julia_steps.run_julia_step("fake", "code", project=tmp_path / "project",
                                  environment={"JULIA_DEPOT_PATH": str(tmp_path / "depot") + julia_steps.os.pathsep},
                                  label="setup", status_cb=lambda _: None, popen=forbidden)

from __future__ import annotations

import io
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from server.solver.beat_runtime import julia_steps


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

    options = dict(project=Path("CPU project"), environment={"JULIA_NUM_THREADS": "3"},
                   label="Instantiating", status_cb=lines.append, popen=popen)
    if returncode:
        with pytest.raises(RuntimeError, match="exit 2") as error:
            julia_steps.run_julia_step("selected Julia", "using Pkg", **options)
        assert "line 19\n" not in str(error.value)
        assert "line 20\n" in str(error.value) and "line 29" in str(error.value)
    else:
        julia_steps.run_julia_step("selected Julia", "using Pkg", **options)
    command, kw = captured[0]
    assert command == ["selected Julia", "--project=CPU project", "--startup-file=no", "-e", "using Pkg"]
    assert kw["env"] == options["environment"] and kw["errors"] == "replace"
    assert kw["stderr"] == subprocess.STDOUT
    assert lines[0] == "Instantiating" and len(lines) == 31
    assert process.stdout.closed


def test_callback_failure_does_not_interrupt_child(capsys):
    seen = []

    def fail(message):
        seen.append(message)
        raise RuntimeError("console")

    process = SimpleNamespace(stdout=io.StringIO("progress\n"), wait=lambda: 0, poll=lambda: 0)
    julia_steps.run_julia_step("fake", "code", project=Path("project"), environment={},
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
        julia_steps.run_julia_step("fake", "code", project=Path("project"), environment={},
                                  label="setup", status_cb=lambda _: None,
                                  popen=lambda *a, **kw: process)
    assert events == ["terminate", ("wait", 5), "kill", ("wait", None), "close"]

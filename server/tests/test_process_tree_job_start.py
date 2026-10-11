"""``popen_in_windows_job`` containment and refusal, with fakes on every OS."""
from __future__ import annotations

import contextlib
import io
import sys
import time
from types import SimpleNamespace as NS

import pytest

from server.platform import job_start, process_tree
from server.platform.job_start import ContainedStartError


@pytest.fixture
def windows(monkeypatch):
    events: list[tuple] = []
    children = []
    state = NS(job=object(), fired=True, error=None, assign=None)

    class Child:
        def __init__(self, command, **kwargs):
            self.command, self.kwargs = command, kwargs
            self.pid = 4242
            self.stdin, self.stdout, self.stderr = [io.BytesIO() for _ in range(3)]
            children.append(self)
            events.append(("popen", command, kwargs))
            if state.fired:
                state.started.job = state.assign(self.pid, 123)
                state.started.fired = True

        def kill(self):
            events.append(("kill", self.pid))

        def wait(self, timeout=None):
            events.append(("wait", timeout))
            return 1

    def confine(pid, *, subject):
        events.append(("assign", pid, subject))
        if state.error is not None:
            raise state.error
        return state.job

    @contextlib.contextmanager
    def start(assign, *, required, subject):
        events.append(("arm", required, subject))
        state.assign = assign
        state.started = job_start.JobStart()
        yield state.started
        events.append(("disarm",))

    monkeypatch.setattr(process_tree, "os", NS(name="nt"))
    monkeypatch.setattr(process_tree.subprocess, "Popen", Child)
    monkeypatch.setattr(process_tree, "confine_to_windows_job", confine)
    monkeypatch.setattr(job_start, "windows_job_start", start)
    return events, state, children


@pytest.mark.parametrize("kwargs", [{}, {"creationflags": 0x10}])
def test_windows_start_requires_a_job_and_preserves_creationflags(windows, kwargs):
    events, state, children = windows
    child, job = process_tree.popen_in_windows_job(["x"], subject="the check", **kwargs)
    assert job is state.started.job is state.job
    assert children == [child]
    assert child.kwargs == kwargs
    assert events == [("arm", True, "the check"), ("popen", ["x"], kwargs),
                      ("assign", child.pid, "the check"), ("disarm",)]


def test_an_unfired_start_is_confined_after_the_fact(windows):
    events, state, children = windows
    state.fired = False
    child, job = process_tree.popen_in_windows_job(["x"], subject="the check")
    assert job is state.job
    assert children == [child]
    assert events[-2:] == [("disarm",), ("assign", child.pid, "the check")]


@pytest.mark.parametrize("raises", [False, True])
def test_an_unfired_start_without_a_job_is_stopped_and_refused(windows, raises):
    events, state, children = windows
    state.fired = False
    state.job = None
    if raises:
        state.error = OSError("assignment refused")
    with pytest.raises(ContainedStartError, match="could not confine the check"):
        process_tree.popen_in_windows_job(["x"], subject="the check")
    child, = children
    assert events[-3:] == [("assign", child.pid, "the check"),
                           ("kill", child.pid), ("wait", 5)]
    assert all(stream.closed for stream in (child.stdin, child.stdout, child.stderr))


def test_non_windows_is_a_plain_popen_without_a_job(windows, monkeypatch):
    events, state, children = windows
    state.fired = False
    monkeypatch.setattr(process_tree, "os", NS(name="posix"))
    child, job = process_tree.popen_in_windows_job(["x"], subject="the check", stdout=-1)
    assert children == [child]
    assert job is None
    assert events == [("popen", ["x"], {"stdout": -1})]


@pytest.mark.skipif(sys.platform != "win32", reason="CancelSynchronousIo is Windows-only")
def test_a_read_blocked_on_a_held_pipe_is_cancelled():
    import os
    import threading

    read_end, write_end = os.pipe()  # this process keeps the writer: no EOF
    stream = os.fdopen(read_end, "rb", buffering=0)
    outcome = []
    def reader():
        try:
            outcome.append(stream.read(1))
        except OSError as exc:
            outcome.append(exc)
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        thread.join(0.2)
        assert thread.is_alive()
        started = time.monotonic()
        assert process_tree.cancel_blocked_reads([thread]) == []
        assert time.monotonic() - started < 1
        assert isinstance(outcome[0], OSError)
    finally:
        stream.close()
        os.close(write_end)


def test_elsewhere_blocked_readers_are_left_alone(monkeypatch):
    monkeypatch.setattr(process_tree, "os", NS(name="posix"))
    alive, gone = NS(is_alive=lambda: True), NS(is_alive=lambda: False)
    assert process_tree.cancel_blocked_reads([alive, gone]) == [alive]

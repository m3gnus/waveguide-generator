"""``popen_in_windows_job``'s order and fallbacks, with fakes on every OS.

The real two-level tree is exercised in ``test_bempp_opencl_lifecycle.py``.
"""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace as NS

import pytest

from server.platform import process_tree


@pytest.fixture
def windows(monkeypatch):
    events: list[tuple] = []

    class Child:
        def __init__(self, command, **kwargs):
            self.command, self.kwargs = command, kwargs
            self.pid = 4242 + len([e for e in events if e[0] == "popen"])
            self.stdin = self.stdout = self.stderr = None
            events.append(("popen", kwargs.get("creationflags", 0)))

        def kill(self):
            events.append(("kill", self.pid))

        def wait(self, timeout=None):
            return 1

    class Job:
        def terminate(self):
            events.append(("terminate",))

        def close(self):
            events.append(("close",))

    state = NS(job=Job(), resume=[True])
    def confine(pid, *, subject):
        events.append(("assign", pid))
        return state.job
    def resume(pid):
        result = state.resume.pop(0) if state.resume else False
        events.append(("resume", pid, result))
        return result
    monkeypatch.setattr(process_tree, "os", NS(name="nt"))
    monkeypatch.setattr(process_tree.subprocess, "Popen", Child)
    monkeypatch.setattr(process_tree, "confine_to_windows_job", confine)
    monkeypatch.setattr(process_tree, "_resume_windows_process", resume)
    monkeypatch.setattr(process_tree, "_RESUME_RETRY_SECONDS", 0)
    return events, state


def test_the_child_is_assigned_while_suspended_and_only_then_resumed(windows):
    events, state = windows
    child, job = process_tree.popen_in_windows_job(["x"], subject="the check", creationflags=0x10)
    assert job is state.job
    assert events == [("popen", 0x10 | process_tree._CREATE_SUSPENDED),
                      ("assign", child.pid), ("resume", child.pid, True)]


def test_without_a_job_the_child_still_runs(windows):
    events, state = windows
    state.job = None
    child, job = process_tree.popen_in_windows_job(["x"], subject="the check")
    assert job is None
    assert events[-1] == ("resume", child.pid, True)
    assert not any(event[0] == "kill" for event in events)


def test_a_transient_resume_failure_is_retried(windows):
    events, state = windows
    state.resume = [False, False, True]
    child, job = process_tree.popen_in_windows_job(["x"], subject="the check")
    assert job is state.job
    assert [e for e in events if e[0] == "resume"] == [
        ("resume", child.pid, False), ("resume", child.pid, False), ("resume", child.pid, True)]
    assert [e for e in events if e[0] == "popen"] == [("popen", process_tree._CREATE_SUSPENDED)]


def test_a_child_that_cannot_be_resumed_is_replaced_without_a_job(windows, caplog):
    events, state = windows
    state.resume = []
    child, job = process_tree.popen_in_windows_job(["x"], subject="the check", creationflags=0x10)
    assert job is None
    first = 4242
    assert events.count(("resume", first, False)) == process_tree._RESUME_ATTEMPTS
    assert ("terminate",) in events and ("close",) in events and ("kill", first) in events
    # The replacement is started the plain way, not suspended.
    assert events[-1] == ("popen", 0x10)
    assert child.pid != first
    assert "without a Windows job object" in caplog.text


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

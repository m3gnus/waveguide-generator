"""The native-source worker has a deadline, a Windows job and the Running mutex."""

import contextlib
import ctypes
from ctypes import wintypes
import io
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from server.cadlink.intake import native_source
from server.platform import process_tree, running_exclusion
from server.platform.job_start import ContainedStartError

_STAGE = Path("unused-stage")


class FakeChild:
    def __init__(self, returncode=0, stderr="", errors=()):
        self.returncode = returncode
        self.stdin = None
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.output = ("worker output", stderr)
        self.errors = iter(errors)
        self.timeouts = []

    def communicate(self, *, timeout):
        self.timeouts.append(timeout)
        error = next(self.errors, None)
        if error is not None:
            raise error
        return self.output

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class FakeJob:
    def __init__(self):
        self.calls = []

    def terminate(self):
        self.calls.append("terminate")

    def close(self):
        self.calls.append("close")


def test_worker_uses_contained_start_and_cleans_up_after_success(monkeypatch):
    child, job = FakeChild(), FakeJob()
    starts = []

    def start(command, **kwargs):
        starts.append((command, kwargs))
        return child, job

    monkeypatch.setattr(process_tree, "popen_in_windows_job", start)
    result = native_source._run_worker(_STAGE)

    assert starts == [(native_source._worker_command(_STAGE), {
        "subject": "the native-source worker",
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    })]
    assert child.timeouts == [native_source.isolation.MESH_BUDGET.wall_time_s]
    assert result.returncode == 0 and result.stdout == "worker output"
    assert job.calls == ["terminate", "close"]
    assert child.stdout.closed and child.stderr.closed


def test_contained_start_refusal_becomes_ingestion_value_error(monkeypatch):
    starts = []

    def refuse(command, **kwargs):
        starts.append(command)
        raise ContainedStartError("could not confine the native-source worker")

    monkeypatch.setattr(process_tree, "popen_in_windows_job", refuse)
    with pytest.raises(ValueError) as raised:
        native_source._run_worker(_STAGE)

    assert str(raised.value) == (
        "native source ingestion failed: could not confine the native-source worker"
    )
    assert starts == [native_source._worker_command(_STAGE)]


def test_timeout_stops_and_reaps_a_real_worker(monkeypatch):
    children = []
    real_start = process_tree.popen_in_windows_job

    def start(command, **kwargs):
        child, job = real_start(command, **kwargs)
        children.append(child)
        return child, job

    monkeypatch.setattr(process_tree, "popen_in_windows_job", start)
    monkeypatch.setattr(native_source, "_worker_command", lambda stage: [
        sys.executable, "-c", "import time; time.sleep(60)",
    ])
    monkeypatch.setattr(native_source, "_WORKER_TIMEOUT_S", 0.1)
    began = time.monotonic()
    try:
        with pytest.raises(ValueError, match="ingestion failed:.*0.1 second deadline"):
            native_source._run_worker(_STAGE)
        assert time.monotonic() - began < 10
        assert len(children) == 1 and children[0].poll() is not None
        assert children[0].stdout.closed and children[0].stderr.closed
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


@pytest.mark.parametrize("error", [OSError("broken pipe"), KeyboardInterrupt()])
def test_communicate_exception_still_terminates_and_closes(monkeypatch, error):
    child, job = FakeChild(returncode=None, errors=[error]), FakeJob()
    monkeypatch.setattr(process_tree, "popen_in_windows_job", lambda *a, **kw: (child, job))
    with pytest.raises(type(error)) as raised:
        native_source._run_worker(_STAGE)
    assert raised.value is error
    assert child.poll() is not None
    assert job.calls == ["terminate", "close"]
    assert child.stdout.closed and child.stderr.closed


def test_second_communicate_timeout_is_bounded_and_suppressed(monkeypatch):
    child = FakeChild(errors=[subprocess.TimeoutExpired("worker", 0.1)] * 2)
    job = FakeJob()
    monkeypatch.setattr(process_tree, "popen_in_windows_job", lambda *a, **kw: (child, job))
    monkeypatch.setattr(native_source, "_WORKER_TIMEOUT_S", 0.1)
    with pytest.raises(ValueError, match="ingestion failed:.*0.1 second deadline"):
        native_source._run_worker(_STAGE)
    assert child.timeouts == [0.1, 5]
    assert job.calls == ["terminate", "close"]
    assert child.stdout.closed and child.stderr.closed


@pytest.mark.parametrize(("stderr", "message"), [
    (
        "Traceback\nRuntimeError: Solver mesh uses 123 DOFs, exceeding the safety ceiling\n",
        "Solver mesh uses 123 DOFs, exceeding the safety ceiling",
    ),
    ("gmsh failed\n", "native source ingestion failed: gmsh failed\n"),
])
def test_nonzero_exit_preserves_refusal_text(monkeypatch, stderr, message):
    child, job = FakeChild(returncode=1, stderr=stderr), FakeJob()
    monkeypatch.setattr(process_tree, "popen_in_windows_job", lambda *a, **kw: (child, job))
    with pytest.raises(ValueError) as raised:
        native_source._run_worker(_STAGE)
    assert str(raised.value) == message
    assert job.calls == ["terminate", "close"]
    assert child.stdout.closed and child.stderr.closed


def test_worker_holds_running_exclusion_before_work(monkeypatch):
    calls = []
    monkeypatch.setattr(running_exclusion, "hold_running_exclusion", lambda: calls.append("hold"))
    monkeypatch.setattr(native_source, "_worker", lambda stage: calls.append(("worker", stage)))
    native_source._worker_main([str(_STAGE)])
    assert calls == ["hold", ("worker", _STAGE)]


def test_running_exclusion_refusal_prevents_work(monkeypatch):
    calls = []
    refusal = running_exclusion.RunningExclusionError(5)

    def hold():
        calls.append("hold")
        raise refusal

    monkeypatch.setattr(running_exclusion, "hold_running_exclusion", hold)
    monkeypatch.setattr(native_source, "_worker", lambda stage: calls.append("worker"))
    # Even argument access must follow holding the mutex.
    with pytest.raises(running_exclusion.RunningExclusionError) as raised:
        native_source._worker_main([])
    assert raised.value is refusal and calls == ["hold"]


def _process_api():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _open_running_process(kernel32, pid):
    handle = kernel32.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, pid)
    assert handle, "could not retain the process handle"
    try:
        code = wintypes.DWORD()
        assert kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        assert code.value == 259, "the process never became alive"
        return handle
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _stop_and_close(kernel32, handle):
    try:
        if kernel32.WaitForSingleObject(handle, 0) == 258:
            assert kernel32.TerminateProcess(handle, 1)
            assert kernel32.WaitForSingleObject(handle, 5000) == 0
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
@pytest.mark.parametrize("timeout", [True, False])
def test_real_worker_job_stops_worker_and_grandchild(monkeypatch, tmp_path, timeout):
    kernel32 = _process_api()
    pid_file, release = tmp_path / "grandchild.pid", tmp_path / "release"
    script = (
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys._base_executable, '-c', 'import time; time.sleep(45)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "target = Path(sys.argv[1])\n"
        "temporary = target.with_suffix('.tmp')\n"
        "temporary.write_text(str(child.pid), encoding='utf-8')\n"
        "temporary.replace(target)\n"
        "deadline = time.monotonic() + 45\n"
        "while time.monotonic() < deadline and not Path(sys.argv[2]).exists():\n"
        "    time.sleep(0.02)\n"
    )
    monkeypatch.setattr(native_source, "_worker_command", lambda stage: [
        sys._base_executable, "-c", script, str(pid_file), str(release),
    ])
    monkeypatch.setattr(native_source, "_WORKER_TIMEOUT_S", 4)
    real_start = process_tree.popen_in_windows_job
    children, jobs, handles, outcomes = [], [], [], []

    def start(command, **kwargs):
        child, job = real_start(command, **kwargs)
        children.append(child)
        jobs.append(job)
        return child, job

    def run():
        try:
            outcomes.append(native_source._run_worker(tmp_path))
        except BaseException as exc:
            outcomes.append(exc)

    monkeypatch.setattr(process_tree, "popen_in_windows_job", start)
    thread = threading.Thread(target=run, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 3
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.exists(), "the worker never reported its grandchild"
        grandchild_pid = int(pid_file.read_text(encoding="utf-8"))
        handles.append(_open_running_process(kernel32, children[0].pid))
        handles.append(_open_running_process(kernel32, grandchild_pid))
        assert len(jobs) == 1 and jobs[0] is not None
        assert jobs[0].contains(children[0].pid) and jobs[0].contains(grandchild_pid)
        if not timeout:
            release.touch()
        thread.join(timeout=7)
        assert not thread.is_alive(), "the ingest helper did not finish"
        assert len(outcomes) == 1
        if timeout:
            assert isinstance(outcomes[0], ValueError)
            assert "ingestion failed" in str(outcomes[0]) and "4 second deadline" in str(outcomes[0])
        else:
            assert isinstance(outcomes[0], subprocess.CompletedProcess)
            assert outcomes[0].returncode == 0
        for handle in handles:
            assert kernel32.WaitForSingleObject(handle, 1000) == 0, "a job member survived"
        assert children[0].poll() is not None
        assert children[0].stdout.closed and children[0].stderr.closed
    finally:
        # Do not close the helper's job from another thread; stop only retained
        # process identities, then let the helper finish its own cleanup.
        for handle in reversed(handles):
            _stop_and_close(kernel32, handle)
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        if thread.ident is not None:
            thread.join(timeout=7)


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
def test_failed_job_assignment_refuses_and_leaves_no_worker(monkeypatch, tmp_path):
    kernel32 = _process_api()
    handles, instances = [], []
    original = subprocess.Popen

    class ObservedPopen(original):
        def __init__(self, *args, **kwargs):
            try:
                super().__init__(*args, **kwargs)
            finally:
                instances.append(self)

    def refuse(pid, *, subject):
        assert subject == "the native-source worker"
        handles.append(_open_running_process(kernel32, pid))
        return None

    monkeypatch.setattr(native_source, "_worker_command", lambda stage: [
        sys._base_executable, "-c", "import time; time.sleep(45)",
    ])
    monkeypatch.setattr(process_tree.subprocess, "Popen", ObservedPopen)
    monkeypatch.setattr(process_tree, "confine_to_windows_job", refuse)
    try:
        with pytest.raises(ValueError, match="^native source ingestion failed:.*could not confine"):
            native_source._run_worker(tmp_path)
        assert len(handles) == len(instances) == 1
        assert kernel32.WaitForSingleObject(handles[0], 5000) == 0
        assert instances[0].stdout.closed and instances[0].stderr.closed
    finally:
        for handle in handles:
            _stop_and_close(kernel32, handle)
        for child in instances:
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    with contextlib.suppress(Exception):
                        stream.close()

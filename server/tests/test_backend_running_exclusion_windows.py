"""Installer probes keep seeing a backend after its multiprocessing child exits."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import site
import socket
import sys
import sysconfig
import threading
import time
from types import SimpleNamespace
import urllib.request
from uuid import uuid4

import pytest

from server.platform import backend_owner
from server.solver.beat_runtime import retire
from server.tests.backend_breakaway import require_backend_breakaway

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer running exclusion")
ROOT = Path(__file__).resolve().parents[2]
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_FILE_NOT_FOUND = 2


def _until(check, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.02)
    pytest.fail(message)


class _NativeProbes:
    def __init__(self):
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "OpenMutexW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "SetEvent": ([wintypes.HANDLE], wintypes.BOOL),
            "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel32, name)
            function.argtypes = arguments
            function.restype = result

    def alive(self, pid):
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            assert error == 87, f"OpenProcess({pid}) failed: Win32 error {error}"
            return False  # ERROR_INVALID_PARAMETER: this PID has exited.
        try:
            code = wintypes.DWORD()
            assert self.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == STILL_ACTIVE
        finally:
            self.kernel32.CloseHandle(handle)

    def mutex(self, name):
        # Match the installer exactly. Never retain a probe handle between samples.
        handle = self.kernel32.OpenMutexW(SYNCHRONIZE, False, name)
        error = ctypes.get_last_error()
        if handle:
            assert self.kernel32.CloseHandle(handle)
        return bool(handle), error

    def terminate(self, pid):
        # Only a PID recorded by this fixture's harness is passed here.
        if not self.alive(pid):
            return
        handle = self.kernel32.OpenProcess(0x0001 | SYNCHRONIZE, False, pid)
        if not handle:
            assert not self.alive(pid)
            return
        try:
            self.kernel32.TerminateProcess(handle, 1)
            assert self.kernel32.WaitForSingleObject(handle, 5000) == 0
        finally:
            self.kernel32.CloseHandle(handle)


@pytest.fixture(params=["headless", "contained"])
def backend(request, tmp_path, retire_event):
    lifetime = request.param
    if lifetime == "headless":
        require_backend_breakaway()
    probes = _NativeProbes()
    name = f"WaveguideGeneratorTestRunning-{os.getpid()}-{uuid4().hex}"
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    pid_file = tmp_path / "child.pid"
    control = retire.RetireSignal()
    assert control.error is None
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    packages = list(dict.fromkeys([
        *site.getsitepackages(),
        sysconfig.get_path("purelib"),
        *(path for path in sys.path
          if Path(path).name.lower() == "site-packages" and Path(path).is_dir()),
    ]))
    shim = "import site\n" + "".join(f"site.addsitedir({path!r})\n" for path in packages)
    (tmp_path / "sitecustomize.py").write_text(shim, encoding="utf-8")
    env = dict(os.environ)
    for key in ("WG2_BUNDLE", "WG2_DATA_DIR", "WG2_APP_ROOT", "WG2_PORT",
                "WG2_UPDATE_STAGING_ROOT", "WG2_BACKEND_CONTAINED"):
        env.pop(key, None)
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "AppData"),
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(ROOT)]),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "WG2_TEST_RUNNING_MUTEX": name,
        "WG2_TEST_RETIRE_EVENT": retire_event, "WG2_TEST_CHILD_PID_FILE": str(pid_file),
        "WG2_TEST_EXIT_LINGER": "3", "WG2_SOLVER_WARMUP": "0",
        "WG2_SKIP_BEAT_CPU_PROVISION": "1", "WG2_WGLINK_REFRESH": "0",
        "WG2_NO_BROWSER": "1", "WG2_ENABLE_DRYRUN": "0",
    })
    process = watchdog = None
    child_pid = None
    expired = threading.Event()
    try:
        # Avoid the venv redirector's job; sitecustomize restores its packages.
        argv = [
            sys._base_executable, str(ROOT / "server/tests/running_backend_main.py"),
            "--lifetime=headless", "--no-browser", "--data-dir", str(data),
            "--port", str(port), "--idle-timeout", "600",
        ]
        process, log = backend_owner._spawn_attempt(argv, data, env, contained=lifetime == "contained")

        def timeout():
            expired.set()
            backend_owner._terminate_child(process)

        watchdog = threading.Timer(115.0, timeout)
        watchdog.daemon = True
        watchdog.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def healthy():
            assert probes.alive(process.pid), log.read_text(encoding="utf-8", errors="replace")
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    health = json.load(response)
                    return response.status == 200 and health.get("lifetime") == lifetime
            except (OSError, ValueError):
                return False

        _until(healthy, 90, "backend never became healthy")
        _until(pid_file.is_file, 5, "backend did not publish its child PID")
        child_pid = int(pid_file.read_text(encoding="utf-8"))
        assert probes.alive(child_pid), "mutex-holding child exited before retirement"
        assert probes.mutex(name)[0], "backend and child did not hold the test mutex"
        yield SimpleNamespace(
            process=process, child_pid=child_pid, probes=probes, name=name,
            signal=lambda: probes.kernel32.SetEvent(control._handle), log=log,
        )
        assert not expired.is_set(), "backend test exceeded its hard overall timeout"
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join(timeout=3)
        if process is not None:
            backend_owner._terminate_child(process)
            job = getattr(process, "_wg_start_job", None)
            if job is not None:
                job.close()
        # The job covers the child even if startup failed before PID publication.
        if child_pid is None and pid_file.is_file():
            child_pid = int(pid_file.read_text(encoding="utf-8"))
        if child_pid is not None:
            probes.terminate(child_pid)
        control.close()


def test_backend_holds_running_until_interpreter_exit(backend):
    process, probes = backend.process, backend.probes
    assert backend.signal()
    deadline = time.monotonic() + 20
    samples = 0
    child_gone = False
    while time.monotonic() < deadline:
        child_alive = probes.alive(backend.child_pid)
        # Probe before backend liveness: a process exiting between those calls
        # must not look like a live interpreter that released its mutex early.
        mutex_open = probes.mutex(backend.name)[0] if not child_alive else False
        backend_alive = probes.alive(process.pid)
        if not child_alive:
            if not child_gone:
                assert backend_alive, "child did not exit before the backend interpreter"
            child_gone = True
            if backend_alive:
                assert mutex_open, "child gone, backend alive, mutex released"
                samples += 1
        if not backend_alive:
            break
        time.sleep(0.015)
    else:
        pytest.fail("backend did not exit within the retirement deadline")
    assert child_gone and samples > 0, "never sampled a child exit while the backend was alive"
    process.wait(timeout=5)
    assert process.returncode == 0, backend.log.read_text(encoding="utf-8", errors="replace")

    def released():
        opened, error = probes.mutex(backend.name)
        if opened:
            return False
        assert error == ERROR_FILE_NOT_FOUND, f"OpenMutexW failed: Win32 error {error}"
        return True

    _until(released, 5, "running mutex remained after backend interpreter exit")

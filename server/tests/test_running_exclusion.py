"""Bundled children keep the installer out until their interpreter exits."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import queue
import site
import subprocess
import sys
import sysconfig
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from server.platform import running_exclusion


@pytest.fixture(autouse=True)
def reset_handle(monkeypatch):
    monkeypatch.setattr(running_exclusion, "_handle", None)


@pytest.fixture
def kernel32(monkeypatch):
    fake = SimpleNamespace(handle=0x100000001, error=0, calls=[], loads=[])

    def create_mutex(*args):
        fake.calls.append(args)
        return fake.handle

    def load(name, *, use_last_error):
        fake.loads.append((name, use_last_error))
        return fake

    fake.CreateMutexW = create_mutex
    monkeypatch.setattr(running_exclusion.ctypes, "WinDLL", load, raising=False)
    monkeypatch.setattr(
        running_exclusion.ctypes, "get_last_error", lambda: fake.error, raising=False
    )
    return fake


def test_non_windows_makes_no_win32_call(monkeypatch, kernel32):
    # Replace the module reference so pathlib and pytest keep their native OS.
    monkeypatch.setattr(running_exclusion, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(sys, "_base_executable", "wg-python.exe", raising=False)

    assert running_exclusion.hold_running_exclusion() is False
    assert kernel32.loads == kernel32.calls == []
    assert running_exclusion._handle is None


@pytest.mark.parametrize("executable", ["python.exe", "", None, "missing"])
def test_windows_non_bundle_makes_no_win32_call(monkeypatch, kernel32, executable):
    monkeypatch.setattr(running_exclusion, "os", SimpleNamespace(name="nt"))
    if executable == "missing":
        monkeypatch.delattr(sys, "_base_executable", raising=False)
    else:
        monkeypatch.setattr(sys, "_base_executable", executable, raising=False)

    assert running_exclusion.hold_running_exclusion() is False
    assert kernel32.loads == kernel32.calls == []
    assert running_exclusion._handle is None


@pytest.mark.parametrize("winerror", [0, 183])
def test_bundle_holds_once_including_existing_mutex(monkeypatch, kernel32, winerror):
    monkeypatch.setattr(running_exclusion, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(sys, "_base_executable", "WG-Python.EXE", raising=False)
    kernel32.error = winerror

    assert running_exclusion.hold_running_exclusion() is True
    assert running_exclusion._handle == kernel32.handle
    assert running_exclusion.hold_running_exclusion() is True
    assert kernel32.loads == [("kernel32", True)]
    assert kernel32.calls == [(None, False, "WaveguideGeneratorRunning")]
    assert kernel32.CreateMutexW.argtypes == [
        wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR
    ]
    assert kernel32.CreateMutexW.restype is wintypes.HANDLE


def test_null_handle_logs_and_refuses_to_run(monkeypatch, kernel32, caplog):
    monkeypatch.setattr(running_exclusion, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(sys, "_base_executable", "wg-python.exe", raising=False)
    kernel32.handle = None
    kernel32.error = 5

    with caplog.at_level(logging.ERROR, logger=running_exclusion.__name__):
        with pytest.raises(running_exclusion.RunningExclusionError) as raised:
            running_exclusion.hold_running_exclusion()

    assert raised.value.winerror == 5
    assert "Win32 error 5" in str(raised.value)
    assert running_exclusion._handle is None
    assert kernel32.calls == [(None, False, "WaveguideGeneratorRunning")]
    assert any(
        record.name == running_exclusion.__name__
        and record.levelno == logging.ERROR
        and "Win32 error 5" in record.getMessage()
        for record in caplog.records
    )


def test_mutex_name_matches_launcher_and_installer_without_admission_check():
    root = Path(__file__).resolve().parents[2]
    name = running_exclusion.RUNNING_MUTEX_NAME
    launcher = (root / "launchers/windows/launcher.c").read_text(encoding="utf-8")
    installer = (root / "installers/windows/bundle-setup.iss").read_text(encoding="utf-8")

    assert f'CreateMutexW(&sa, FALSE, L"{name}")' in launcher
    assert f"OpenMutexW(SYNCHRONIZE, 0, '{name}')" in installer
    source = Path(running_exclusion.__file__).read_text(encoding="utf-8")
    assert "WaveguideGeneratorSetup" not in source


@pytest.mark.skipif(os.name != "nt", reason="native Windows named mutex lifetime")
def test_native_child_keeps_mutex_alive_until_exit(tmp_path):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    name = f"WaveguideGeneratorRunningTest-{os.getpid()}-{uuid4().hex}"
    root = Path(__file__).resolve().parents[2]
    packages = list(dict.fromkeys([*site.getsitepackages(), sysconfig.get_path("purelib")]))
    shim = "import site\n" + "".join(f"site.addsitedir({path!r})\n" for path in packages)
    (tmp_path / "sitecustomize.py").write_text(shim, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(tmp_path), str(root)]),
               PYTHONDONTWRITEBYTECODE="1")
    script = (
        "import sys\n"
        "from server.platform.running_exclusion import _hold\n"
        f"_hold({name!r})\n"
        "print('ready', flush=True)\n"
        "sys.stdin.read()\n"
    )
    parent_handle = kernel32.CreateMutexW(None, False, name)
    assert parent_handle, f"CreateMutexW failed: {ctypes.get_last_error()}"
    child = None
    reader = None
    try:
        # The venv's python.exe is a redirector with its own job; use the real
        # interpreter and let sitecustomize restore this venv's packages.
        child = subprocess.Popen(
            [sys._base_executable, "-c", script], cwd=root, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
        reader.start()
        try:
            line = ready.get(timeout=30)
        except queue.Empty:
            pytest.fail("child did not announce its mutex handle within 30 seconds")
        if line != "ready\n":
            child.wait(timeout=30)
            pytest.fail(f"child did not hold the mutex: {line!r}\n{child.stderr.read()}")
        assert kernel32.CloseHandle(parent_handle)
        parent_handle = None

        held = kernel32.OpenMutexW(0x00100000, False, name)  # SYNCHRONIZE
        try:
            assert held, "the mutex vanished when only the child should hold it"
        finally:
            if held:
                kernel32.CloseHandle(held)

        child.stdin.close()
        child.wait(timeout=30)
        assert child.returncode == 0, child.stderr.read()
        gone = kernel32.OpenMutexW(0x00100000, False, name)
        winerror = ctypes.get_last_error()
        if gone:
            kernel32.CloseHandle(gone)
        assert not gone
        assert winerror == 2  # ERROR_FILE_NOT_FOUND
    finally:
        if parent_handle:
            kernel32.CloseHandle(parent_handle)
        if child is not None:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=30)
            if reader is not None:
                reader.join(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()

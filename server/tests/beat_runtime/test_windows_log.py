from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from server.solver.beat_runtime import registry as r
from server.tests.beat_runtime.fake_host_worker import wait_until


@pytest.mark.parametrize("create", [False, True])
@pytest.mark.parametrize("outcome", ["success", "wrap_error", "wrap_invalid", "open_error"])
def test_windows_append_handle_access_and_ownership(tmp_path, monkeypatch, create, outcome):
    calls = []
    handle = ctypes.c_void_p(-1).value if outcome == "open_error" else 99

    class Function:
        def __init__(self, action):
            self.action = action

        def __call__(self, *args):
            return self.action(*args)

    kernel = SimpleNamespace(
        CreateFileW=Function(lambda *args: calls.append(("open", args)) or handle),
        CloseHandle=Function(lambda value: calls.append(("close", value)) or True),
    )

    def wrap(value, flags):
        calls.append(("wrap", value, flags))
        if outcome == "wrap_error":
            raise OSError("fixture wrap failure")
        return -1 if outcome == "wrap_invalid" else 42

    def library(name, **options):
        assert name == "kernel32" and options == {"use_last_error": True}
        return kernel

    monkeypatch.setattr(ctypes, "WinDLL", library, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda error: OSError(error, "fixture open failure"), raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=wrap))
    path = tmp_path / "host.log"
    if outcome == "success":
        assert r._windows_append_fd(path, create=create) == 42
    else:
        with pytest.raises(OSError):
            r._windows_append_fd(path, create=create)

    _, args = calls[0]
    assert args == (str(path), 0x100084, 0x7, None, 4 if create else 3, 0x200080, None)
    assert args[1] & 0x4  # FILE_APPEND_DATA
    assert args[1] & 0x80  # FILE_READ_ATTRIBUTES permits the existing fstat safety checks.
    assert not args[1] & 0x2  # FILE_WRITE_DATA would allow inherited stderr to overwrite.
    assert kernel.CreateFileW.argtypes == [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                          w.DWORD, w.DWORD, w.HANDLE]
    assert kernel.CreateFileW.restype is w.HANDLE
    assert kernel.CloseHandle.argtypes == [w.HANDLE]
    assert kernel.CloseHandle.restype is w.BOOL
    if outcome == "open_error":
        assert len(calls) == 1  # INVALID_HANDLE_VALUE must not be wrapped or closed.
    else:
        assert calls[1] == ("wrap", 99, os.O_WRONLY | os.O_APPEND)
        assert calls[2:] == ([] if outcome == "success" else [("close", 99)])


@pytest.mark.parametrize("failure", ["root", "file", "opened"])
def test_windows_append_reuses_private_checks_and_closes_rejected_fd(tmp_path, monkeypatch, failure):
    root = r.private_directory(tmp_path / "registry")
    path = root / "host.log"
    with r._private_file(path, create=True):
        pass
    checks, opened = [], []

    def check(candidate):
        checks.append(candidate)
        if ((failure == "root" and candidate == root)
                or (failure == "file" and candidate == path)
                or (failure == "opened" and checks.count(path) == 2)):
            raise ValueError("fixture unsafe owner/DACL")

    def append_fd(candidate, *, create):
        assert candidate == path and not create
        fd = os.open(candidate, os.O_WRONLY | os.O_APPEND)
        opened.append(fd)
        return fd

    monkeypatch.setattr(r, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(r, "_windows_security", lambda: SimpleNamespace(check_path=check))
    monkeypatch.setattr(r, "_windows_append_fd", append_fd)
    with pytest.raises(r.RecordRefused, match="unsafe owner/DACL"):
        with r._private_file(path, append=True):
            pytest.fail("unsafe append fd was yielded")
    assert len(opened) == (1 if failure == "opened" else 0)
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows inherited append handles")
def test_windows_inherited_output_and_private_appends_survive_truncation(tmp_path):
    root = r.private_directory(tmp_path / "registry")
    log = root / "host.log"
    with r._private_file(log, create=True) as fd:
        os.write(fd, b"old run\n")
    # Match host startup: truncate after stdout/stderr have already been inherited.
    script = """
import os, sys
from pathlib import Path
from server.solver.beat_runtime import registry as r
log = Path(sys.argv[1])
with r._private_file(log, create=True) as fd:
    os.ftruncate(fd, 0)
os.write(1, b'ready\\n')
for index, line in enumerate(sys.stdin):
    with r._private_file(log, create=True, append=True) as fd:
        os.write(fd, f'host {index}\\n'.encode())
    os.write(1, f'stdout {index}\\n'.encode())
    os.write(2, f'stderr {index}\\n'.encode())
"""
    expected = ["ready"]
    with r._private_file(log, append=True) as inherited_fd:
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(log)], cwd=Path(__file__).resolve().parents[3],
            stdin=subprocess.PIPE, stdout=inherited_fd, stderr=subprocess.STDOUT,
            close_fds=True, text=True,
        )
    try:
        wait_until(lambda: "ready\n" in log.read_text())
        with r._private_file(log, append=True) as parent_fd:
            for index in range(64):
                os.write(parent_fd, f"parent {index}\n".encode())
                process.stdin.write("go\n")
                process.stdin.flush()
                wait_until(lambda: f"stderr {index}\n" in log.read_text())
                expected.extend([f"parent {index}", f"host {index}",
                                 f"stdout {index}", f"stderr {index}"])
        process.stdin.close()
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()  # Only this test's recorded child.
        process.wait(timeout=5)
        if not process.stdin.closed:
            process.stdin.close()
    assert log.read_text().splitlines() == expected

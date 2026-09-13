"""Physical-memory detection, including the Windows query, exercised on any host.

The Windows path cannot run natively off Windows, so these tests stand in for
``kernel32.GlobalMemoryStatusEx`` with a fake that writes into the real
``MEMORYSTATUSEX`` structure the provider allocates. The real query runs in
``test_this_host_reports_its_physical_memory`` on the Windows CI runner.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from server.platform import memory
from server.platform.memory import PhysicalMemory, physical_memory


GIB = 1024**3


class _FakeGlobalMemoryStatusEx:
    """``kernel32.GlobalMemoryStatusEx``: fills the caller's structure or fails."""

    def __init__(self, total_bytes: int | None) -> None:
        self.total_bytes = total_bytes
        self.argtypes = None
        self.restype = None
        self.lengths_seen: list[int] = []

    def __call__(self, status_pointer) -> int:
        status = status_pointer.contents
        self.lengths_seen.append(int(status.dwLength))
        if self.total_bytes is None:
            return 0
        status.ullTotalPhys = self.total_bytes
        return 1


def _fake_kernel32(
    monkeypatch: pytest.MonkeyPatch, total_bytes: int | None
) -> tuple[_FakeGlobalMemoryStatusEx, list[str]]:
    query = _FakeGlobalMemoryStatusEx(total_bytes)
    loaded: list[str] = []

    def win_dll(name: str, **_kwargs: object) -> SimpleNamespace:
        loaded.append(name)
        return SimpleNamespace(GlobalMemoryStatusEx=query)

    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)
    return query, loaded


def _fake_sysconf(
    monkeypatch: pytest.MonkeyPatch, *, page_size: int, pages: int
) -> None:
    values = {"SC_PAGE_SIZE": page_size, "SC_PHYS_PAGES": pages}
    monkeypatch.setattr(os, "sysconf", lambda name: values[name], raising=False)


def _failing_sysconf(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def sysconf(_name: str) -> int:
        raise error

    monkeypatch.setattr(os, "sysconf", sysconf, raising=False)


def _no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("this platform must not shell out for its memory size")

    monkeypatch.setattr(subprocess, "run", run)


def test_memorystatusex_has_the_win32_layout() -> None:
    # Two DWORDs, then seven DWORDLONGs: 64 bytes, total physical at offset 8.
    # A DWORD spelled c_ulong would be 8 bytes on LP64 hosts and hide a layout
    # bug everywhere except Windows.
    assert ctypes.sizeof(memory._MemoryStatusEx) == 64
    assert memory._MemoryStatusEx.ullTotalPhys.offset == 8


def test_windows_reads_total_physical_memory_from_globalmemorystatusex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query, loaded = _fake_kernel32(monkeypatch, 32 * GIB)
    # Windows has no os.sysconf; a probe that consulted it would fail there.
    monkeypatch.delattr(os, "sysconf", raising=False)

    result = physical_memory(system="Windows")

    assert result == PhysicalMemory(bytes=32 * GIB, source="GlobalMemoryStatusEx", known=True)
    assert loaded == ["kernel32"]
    # The API refuses a structure whose dwLength is not its own size.
    assert query.lengths_seen == [64]
    assert query.argtypes == [ctypes.POINTER(memory._MemoryStatusEx)]


def test_windows_query_failure_is_unknown_not_a_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_kernel32(monkeypatch, None)

    result = physical_memory(system="Windows")

    assert result.known is False
    assert result.bytes is None
    assert result.source == "unknown"
    assert "GlobalMemoryStatusEx" in result.detail


def test_windows_without_a_loadable_kernel32_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def win_dll(_name: str, **_kwargs: object) -> None:
        raise OSError("kernel32 could not be loaded")

    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)

    result = physical_memory(system="Windows")

    assert (result.known, result.bytes) == (False, None)
    assert "kernel32 could not be loaded" in result.detail


def test_windows_zero_total_is_not_a_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_kernel32(monkeypatch, 0)

    assert physical_memory(system="Windows").known is False


def test_an_unexpected_probe_error_degrades_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Memory detection feeds admission; it must never take a mesh build down.
    def win_dll(_name: str, **_kwargs: object) -> None:
        raise RuntimeError("unexpected ctypes failure")

    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)

    result = physical_memory(system="Windows")

    assert result.known is False
    assert "unexpected ctypes failure" in result.detail


def test_linux_uses_sysconf(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_sysconf(monkeypatch, page_size=4096, pages=(16 * GIB) // 4096)
    _no_subprocess(monkeypatch)

    assert physical_memory(system="Linux") == PhysicalMemory(
        bytes=16 * GIB, source="sysconf", known=True
    )


@pytest.mark.parametrize(
    "error", [ValueError("unrecognized configuration name"), OSError("EINVAL")]
)
def test_linux_sysconf_failure_is_unknown(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _failing_sysconf(monkeypatch, error)
    _no_subprocess(monkeypatch)

    result = physical_memory(system="Linux")

    assert (result.known, result.bytes, result.source) == (False, None, "unknown")
    assert "sysconf" in result.detail


def test_indeterminate_sysconf_is_not_a_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    # sysconf reports -1 for a limit it cannot determine.
    _fake_sysconf(monkeypatch, page_size=4096, pages=-1)
    _no_subprocess(monkeypatch)

    assert physical_memory(system="Linux").known is False


def test_macos_falls_back_to_sysctl_hw_memsize(monkeypatch: pytest.MonkeyPatch) -> None:
    _failing_sysconf(monkeypatch, ValueError("unrecognized configuration name"))
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        assert kwargs.get("timeout"), "the fallback must not be able to hang a build"
        return subprocess.CompletedProcess(args, 0, stdout="8589934592\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    assert physical_memory(system="Darwin") == PhysicalMemory(
        bytes=8 * GIB, source="sysctl hw.memsize", known=True
    )
    assert calls == [["/usr/sbin/sysctl", "-n", "hw.memsize"]]


def test_macos_prefers_sysconf_when_it_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_sysconf(monkeypatch, page_size=16384, pages=(24 * GIB) // 16384)
    _no_subprocess(monkeypatch)

    assert physical_memory(system="Darwin").source == "sysconf"


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.TimeoutExpired(["sysctl"], 5.0),
        subprocess.CalledProcessError(1, ["sysctl"]),
        FileNotFoundError("sysctl"),
        "not a number\n",
    ],
    ids=["timeout", "nonzero-exit", "missing", "garbage"],
)
def test_macos_with_both_probes_failing_is_unknown(
    monkeypatch: pytest.MonkeyPatch, outcome: object
) -> None:
    _failing_sysconf(monkeypatch, OSError("sysconf failed"))

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(args, 0, stdout=outcome, stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    result = physical_memory(system="Darwin")

    assert (result.known, result.bytes, result.source) == (False, None, "unknown")
    assert "sysconf" in result.detail and "sysctl" in result.detail


def test_to_dict_reports_bytes_source_and_known() -> None:
    assert PhysicalMemory(bytes=None, source="unknown", known=False).to_dict() == {
        "bytes": None,
        "source": "unknown",
        "known": False,
    }


def test_this_host_reports_its_physical_memory() -> None:
    """The real probe for the running platform, with nothing injected.

    On the Windows CI runner this is the only test that calls the real
    ``GlobalMemoryStatusEx``; everywhere else it calls the real ``sysconf``.
    """

    result = physical_memory()

    expected_source = "GlobalMemoryStatusEx" if sys.platform == "win32" else "sysconf"
    assert result.known is True, result.detail
    assert result.source == expected_source
    assert result.bytes is not None and result.bytes >= 1 * GIB


@pytest.mark.skipif(sys.platform != "darwin", reason="hw.memsize is a macOS sysctl")
def test_macos_sysctl_fallback_agrees_with_sysconf_on_this_host() -> None:
    assert memory._sysctl_memsize_bytes() == memory._sysconf_total_bytes()

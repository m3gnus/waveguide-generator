from types import SimpleNamespace
from unittest.mock import Mock

import ctypes
import logging
import time
import sys

import pytest

from server.solver.beat_runtime import clock


@pytest.fixture
def idle_clock(monkeypatch):
    monkeypatch.setattr(clock, "_clock", clock._IdleClock())
    monkeypatch.setattr(clock.time, "monotonic", lambda: 10.0)
    return clock.suspend_aware_monotonic


@pytest.mark.parametrize("platform,constant", [
    ("linux", "CLOCK_BOOTTIME"), ("darwin", "CLOCK_MONOTONIC_RAW"),
])
def test_posix_clock_includes_simulated_suspend(idle_clock, monkeypatch, platform, constant):
    monkeypatch.setattr(clock.sys, "platform", platform)
    monkeypatch.setattr(clock.time, constant, 123, raising=False)
    native = Mock(side_effect=[100.0, 2000.0, 2000.0])
    monkeypatch.setattr(clock.time, "clock_gettime", native, raising=False)
    assert idle_clock() == 100.0
    assert idle_clock() == 2000.0
    assert idle_clock() == 2000.0  # Equal successive readings are valid.
    assert clock.time.monotonic() == 10.0
    assert native.call_args_list == [((123,),)] * 3


def test_windows_tick_count_is_unsigned_64_bit_seconds(idle_clock, monkeypatch):
    monkeypatch.setattr(clock.sys, "platform", "win32")
    # Exceed 32 bits to catch ctypes' default signed-int return truncation.
    ticks = Mock(side_effect=[2**40 + 125, 2**40 + 1_900_125])
    library = Mock(return_value=SimpleNamespace(GetTickCount64=ticks))
    monkeypatch.setattr(clock.ctypes, "WinDLL", library, raising=False)
    assert idle_clock() == (2**40 + 125) / 1000.0
    assert idle_clock() == (2**40 + 1_900_125) / 1000.0
    assert ticks.argtypes == []
    assert ticks.restype is ctypes.c_uint64
    assert ticks.call_args_list == [((),)] * 2
    library.assert_called_once_with("kernel32", use_last_error=True)


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32", "unsupported"])
@pytest.mark.parametrize("failure", ["missing", "failed"])
def test_unavailable_native_clock_logs_and_falls_back(
    idle_clock, monkeypatch, caplog, platform, failure,
):
    monkeypatch.setattr(clock.sys, "platform", platform)
    if platform in {"linux", "darwin"}:
        constant = "CLOCK_BOOTTIME" if platform == "linux" else "CLOCK_MONOTONIC_RAW"
        if failure == "missing":
            monkeypatch.delattr(clock.time, constant, raising=False)
        else:
            monkeypatch.setattr(clock.time, constant, 123, raising=False)
            monkeypatch.setattr(clock.time, "clock_gettime", Mock(side_effect=OSError("failed")),
                                raising=False)
    elif platform == "win32":
        if failure == "missing":
            monkeypatch.delattr(clock.ctypes, "WinDLL", raising=False)
        else:
            ticks = Mock(side_effect=OSError("failed"))
            monkeypatch.setattr(clock.ctypes, "WinDLL",
                                Mock(return_value=SimpleNamespace(GetTickCount64=ticks)), raising=False)
    with caplog.at_level(logging.WARNING):
        assert idle_clock() == 10.0
        monkeypatch.setattr(clock.time, "monotonic", lambda: 12.0)
        assert idle_clock() == 12.0
    assert len(caplog.records) == 1
    assert "falling back to time.monotonic()" in caplog.text


@pytest.mark.parametrize("bad_reading", [OSError("failed"), float("nan"),
                                       float("inf"), float("-inf"), -1.0])
def test_failure_after_suspend_preserves_epoch_and_elapsed_idle_age(
    idle_clock, monkeypatch, caplog, bad_reading,
):
    native = Mock(side_effect=[100.0, 2000.0, bad_reading])
    monkeypatch.setattr(clock, "_idle_source", lambda: native)
    assert idle_clock() == 100.0
    assert idle_clock() == 2000.0
    with caplog.at_level(logging.WARNING):
        assert idle_clock() == 2000.0
        monkeypatch.setattr(clock.time, "monotonic", lambda: 12.0)
        assert idle_clock() == 2002.0
        # Even a regression in the fallback clock cannot make idle age negative.
        monkeypatch.setattr(clock.time, "monotonic", lambda: 9.0)
        assert idle_clock() == 2002.0
        monkeypatch.setattr(clock.time, "monotonic", lambda: 15.0)
        assert idle_clock() == 2005.0
    assert native.call_count == 3  # Stay on fallback rather than mixing epochs.
    assert len(caplog.records) == 1


def test_small_backward_step_is_held_without_losing_suspend_awareness(idle_clock, monkeypatch, caplog):
    native = Mock(side_effect=[100.0, 99.999, 5000.0])
    monkeypatch.setattr(clock, "_idle_source", lambda: native)
    with caplog.at_level(logging.WARNING):
        assert idle_clock() == 100.0
        assert idle_clock() == 100.0  # held, not a fallback
        assert idle_clock() == 5000.0  # still the native, suspend-inclusive clock
    assert native.call_count == 3
    assert not caplog.records


@pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="native clock smoke test")
def test_real_native_idle_clock_reads_without_fallback(caplog):
    idle_clock = clock._IdleClock()  # real native and monotonic clocks, unpatched
    with caplog.at_level(logging.WARNING):
        first = idle_clock()
        second = idle_clock()
    assert second >= first and first > 0
    assert not caplog.records
    # It includes suspend, so it is never behind the sleep-excluding monotonic clock.
    assert first >= time.monotonic() - 1.0

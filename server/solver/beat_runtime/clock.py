"""A suspend-inclusive clock for idle expiry only (seconds, arbitrary epoch).

Linux CLOCK_BOOTTIME includes suspend, unlike CLOCK_MONOTONIC:
https://man7.org/linux/man-pages/man2/clock_gettime.2.html

Darwin clock_gettime(CLOCK_MONOTONIC) includes sleep. Apple's implementation
uses boot-relative time; CLOCK_MONOTONIC_RAW also includes sleep and uses
mach_continuous_time. CLOCK_UPTIME_RAW/mach_absolute_time excludes sleep:
https://github.com/apple-oss-distributions/Libc/blob/main/gen/clock_gettime.3
https://github.com/apple-oss-distributions/Libc/blob/main/gen/clock_gettime.c

Windows GetTickCount64 includes sleep/hibernation; QueryUnbiasedInterruptTime
excludes it (and is therefore unsuitable here):
https://learn.microsoft.com/en-us/windows/win32/sysinfo/interrupt-time

Python 3.13 time.monotonic uses mach_absolute_time on macOS and QPC on Windows:
https://docs.python.org/3.13/library/time.html#time.monotonic
QPC itself includes sleep on Windows; this helper leaves short deadlines'
existing platform semantics intact rather than promising suspend exclusion:
https://learn.microsoft.com/en-us/windows/win32/sysinfo/acquiring-high-resolution-time-stamps
"""

from collections.abc import Callable
import ctypes
import logging
import math
import sys
import threading
import time

_log = logging.getLogger(__name__)


def _idle_source() -> Callable[[], float]:
    if sys.platform == "linux":
        clock_id = time.CLOCK_BOOTTIME
        return lambda: time.clock_gettime(clock_id)
    if sys.platform == "darwin":
        clock_id = time.CLOCK_MONOTONIC
        return lambda: time.clock_gettime(clock_id)
    if sys.platform == "win32":
        ticks = ctypes.WinDLL("kernel32", use_last_error=True).GetTickCount64
        ticks.argtypes = []
        ticks.restype = ctypes.c_uint64
        return lambda: ticks() / 1000.0
    raise OSError(f"No suspend-inclusive idle clock for {sys.platform}")


class _IdleClock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._read: Callable[[], float] | None = None
        self._last: float | None = None
        self._fallback = False
        self._offset = 0.0

    def __call__(self) -> float:
        with self._lock:
            if self._fallback:
                # Preserve the idle clock's epoch, including already observed
                # suspend time, rather than mixing native and Python epochs.
                now = max(self._last, time.monotonic() + self._offset)
            else:
                try:
                    if self._read is None:
                        self._read = _idle_source()
                    now = self._read()
                    if not math.isfinite(now) or now < 0:
                        raise ValueError(f"Invalid idle clock reading: {now}")
                    if self._last is not None and now < self._last:
                        raise ValueError("Idle clock moved backwards")
                except Exception as exc:
                    monotonic = time.monotonic()
                    now = self._last if self._last is not None else monotonic
                    self._offset = now - monotonic
                    self._fallback = True
                    _log.warning("Suspend-aware idle clock unavailable (%s); falling back to "
                                 "time.monotonic() with a preserved epoch", exc)
            self._last = now
            return now


_clock = _IdleClock()


def suspend_aware_monotonic() -> float:
    """Include suspend in idle age; permanently fall back on failure/regression.

    Fallback logs once and rebases at the last good reading so idle age never
    goes backwards or spuriously expires because the clocks have different epochs.
    Suspend inclusion is no longer guaranteed after fallback.
    """
    return _clock()

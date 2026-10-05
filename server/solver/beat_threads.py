"""How many Julia threads WG gives a BEAT worker.

BEAT · Metal assembles on the GPU but factorizes, and evaluates the field, on
the CPU with every Julia and BLAS thread at once; the package's ``"auto"``
gives it one thread per performance core. Inside WG those cores are never
idle: the server builds a provisional result per frequency and the window
redraws the plots while the sweep runs. One busy core then stalls every
thread of the factorization at each step.

Measured on an 8P+2E M1 Max, 976-node quarter horn, 60 frequencies
100 Hz-20 kHz, with N cores kept busy by a spinning process:

    busy cores        0       1       2
    8 threads     4.3 s   5.9 s   7.7 s
    6 threads     3.9 s   4.0 s   4.6 s

Metal-BEM, which keeps its work on the GPU and the matrix coprocessor, stayed
at 3.9 s throughout, and in the application BEAT · Metal took 6.5 s against
Metal-BEM's 4.2 s. A quarter of the performance cores is left for the rest of
the application. Solving from the UI (1,134-node horn, 40 frequencies, warm
worker), BEAT · Metal went from 3.95-4.44 s to 3.65-3.75 s; Metal-BEM stayed
at 3.54-3.58 s. A cold worker starts in the same 7.2 s either way.

Only Metal changes. BEAT · CPU's thread count is what the Windows numbers
were qualified with, and CUDA and ROCm have not run on their hardware yet.

Every caller that starts a BEAT worker must ask here: the package keys
persistent workers by thread count, so a warmup with a different count warms
a worker no solve will use.
"""

from __future__ import annotations

from functools import lru_cache
import os
import platform
import subprocess

from .beat_runtime.provider import official_selected


@lru_cache(maxsize=1)
def _performance_core_count() -> int:
    total = max(1, os.cpu_count() or 1)
    if platform.system() != "Darwin":
        return total
    try:
        result = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.perflevel0.logicalcpu"],
                                capture_output=True, text=True, timeout=5)
        count = int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return total
    return min(count, total) if count >= 1 else total


def resolve_julia_threads(backend: str, *, performance_cores: int) -> int:
    if official_selected():
        from .beat_runtime.threads import resolve_julia_threads as resolve

        return resolve(backend, performance_cores=performance_cores)
    return max(1, performance_cores - performance_cores // 4)


def beat_julia_threads(backend: str | None) -> int | str:
    """Legacy HBB façade; keep its non-Metal AUTO contract until migration."""

    if backend != "metal":
        return "auto"
    return resolve_julia_threads(backend, performance_cores=_performance_core_count())

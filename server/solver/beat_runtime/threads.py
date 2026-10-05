"""Resolve WG's worker thread budget once, before keying or starting a worker.

AUTO uses macOS performance cores when available. Metal leaves a quarter of
those cores for the server/window; see beat_threads.py for measurement history.
Pass the returned integer unchanged to probes, warm-up, keys and EngineWorker.
"""

from functools import lru_cache
import os
import platform
import subprocess


@lru_cache(maxsize=1)
def _performance_core_count() -> int:
    total = max(1, os.cpu_count() or 1)
    if platform.system() != "Darwin":
        return total
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.perflevel0.logicalcpu"],
            capture_output=True, text=True, timeout=5,
        )
        # Like the HBB-era facade, trust a parsable count whatever the exit status.
        count = int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return total
    return min(count, total) if count >= 1 else total


def resolve_julia_threads(
    backend: str | None, julia_threads: int | str = "auto", *,
    performance_cores: int | None = None,
) -> int:
    """Positive explicit counts win; AUTO alone applies Metal headroom.

    Invalid explicit values are configuration errors, rather than silently
    changing the launch/key budget. performance_cores is an injectable snapshot.
    """
    if isinstance(julia_threads, str) and julia_threads.strip().lower() == "auto":
        cores = _performance_core_count() if performance_cores is None else max(1, performance_cores)
        return max(1, cores - cores // 4) if backend == "metal" else cores
    if isinstance(julia_threads, bool) or not isinstance(julia_threads, (int, str)):
        raise ValueError("Julia threads must be 'auto' or a positive integer")
    try:
        count = int(julia_threads)
    except ValueError as exc:
        raise ValueError("Julia threads must be 'auto' or a positive integer") from exc
    if count < 1:
        raise ValueError("Julia threads must be a positive integer")
    return count

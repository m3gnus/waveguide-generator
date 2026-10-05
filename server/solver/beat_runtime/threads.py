"""Resolve WG's worker thread budget once, before keying or starting a worker.

AUTO uses macOS performance cores when available. Metal leaves a quarter of
those cores for the server/window; see beat_threads.py for measurement history.
Pass the returned integer unchanged to probes, warm-up, keys and EngineWorker.
"""

from __future__ import annotations

from ..beat_threads import _performance_core_count


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

"""What the solver adapters share about the boot warmup, with no engine in it.

The warmup itself chooses among engines, so it lives in
``server/engines/warmup.py``; this module keeps what an engine adapter may read
without reaching any other engine: the warmup mesh and frequency, the warmup
thread's name, the BEAT warmup flag a queued solve reports, and the stored
engine preference the launcher and the app warm for.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
import threading
import time
from typing import Iterator, Mapping


#: A 410-triangle watertight OSSE horn with a wall, meshed over the full
#: domain by this repository's own mesher (the design is the one in
#: ``server/tests/test_mesh_builder.py``), so its physical group tags are
#: exactly what the solver wrapper expects. Nothing about the warmup depends
#: on the geometry: kernel compilation and JIT are mesh-independent, so the
#: only requirements are that it is small and that it solves cleanly. Full
#: domain rather than one quadrant specifically to avoid the wrapper's
#: mirror-reduced-mesh warning, which would otherwise appear in the user's log
#: on every start and describe a problem they do not have.
WARMUP_MESH = Path(__file__).resolve().parent / "warmup_mesh.msh"

#: One frequency is enough. Every kernel, every operator and the linear solver
#: are exercised once per frequency; a second adds compile-free seconds.
WARMUP_FREQUENCY_HZ = 1000.0

WARMUP_THREAD_NAME = "wg2-solver-warmup"

#: What a solve says while it is queued behind the boot warmup.
#:
#: BEAT keeps one Julia worker per server process, so a solve that arrives
#: while the warmup is still inside it simply waits for the lock -- measured at
#: 10-16 s on the packaged 0.3.1 app, under the *previous* stage message,
#: "Configuring BEAT Engine BEM solve (metal)". That message is true and
#: useless: the user is not waiting on configuration, and nothing on screen
#: said that the wait is the one-off compilation their next solve will not pay.
BEAT_WARMUP_STAGE_MESSAGE = (
    "Waiting for engine warm-up to finish "
    "(first solve after start pays engine compilation)"
)

_beat_warmup_lock = threading.Lock()
_beat_warmup_since: float | None = None


@contextlib.contextmanager
def beat_warmup_recorded() -> Iterator[None]:
    """Publish "the BEAT worker is being warmed" for the duration of the block.

    A plain timestamp under a lock rather than anything the solve can wait on:
    the solve must not block on the warmup here, it already blocks on the
    worker lock inside the package. All this state is for is telling the user
    *why*.
    """

    global _beat_warmup_since
    with _beat_warmup_lock:
        _beat_warmup_since = time.monotonic()
    try:
        yield
    finally:
        with _beat_warmup_lock:
            _beat_warmup_since = None


def beat_warmup_in_progress() -> bool:
    """Whether a BEAT warmup currently holds the Julia worker."""

    with _beat_warmup_lock:
        return _beat_warmup_since is not None


#: The frontend namespace that carries the solve options, and the key inside it
#: that names the engine. ``server/settings/store.py`` stores every namespace
#: opaquely because the frontend owns the schema; this reads one string out of
#: one of them and treats every other shape as "no preference", so a schema
#: change on that side costs a missed warmup rather than a failed boot.
SOLVE_OPTIONS_NAMESPACE = "solveOptions"


def persisted_engine_preference(settings: object | None) -> str | None:
    """The engine the user last selected, or ``None`` if they never selected one.

    The prewarm hooks have to answer "which engine will this user's first solve
    use", and AUTO's answer is only that answer while the picker is left on
    AUTO. It is wrong precisely where the wait is longest: on a Mac AUTO
    resolves to Metal -- correctly, it is measurably faster there -- so a user
    who has explicitly chosen BEAT got no warmup at all, and paid BEAT's whole
    one-off cost on their first solve, every launch. Measured on the reference
    Windows box against the CPU backend: 40.2 s for the first warmup and 0.1 s
    for the second, so this is the difference between a 40 s first solve and an
    immediate one.

    Returns the raw stored string, lowercased -- including ``"auto"``, which
    the caller distinguishes from a real engine name. Availability is not
    checked here; ``EngineRegistry.resolve`` is what knows whether this host
    can actually run the answer.
    """

    if settings is None:
        return None
    try:
        selected = settings.get("enginePreference")  # type: ignore[attr-defined]
        if isinstance(selected, str):
            try:
                selected = json.loads(selected)
            except ValueError:
                selected = None
        if isinstance(selected, Mapping) and isinstance(selected.get("engine"), str):
            return selected["engine"].strip().lower() or None
    except Exception:  # noqa: BLE001 - fall back to the legacy namespace
        pass
    try:
        stored = settings.get(SOLVE_OPTIONS_NAMESPACE)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - an unreadable setting is no preference
        return None
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            return None
    if not isinstance(stored, Mapping):
        return None
    # The frontend persists through Zustand, which wraps the store in "state".
    state = stored.get("state")
    if not isinstance(state, Mapping):
        return None
    engine = state.get("engine")
    if not isinstance(engine, str):
        return None
    return engine.strip().lower() or None


__all__ = [
    "BEAT_WARMUP_STAGE_MESSAGE",
    "SOLVE_OPTIONS_NAMESPACE",
    "WARMUP_FREQUENCY_HZ",
    "WARMUP_MESH",
    "WARMUP_THREAD_NAME",
    "beat_warmup_in_progress",
    "beat_warmup_recorded",
    "persisted_engine_preference",
]

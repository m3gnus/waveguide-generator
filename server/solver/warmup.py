"""Optionally pay the selected solver's one-off per-process start-up cost.

``docs/validation/2026-08/WINDOWS-VALIDATION.md`` check 12 measured the problem this exists to
fix. The first bempp solve after every server start spends a long time inside
one block with no cancellation checkpoint in it -- the engine's only checkpoint
is its per-frequency progress callback, and the first callback does not fire
until the first assembly is finished. Re-measured on the same machine in
2026-08, 3 frequencies, R-OSSE: 25.4 s of that block on OpenCL and 61.4 s on
numba, for a first solve of 28.2 s and 65.7 s. The second solve is 0.8 s and
4.1 s.

Check 12 read that block as Stop latency. It is no longer that:
``server/solver/bempp_process.py`` moved the solve into a child the parent
polls every 50 ms and kills outright, and a Stop 5.0 s into a cold solve now
returns in 5.05 s. What survives is the part a process boundary cannot fix --
time to first result -- and that is what this module is for.

The cost is per process and cannot be moved to disk. bempp-cl's hot numba
kernels are declared without ``cache=True``, so the JIT runs again in every new
interpreter; only pyopencl's *program* cache survives a restart, and that is
the smaller half.

*Which* process therefore decides whether warming is worth anything at all,
and that is what this module originally got wrong. Every BEMPP solve runs in
the killable worker child owned by ``server/solver/bempp_process.py``, not
here, so warming this interpreter warmed one that never solves. Measured on
the reference Windows VM: ``WG2_SOLVER_WARMUP=1`` spent 24.2 s in the parent
and the first child solve still took 24.3 s, indistinguishable from no warmup
at all. ``_warm_bempp`` now hands the work to the child, and
``server.app`` does the same from boot without an opt-in, through
``prewarm_bempp_worker_for_engine``.

That relocation is also what answers the objection below. The warmup is a real,
non-cancellable native solve with no fast shutdown hook, so a daemon thread
running it *in this process* could hold Quit open for the rest of the
initialization block -- which is why the in-process path stayed behind
``WG2_SOLVER_WARMUP=1`` and why it still does for Metal, which has no worker
child. A child needs no cooperation to die: ``BemppProcessHost.close()`` gives
it half a second and then reaches ``TerminateProcess``/``SIGKILL``. So the
BEMPP branch is on by default and the Metal branch is not, and the difference
between them is exactly whether shutdown can bound the wait.

Measured phases in a fresh interpreter on the development Windows VM:

======================================  ========
importing ``bempp_cl.api``               ~1.7 s
first ``function_space`` (numba JIT)    ~12.4 s
four boundary-operator assemblies        ~4.4 s
a small GMRES solve                     ~10.5 s
potential-operator evaluation            ~0.5 s
======================================  ========

Running one real end-to-end solve is therefore the warmup: it is the only
thing that touches every one of those paths in the same order a user's solve
will. The engine must match AUTO resolution. Apple Silicon normally resolves
to Metal; warming the fallback BEMPP engine there wastes several seconds and
does not remove Metal's smaller first-solve cost.

``start_solver_warmup`` uses a plain daemon thread rather than
``asyncio.to_thread``. The default executor's threads are joined during
interpreter shutdown, and for the in-process Metal path that would hold Quit
open for the remainder of the native initialization block. The worker prewarm
has no such problem -- it only spawns a process and writes one command to a
pipe -- so it runs as an ordinary task the app can cancel.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Iterator, Mapping


log = logging.getLogger("wg.solver.warmup")

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

_lock = threading.Lock()
_thread: threading.Thread | None = None

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


class _QuietWarmupFilter(logging.Filter):
    """Keep the warmup's own solver chatter out of the user's log.

    A real solve's bempp output is a diagnostic somebody may need; the
    warmup's is the same output about a 410-triangle stand-in nobody asked to
    solve, and at INFO it is roughly a hundred lines of assembler timings and
    GMRES iterations on every single start.

    Filtering by *thread* rather than by logger level is what keeps this
    surgical: it silences those libraries only while they are being driven by
    the warmup thread, so a real solve running at the same time still logs in
    full. Records from this module are always kept -- the point is to be able
    to see that the warmup ran.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.threadName != WARMUP_THREAD_NAME:
            return True
        return record.name.startswith("wg.")


_quiet_filter = _QuietWarmupFilter()


def _log_handlers() -> tuple[logging.Handler, ...]:
    """Every handler a warmup record could reach.

    Filters attached to a *logger* only see records logged through that logger,
    so a filter on the root logger would miss everything propagating up from
    the solver's own loggers. Handler filters see all of it.
    """

    handlers = list(logging.getLogger().handlers)
    try:
        from server.platform.logging_setup import log_sinks

        handlers.extend(log_sinks())
    except Exception:  # noqa: BLE001 - logging may not be configured at all
        pass
    return tuple(dict.fromkeys(handlers))


def _warm_metal() -> None:
    """Run the same native Metal formulation used by a real application solve."""

    from hornlab_metal_bem import ObservationConfig, native_config, solve

    from .formulation import DEFAULT_BEM_FORMULATION, DEFAULT_COMPLEX_K_SHIFT

    config = native_config(
        freq_min_hz=WARMUP_FREQUENCY_HZ,
        freq_max_hz=WARMUP_FREQUENCY_HZ,
        freq_count=1,
        observation=ObservationConfig(),
        formulation=DEFAULT_BEM_FORMULATION,
        complex_k_shift=DEFAULT_COMPLEX_K_SHIFT,
    )
    solve(str(WARMUP_MESH), config)


def _warm_beat(backend: str) -> None:
    """Start and exercise the BEAT Engine's persistent Julia worker.

    BEAT is the one engine whose cost was never being paid off the user's
    first solve, and the one where a warmup is cheapest to justify. Every BEAT
    solve goes through a Julia worker that ``hornlab_beat_bem`` keeps for the
    life of *this* process, so the cost is paid once per server -- but nothing
    was paying it until a user asked for a solve and then waited through Julia
    startup, package loading, engine compilation and accelerator kernel
    compilation before the first frequency began.

    ``mode="tiny"`` rather than ``"worker"``: booting the worker alone leaves
    the JIT and the GPU kernel compilation on the first real solve, which is
    most of the wait. ``"tiny"`` solves one frequency on a four-triangle
    tetrahedron and touches those paths in the order a user's solve will, the
    same reasoning as the BEMPP and Metal warmups above.

    Unlike ``_warm_metal`` this needs no ``WG2_SOLVER_WARMUP=1`` opt-in. The
    objection that gates the Metal branch is that a non-cancellable native
    solve on a daemon thread can hold Quit open; BEAT's work happens in
    another process that ``shutdown_beat_worker`` lets go of in bounded time
    -- detaching from a persistent host, terminating a child under
    ``HORNLAB_BEAT_PERSISTENT_HOST=0`` -- so this thread is only ever waiting
    on a pipe.

    The backend is the caller's, not this function's to guess. Each BEAT
    backend is its own selectable engine and its own persistent Julia worker
    (the package keys workers by Julia project, and the four backends have four
    projects), so warming a different one is worse than not warming at all: it
    pays a device initialisation twice and leaves the path the user waits on
    cold. ``prewarm_beat_worker_for_engine`` reads the backend off the engine
    name that was actually selected.
    """

    import hornlab_beat_bem

    # Recorded around the call, not inside the package: a solve that arrives
    # now waits for the same worker, and ``beat_warmup_in_progress`` is what
    # lets ``server/solver/beat.py`` say so instead of leaving the user on
    # "Configuring BEAT Engine BEM solve" for the length of the compile.
    with beat_warmup_recorded():
        hornlab_beat_bem.warm_up(beat_backend=backend, mode="tiny")


def warm_bempp_in_this_process(status: Mapping[str, object]) -> None:
    """Compile and exercise the BEMPP fallback in the *calling* interpreter.

    Called in the killable worker child by
    ``server/solver/bempp_process.py``, which is the process every real BEMPP
    solve runs in.  Calling it in the API process warms an interpreter that
    never solves: measured on the reference Windows VM, an in-parent warmup
    cost 24.2 s and left the first child solve at 24.3 s, unchanged.  Only
    pyopencl's on-disk *program* cache crosses a process boundary, and on this
    host it was already populated.
    """

    backend = status.get("assembly_backend") or "opencl"
    from hornlab_bempp_bem import ObservationConfig, SolveConfig, solve

    config = SolveConfig(
        freq_min_hz=WARMUP_FREQUENCY_HZ,
        freq_max_hz=WARMUP_FREQUENCY_HZ,
        freq_count=1,
        observation=ObservationConfig(),
        assembly_backend=backend,
        opencl_device="cpu",
        precision="single",
        # Never spawn. A worker process would re-pay every cost this
        # warmup exists to pay once, and would do it while the user is
        # trying to use the application.
        workers=1,
    )
    solve(str(WARMUP_MESH), config)


def _warm_bempp(status: Mapping[str, object]) -> None:
    """Warm the BEMPP worker child, which is where a real solve runs.

    ``del status`` is deliberate: the child re-probes rather than inheriting a
    status this process happened to resolve.  ``bempp_status`` is cached per
    interpreter and the child is a fresh one, so a probe there costs a probe
    and describes the process whose backend choice actually matters.
    """

    del status
    from .bempp_process import prewarm_bempp_process

    prewarm_bempp_process()


def _run_warmup() -> None:
    began = time.monotonic()
    handlers = _log_handlers()
    for handler in handlers:
        handler.addFilter(_quiet_filter)
    try:
        # Match EngineRegistry.resolve_auto_engine's priority without probing
        # every optional stack up front. In particular, do not import or
        # initialize BEMPP when Metal is the engine the first user solve takes.
        from server.solver import metal as metal_adapter

        metal_status = metal_adapter.metal_status()
        if metal_status.get("available"):
            engine = "Metal"
            _warm_metal()
        else:
            # AUTO's order is metal, BEAT's accelerators, bempp, BEAT-CPU (see
            # FULL3D_ENGINE_ORDER). Leaving BEAT out here did not merely skip a
            # warmup: on a CUDA host, where AUTO resolves to BEAT, this fell
            # through and warmed BEMPP -- an engine that host's first solve
            # never reaches. The package probe is the right question at this
            # point precisely because it reports available only for an
            # accelerator, which is the half of BEAT that outranks BEMPP.
            from server.solver import beat as beat_adapter

            beat_status = beat_adapter.beat_status()
            if beat_status.get("available"):
                engine = "BEAT"
                _warm_beat(beat_adapter.resolve_beat_backend(beat_status))
            else:
                from server.solver import bempp as bempp_adapter

                bempp_status = bempp_adapter.bempp_status()
                if not bempp_status.get("available"):
                    log.info(
                        "Solver warmup skipped: Metal: %s; BEAT: %s; BEMPP: %s",
                        metal_status.get("reason"),
                        beat_status.get("reason"),
                        bempp_status.get("reason"),
                    )
                    return
                engine = "BEMPP"
                _warm_bempp(bempp_status)
    except Exception as exc:  # noqa: BLE001 - a warmup is an optimisation
        # Whatever failed here will fail again at the real call site, where the
        # message reaches the user attached to their job. The same policy as
        # server/platform/warmup.py.
        log.info("Solver warmup did not complete: %s", exc)
        return
    finally:
        for handler in handlers:
            handler.removeFilter(_quiet_filter)
    if engine == "BEMPP":
        # Nothing was warmed here. The child was handed the work and is paying
        # the cost now, off this thread and off the user's first solve; saying
        # "finished" would claim a duration that has not elapsed yet.
        log.info("BEMPP worker warmup handed to the solver worker process")
    else:
        log.info(
            "%s solver warmup finished in %.1f s; the first solve no longer pays its "
            "one-off initialization cost",
            engine,
            time.monotonic() - began,
        )


def start_solver_warmup() -> threading.Thread | None:
    """Begin an explicitly requested background warmup. Never blocks or raises.

    Returns the live thread, or ``None`` when warming is switched off with
    ``WG2_SOLVER_WARMUP=0``. Calling twice while one is running returns the
    running thread rather than starting a second. The production launcher
    calls this path only when ``WG2_SOLVER_WARMUP=1``; direct callers opt in by
    calling it or by passing ``solver_warmup=True`` to ``create_app``.
    """

    global _thread
    if os.environ.get("WG2_SOLVER_WARMUP") == "0":
        log.info("Solver warmup disabled by WG2_SOLVER_WARMUP=0")
        return None
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        _thread = threading.Thread(
            target=_run_warmup, name=WARMUP_THREAD_NAME, daemon=True
        )
        _thread.start()
        return _thread


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


def prewarm_beat_worker_for_engine(engine: str | None) -> bool:
    """Warm the BEAT Julia worker for the backend this host will actually solve on.

    The sibling of ``prewarm_bempp_worker_for_engine``, and registered the same
    way, for the same reason: BEAT's initialization is the largest thing
    between a server start and a GPU host's first result, and it happens in a
    child process, so warming it costs shutdown nothing.

    It is a *separate* hook rather than a branch inside the BEMPP one because
    the two warm different things in different processes and only one of them
    can apply to a given host; a single function named for one engine that
    quietly warmed the other would be worse than two honest ones.

    Returns whether a worker was warmed, for tests and diagnostics.

    Every branch logs at INFO, including the skip. PLAN calls this step a hard
    requirement and six consecutive boots of ``server.log`` recorded nothing
    about it at all: the success path logged nothing and the skip logged at
    DEBUG, which the shipped configuration does not emit. An optimisation whose
    absence is invisible cannot be diagnosed from a user's log, and this one
    was diagnosed from a packaged app's ``job_events`` instead.
    """

    if os.environ.get("WG2_SOLVER_WARMUP") == "0":
        log.info("BEAT worker prewarm disabled by WG2_SOLVER_WARMUP=0")
        return False
    from server.solver import beat as beat_adapter

    # The backend is read off the engine name rather than re-derived from a
    # probe: each BEAT backend is its own selectable engine and its own
    # persistent Julia worker, so "which engine will the first solve use" is
    # already the whole answer.
    backend = beat_adapter.beat_engine_backend(engine or "")
    if backend is None and (engine or "").strip().lower() != beat_adapter.LEGACY_BEAT_ENGINE:
        log.info("BEAT worker prewarm skipped: the first solve uses %s", engine)
        return False
    began = time.monotonic()
    try:
        if backend is None:
            # A stored preference written before the backends became separately
            # selectable, which the registry could not map to an available
            # variant. Letting the package's own probe choose is what this hook
            # did for that name before the split, and it beats warming nothing.
            backend = beat_adapter.resolve_beat_backend(beat_adapter.beat_status())
        log.info("BEAT worker prewarm starting on the %s backend", backend)
        _warm_beat(backend)
    except Exception as exc:  # noqa: BLE001 - a prewarm is an optimisation
        log.info(
            "BEAT worker prewarm failed after %.1f s: %s", time.monotonic() - began, exc
        )
        return False
    log.info(
        "BEAT worker prewarm finished in %.1f s; the first solve no longer pays "
        "the engine's one-off compilation",
        time.monotonic() - began,
    )
    return True


def prewarm_bempp_worker_for_engine(engine: str | None) -> bool:
    """Warm the BEMPP worker child, but only where AUTO would reach it.

    ``engine`` is AUTO's already-resolved answer, passed in rather than probed
    for. Probing here instead cost a second, concurrent ``detect_engines()`` at
    boot: this ran on its own thread 2 ms after ``EngineRegistry.prewarm``
    started the same work, and neither ``lru_cache`` nor ``circsym_status``
    (which has no cache at all) serialises a miss, so both threads did the full
    probe. The registry already owns one snapshot behind an ``asyncio.Lock``;
    the caller awaits that and hands the answer over.

    Returns whether a worker was warmed, for tests and diagnostics.
    """

    if os.environ.get("WG2_SOLVER_WARMUP") == "0":
        log.info("BEMPP worker prewarm disabled by WG2_SOLVER_WARMUP=0")
        return False
    if engine != "bempp":
        # A Metal or GPU-BEAT host would spend a process and several seconds on
        # a fallback its first solve never reaches. Users who then pick BEMPP by
        # hand pay the cost on that solve, as before.
        log.debug("BEMPP worker prewarm skipped: AUTO resolves to %s", engine)
        return False
    try:
        from .bempp_process import prewarm_bempp_process

        prewarm_bempp_process()
    except Exception as exc:  # noqa: BLE001 - a prewarm is an optimisation
        log.info("BEMPP worker prewarm did not start: %s", exc)
        return False
    return True


def solver_warmup_thread() -> threading.Thread | None:
    """The live warmup thread, for tests and diagnostics."""

    return _thread


__all__ = [
    "BEAT_WARMUP_STAGE_MESSAGE",
    "WARMUP_FREQUENCY_HZ",
    "WARMUP_MESH",
    "beat_warmup_in_progress",
    "beat_warmup_recorded",
    "persisted_engine_preference",
    "prewarm_beat_worker_for_engine",
    "prewarm_bempp_worker_for_engine",
    "solver_warmup_thread",
    "start_solver_warmup",
    "warm_bempp_in_this_process",
]

"""Optionally pay the selected solver's one-off per-process start-up cost.

``docs/WINDOWS-VALIDATION.md`` check 12 measured the problem this exists to
fix. The first bempp solve after every server start spends a long time inside
one uninterruptible block -- 17.5 s on OpenCL and 53.8 s on numba, measured on
the reference Windows machine -- and *no cancellation checkpoint can run
during it*, because the engine's only checkpoint is its per-frequency progress
callback and the first callback does not fire until the first assembly is
finished. So a user who presses Stop during their first solve waits out the
whole block. The second solve is 0.7-1.0 s.

The cost is per process and cannot be moved to disk. bempp-cl's hot numba
kernels are declared without ``cache=True``, so the JIT runs again in every new
interpreter; only pyopencl's *program* cache survives a restart, and that is
the smaller half. A controlled benchmark can move when this cost is paid, but
the warmup is itself a real, non-cancellable native solve. It is not serialized
with user jobs and has no fast shutdown hook, so the production launcher keeps
it off unless the operator explicitly sets ``WG2_SOLVER_WARMUP=1``.

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

The explicitly requested experiment uses a plain daemon thread rather than
``asyncio.to_thread``. The default executor's threads are joined during
interpreter shutdown and could hold Quit open for the remainder of the native
initialization block. Abandoning a daemon in native code is not a clean
production shutdown guarantee; that is why release launches do not start this
thread automatically.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path


log = logging.getLogger("wg2.solver.warmup")

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
        return record.name.startswith("wg2.")


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


def _warm_bempp(status: dict[str, object]) -> None:
    """Compile and exercise the BEMPP fallback selected on non-Metal hosts."""

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
            from server.solver import bempp as bempp_adapter

            bempp_status = bempp_adapter.bempp_status()
            if not bempp_status.get("available"):
                log.info(
                    "Solver warmup skipped: Metal: %s; BEMPP: %s",
                    metal_status.get("reason"),
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


def solver_warmup_thread() -> threading.Thread | None:
    """The live warmup thread, for tests and diagnostics."""

    return _thread


__all__ = ["WARMUP_FREQUENCY_HZ", "WARMUP_MESH", "solver_warmup_thread", "start_solver_warmup"]

"""Official BEAT runtime selection for shared exterior voltage jobs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any

from . import exterior_transducer_imported as exterior
from .beat_adapter.transducers import TransducerSweep
from .exterior_transducer_imported import (
    MIXED_BASIS_CONTRACT as MIXED_BASIS_CONTRACT,
    compile_request as compile_request,
    drivers_for as drivers_for,
    enabled as enabled,
    excitation_plan as excitation_plan,
    preflight as preflight,
)


def _solver_engine(backend: str) -> dict[str, Any]:
    from .official_beat import engine_version

    return {
        "engine": f"beat-{backend}",
        "provider": "official",
        "package": "beat-engine",
        "package_version": engine_version(),
        "device": backend,
    }


def package_sweep(
    sweep: TransducerSweep, compiled, request: Any, record: Mapping, *, started: float,
    port_indices: Mapping[str, Sequence[int]] | None = None,
    solver_engine: Mapping[str, Any] | None = None,
) -> dict:
    return exterior.package_sweep(
        sweep, compiled, request, record, started=started, port_indices=port_indices,
        solver_engine=solver_engine if solver_engine is not None else _solver_engine(
            compiled.wire["solver_options"]["bem_backend"]),
    )


def solve(
    msh_text: str,
    request: Any,
    record: Mapping,
    *,
    backend: str,
    stage_callback=None,
    cancellation_callback=None,
    result_callback=None,
    worker_manager=None,
    julia_executable=None,
    compiled_solver=None,
    solver_engine: Mapping[str, Any] | None = None,
) -> dict:
    if compiled_solver is None:
        from .official_beat import solve_transducer_compiled

        compiled_solver = partial(solve_transducer_compiled, worker_manager=worker_manager,
                                  julia_executable=julia_executable)
    return exterior.solve(
        msh_text, request, record, backend=backend, compiled_solver=compiled_solver,
        solver_engine=solver_engine if solver_engine is not None else _solver_engine(backend),
        stage_callback=stage_callback, cancellation_callback=cancellation_callback,
        result_callback=result_callback,
    )

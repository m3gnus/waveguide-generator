"""Shared adapter outcome and callbacks for the retained in-process pipeline.

The artifact/result split follows v1
``server/services/simulation_runner.py:451-489,529-567``.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from server.jobs.models import SolveRequest

from .field_traces_store import FieldTraceArtifact


CancelCallback = Callable[[], None]
StageCallback = Callable[[str, float, str], None]
ArtifactCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
ResultCallback = Callable[[int, dict[str, Any]], None]

# Registration marker rather than engine-name matching: a future engine can
# happen to use a BEAT-like name without implementing this exact run contract.
FULL3D_SOLVER_PORT_MARKER = object()


@dataclass(slots=True)
class EngineRunResult:
    results: dict[str, Any]
    msh_text: str | None = None
    mesh_stats: dict[str, Any] | None = None
    # Compressed NPZ of per-channel complex pressure bases (multi-channel
    # imported solves only). Persisting these is what makes post-solve
    # recombination possible: the JSON contract stores magnitude+phase per
    # channel, but a new crossover needs the complex fields.
    channel_bases: bytes | None = None
    # Opt-in passive-cardioid aperture radiation matrix. Kept as the original
    # compressed NPZ so complex matrices, diagnostics, and physical face
    # identity remain lossless and downloadable independently of result JSON.
    radiation_impedance: bytes | None = None
    # Full-3D backend-native surface traces, retained per unsynthesized channel.
    field_traces: FieldTraceArtifact | None = None
    field_trace_unavailable_reason: str | None = None


class Full3DSolverPort(Protocol):
    """HornLab-facing contact point for an independently implemented BEM engine.

    BEAT and Metal share this application boundary, not a native worker API.
    The optional imported record is rejected by engines that only accept
    parametric geometry. Callback ownership stays with the job runner: a port
    must check cancellation before expensive phases, emit progress and partial
    results when available, and return an ``EngineRunResult``.
    """

    name: str
    solver_port_marker: object

    async def run(
        self,
        request: SolveRequest,
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None = None,
        result_cb: ResultCallback | None = None,
        imported_record: Mapping[str, Any] | None = None,
    ) -> EngineRunResult: ...


def is_full3d_solver_port(engine: object) -> bool:
    """Return whether an adapter explicitly opts into the shared run seam."""

    return getattr(engine, "solver_port_marker", None) is FULL3D_SOLVER_PORT_MARKER


async def run_full3d_solver_port(
    engine: Full3DSolverPort,
    request: SolveRequest,
    *,
    cancel_cb: CancelCallback,
    stage_cb: StageCallback,
    artifact_cb: ArtifactCallback | None = None,
    result_cb: ResultCallback | None = None,
    imported_record: Mapping[str, Any] | None = None,
) -> EngineRunResult:
    """Invoke a BEM port without native-protocol or signature introspection."""

    outcome = await engine.run(
        request,
        cancel_cb=cancel_cb,
        stage_cb=stage_cb,
        artifact_cb=artifact_cb,
        result_cb=result_cb,
        imported_record=imported_record,
    )
    if not isinstance(outcome, EngineRunResult):
        raise TypeError(f"{engine.name} returned no normalized EngineRunResult")
    return outcome


__all__ = [
    "ArtifactCallback",
    "CancelCallback",
    "EngineRunResult",
    "FULL3D_SOLVER_PORT_MARKER",
    "Full3DSolverPort",
    "is_full3d_solver_port",
    "run_full3d_solver_port",
    "ResultCallback",
    "StageCallback",
]

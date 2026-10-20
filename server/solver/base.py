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
ExecutionMetadataCallback = Callable[[Mapping[str, Any]], Awaitable[None]]

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
    # Deferred lossless arrays: compression/preparation and publication share
    # the runtime's optional-field failure policy, outside native execution.
    fem_field_arrays: Callable[[], Mapping[str, Any]] | None = None
    bem_trace_arrays: Callable[[], Mapping[str, Any] | None] | None = None
    fem_plane_unavailable_reason: str | None = None
    execution_metadata: dict[str, Any] | None = None


class Full3DSolverPort(Protocol):
    """HornLab-facing contact point for an independently implemented solver.

    BEAT, Metal and BEMPP share this application boundary, not a native worker API.
    The optional imported record is rejected by engines that only accept
    parametric geometry. Callback ownership stays with the job runner: a port
    must check cancellation before expensive phases, emit progress and partial
    results when available, and return an ``EngineRunResult``. The optional async
    execution metadata callback records compiled backend decisions before native
    execution, including when that execution later fails or is cancelled.
    """

    name: str

    async def run(
        self,
        request: SolveRequest,
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None = None,
        result_cb: ResultCallback | None = None,
        imported_record: Mapping[str, Any] | None = None,
        execution_metadata_cb: ExecutionMetadataCallback | None = None,
    ) -> EngineRunResult: ...


__all__ = [
    "ArtifactCallback",
    "CancelCallback",
    "EngineRunResult",
    "ExecutionMetadataCallback",
    "Full3DSolverPort",
    "ResultCallback",
    "StageCallback",
]

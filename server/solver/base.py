"""Shared adapter outcome and callbacks for the retained in-process pipeline.

The artifact/result split follows v1
``server/services/simulation_runner.py:451-489,529-567``.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

from .field_traces_store import FieldTraceArtifact


CancelCallback = Callable[[], None]
StageCallback = Callable[[str, float, str], None]
ArtifactCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
ResultCallback = Callable[[int, dict[str, Any]], None]


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
    # Full-3D native Metal surface traces, retained per unsynthesized channel.
    field_traces: FieldTraceArtifact | None = None
    field_trace_unavailable_reason: str | None = None


__all__ = [
    "ArtifactCallback",
    "CancelCallback",
    "EngineRunResult",
    "ResultCallback",
    "StageCallback",
]

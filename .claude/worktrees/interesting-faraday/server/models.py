"""Backward-compatible alias for API contracts.

Internal backend code should import these models from ``contracts`` instead.
"""

from contracts import (
    BoundaryCondition,
    ChartsRenderRequest,
    DirectivityRenderRequest,
    JobStatus,
    MeshData,
    PolarConfig,
    SimulationRequest,
    SimulationResults,
    WaveguideParamsRequest,
)

__all__ = [
    "BoundaryCondition",
    "ChartsRenderRequest",
    "DirectivityRenderRequest",
    "JobStatus",
    "MeshData",
    "PolarConfig",
    "SimulationRequest",
    "SimulationResults",
    "WaveguideParamsRequest",
]

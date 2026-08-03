"""Typed bridge from v2 job/design models to native adapter configuration.

Frequency and source fields correspond to v1 orchestration at
``server/services/simulation_runner.py:320-395``; observation defaults follow
v1 ``server/solver/result_mapping.py:98-130``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from server.design.schema import DesignConfig, Expr
from server.jobs.models import SolveRequest

from .quadrants import FULL_DOMAIN_QUADRANTS, normalise_quadrants


def _number(value: Expr | None, fallback: float) -> float:
    if value is None or value.value is None:
        return fallback
    return float(value.value)


@dataclass(slots=True)
class SolverContext:
    design: DesignConfig
    frequency_range: tuple[float, float]
    num_frequencies: int
    frequency_spacing: str = "log"
    mesh_validation_mode: str = "warn"
    solver_mode: str = "full_3d"
    quadrants: int = FULL_DOMAIN_QUADRANTS
    sim_type: int = 2
    source_motion: str = "normal"
    polar_config: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled_axes": ["horizontal", "vertical"],
            "distance": 2.0,
            "angle_range": [0.0, 180.0, 37],
            "observation_origin": "mouth",
            "spherical_sampling": False,
        }
    )

    @classmethod
    def from_request(cls, request: SolveRequest, *, solver_mode: str) -> "SolverContext":
        root = request.design.root
        simulation = root.simulation
        if request.options.frequency_range is not None:
            start, end = request.options.frequency_range
        else:
            start = _number(simulation.f1, 200.0)
            end = _number(simulation.f2, 20_000.0)
        if start <= 0.0 or end <= start:
            start, end = 200.0, 20_000.0
        count = request.options.num_frequencies
        if count is None:
            count = int(round(_number(simulation.num_frequencies, 24.0)))
        count = max(1, min(401, int(count)))
        quadrants = normalise_quadrants(_number(root.mesh.quadrants, float(FULL_DOMAIN_QUADRANTS)))

        convention = root.source.velocity_convention
        if convention in {"normal", "axial"}:
            source_motion = convention
        else:
            velocity = _number(root.source.velocity, 1.0)
            if velocity not in {1.0, 2.0}:
                raise ValueError("source.velocity must be 1 (normal) or 2 (axial)")
            source_motion = "axial" if velocity == 2.0 else "normal"

        return cls(
            design=request.design,
            frequency_range=(float(start), float(end)),
            num_frequencies=count,
            frequency_spacing=request.options.frequency_spacing,
            mesh_validation_mode=request.options.mesh_validation_mode,
            solver_mode=solver_mode,
            quadrants=quadrants,
            sim_type=1 if simulation.sim_type == "infinite-baffle" else 2,
            source_motion=source_motion,
        )


__all__ = ["SolverContext"]

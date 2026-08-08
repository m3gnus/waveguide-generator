"""Typed bridge from v2 job/design models to native adapter configuration.

Frequency and source fields correspond to v1 orchestration at
``server/services/simulation_runner.py:320-395``; observation defaults follow
v1 ``server/solver/result_mapping.py:98-130``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from server.design.schema import DesignConfig, Expr
from server.jobs.models import PolarConfig, SolveRequest

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
    # When set, the adapters solve these points verbatim and ``frequency_spacing``
    # is inert. ``frequency_range``/``num_frequencies`` still describe the list
    # (first, last, length) so metadata, mesh diagnostics, and progress reporting
    # need no special case.
    frequencies_hz: tuple[float, ...] | None = None
    mesh_validation_mode: str = "warn"
    verbose: bool = False
    solver_mode: str = "full_3d"
    quadrants: int = FULL_DOMAIN_QUADRANTS
    sim_type: int = 2
    source_motion: str = "normal"
    polar_config: dict[str, Any] = field(
        default_factory=lambda: PolarConfig().model_dump(mode="json")
    )

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Revalidate mutable context fields at every native adapter boundary."""

        start, end = self.frequency_range
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("solver frequency bounds must be finite")
        if start <= 0.0 or end < start:
            raise ValueError("solver frequency bounds must be positive and increasing")
        if self.frequencies_hz is None and end == start:
            raise ValueError("solver frequency bounds must be positive and increasing")
        if isinstance(self.num_frequencies, bool) or not 1 <= int(self.num_frequencies) <= 401:
            raise ValueError("solver frequency count must be between 1 and 401")
        if self.frequencies_hz is not None:
            self._validate_explicit_frequencies()
        if self.frequency_spacing not in {"log", "linear"}:
            raise ValueError("solver frequency spacing must be 'log' or 'linear'")
        if self.mesh_validation_mode not in {"strict", "warn", "off"}:
            raise ValueError("solver mesh validation mode must be strict, warn, or off")
        if not self.polar_config.get("enabled_axes"):
            raise ValueError("solver polar config must enable at least one axis")

    def _validate_explicit_frequencies(self) -> None:
        """Re-check the explicit sweep, including its agreement with the summary fields.

        The summary fields are what the adapters hand to the native configs, so a
        context mutated after construction must not be able to drift into a state
        where the reported range no longer describes the solved points.
        """

        frequencies = self.frequencies_hz
        assert frequencies is not None  # guarded by the caller
        if not frequencies:
            raise ValueError("solver explicit frequencies must not be empty")
        if not all(math.isfinite(value) for value in frequencies):
            raise ValueError("solver explicit frequencies must be finite")
        if any(value <= 0.0 for value in frequencies):
            raise ValueError("solver explicit frequencies must be positive")
        if any(later <= earlier for earlier, later in zip(frequencies, frequencies[1:])):
            raise ValueError("solver explicit frequencies must be strictly ascending")
        if len(frequencies) != int(self.num_frequencies):
            raise ValueError(
                "solver frequency count must match the explicit frequency list"
            )
        if self.frequency_range != (frequencies[0], frequencies[-1]):
            raise ValueError(
                "solver frequency bounds must match the explicit frequency list"
            )

    @classmethod
    def from_request(cls, request: SolveRequest, *, solver_mode: str) -> "SolverContext":
        root = request.design.root
        simulation = root.simulation
        explicit: tuple[float, ...] | None = None
        if request.options.frequencies_hz is not None:
            # SolveOptions already rejected empty, non-finite, non-positive, and
            # non-ascending lists, so the summary fields follow from it directly.
            # Nothing here may fall back to a default grid: an unusable explicit
            # list must surface as an error, never as a silently different sweep.
            explicit = tuple(float(value) for value in request.options.frequencies_hz)
            start, end = explicit[0], explicit[-1]
            count = len(explicit)
        else:
            if request.options.frequency_range is not None:
                start, end = request.options.frequency_range
            else:
                start = _number(simulation.f1, 200.0)
                end = _number(simulation.f2, 20_000.0)
            if not math.isfinite(start) or not math.isfinite(end):
                raise ValueError("solver frequency bounds must be finite")
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
            frequencies_hz=explicit,
            mesh_validation_mode=request.options.mesh_validation_mode,
            verbose=request.options.verbose,
            solver_mode=solver_mode,
            quadrants=quadrants,
            sim_type=1 if simulation.sim_type == "infinite-baffle" else 2,
            source_motion=source_motion,
            polar_config=request.options.polar_config.model_dump(mode="json"),
        )


__all__ = ["SolverContext"]

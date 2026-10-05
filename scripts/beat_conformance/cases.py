"""Declared HBB conformance cases adapted to WG compiled requests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from server.solver.beat_adapter.capabilities import FRAME, PROBE_MESH, capability_report
from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.request import CompiledRequest, SourceBasis, build_request
from server.solver.beat_adapter.results import SweepResult

from .analytic import (SPHERE_OBSERVATION_M, SPHERE_RADIUS_M, SPHERE_SUBDIVISIONS,
                       gmsh_surface, icosphere, score_sphere)


@dataclass(frozen=True)
class ConformanceCase:
    name: str
    description: str
    frequencies_hz: tuple[float, ...] = ()
    backend: str = "cpu"
    precision: str = "float32"
    min_solved_count: int = 0
    make_request: Callable[[], CompiledRequest] | None = None
    accept: Callable[[SweepResult], dict[str, Any]] | None = None
    static_check: Callable[[], dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if (self.backend not in {"cpu", "metal"} or self.precision not in {"float32", "float64"}
                or (self.backend == "metal" and self.precision != "float32")):
            raise ValueError("Cases require a supported CPU/Metal precision")
        if (self.make_request is None) == (self.static_check is None):
            raise ValueError("Case must declare exactly one static check or solve request")
        if self.make_request is not None and (
                not self.frequencies_hz or type(self.min_solved_count) is not int
                or self.min_solved_count < len(self.frequencies_hz) or self.accept is None):
            raise ValueError("Solve cases require acceptance and a floor covering every frequency")


def _request(mesh: str, frequencies: tuple[float, ...], *, precision: str,
             distance: float, angles: tuple[float, float, int], backend: str = "cpu") -> CompiledRequest:
    return build_request(
        mesh, sources=[SourceBasis("source", 2, port_id="source")],
        channel_ports={"source": ["source"]}, frame=FRAME, frequencies_hz=frequencies,
        precision=precision, engine_id=f"beat-{backend}", layout=build_observations(angle_range=angles, distance_m=distance,
                                                      precision=precision, sphere_grid=None))


def exterior_request(backend: str = "cpu") -> CompiledRequest:
    return _request(PROBE_MESH, (300., 500.), precision="float32", distance=1., angles=(0., 90., 4), backend=backend)


def sphere_request() -> CompiledRequest:
    vertices, faces = icosphere(SPHERE_RADIUS_M, SPHERE_SUBDIVISIONS)
    return _request(gmsh_surface(vertices, faces), (1000.,), precision="float64",
                    distance=SPHERE_OBSERVATION_M, angles=(0., 180., 5))


def score_exterior(result: SweepResult) -> dict[str, Any]:
    failures = []
    if result.pressure_complex.shape != (2, 2, 4):
        failures.append("exterior pressure shape differs from (2, 2, 4)")
    if (not result.pressure_complex.size or not np.isfinite(result.pressure_complex).all()
            or not np.any(np.abs(result.pressure_complex)) or not np.isfinite(result.impedance).all()):
        failures.append("exterior pressure/impedance is empty, nonfinite or zero")
    offsets = result.spl_db - result.directivity_db
    spread = float(np.max(np.abs(offsets - offsets[:, :, :1]))) if offsets.size else None
    if spread is None or not np.isfinite(spread) or spread > 1e-9:
        failures.append("directivity is not a per-cut offset of absolute SPL")
    reference = result.directivity_reference_index
    if (result.directivity_db.shape != (2, 2, 4) or not 0 <= reference < 4
            or not np.allclose(result.directivity_db[:, :, reference], 0., atol=1e-12, rtol=0)):
        failures.append("directivity is not zero at the reference angle")
    resistance = (-1j * 2 * np.pi * result.frequencies_hz * result.impedance).real
    if not np.all(resistance > 0):
        failures.append("radiating source has non-positive resistance")
    return {"passed": not failures, "failures": failures,
            "max_directivity_offset_spread_db": spread, "radiation_resistance": resistance.tolist()}


def all_cases() -> tuple[ConformanceCase, ...]:
    return (
        ConformanceCase("capability_refusals", "Request construction and observed refusal reasons",
                        static_check=capability_report),
        ConformanceCase("exterior_contract_two_frequencies", "Exterior completion, SPL and resistance",
                        (300., 500.), min_solved_count=2, make_request=exterior_request, accept=score_exterior),
        ConformanceCase("analytic_pulsating_sphere_phase", "Absolute magnitude, phase and failing controls",
                        (1000.,), precision="float64", min_solved_count=1, make_request=sphere_request,
                        accept=lambda result: score_sphere(result.pressure_complex)),
        ConformanceCase("metal_exterior_float32", "Metal exterior completion, SPL and resistance",
                        (300., 500.), backend="metal", min_solved_count=2,
                        make_request=lambda: exterior_request("metal"), accept=score_exterior),
    )

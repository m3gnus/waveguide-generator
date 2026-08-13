"""Deterministic, explicitly gated solve-pipeline exercise engine."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from server.jobs.models import PolarConfig
from server.solver.acoustics import solver_sound_speed_m_per_s
from server.solver.beam_shape import beam_shape_summary
from server.solver.contract import build_directivity_metadata
from server.solver.directivity_index import (
    calculate_di_from_polar_patterns,
    calculate_plane_di_from_polar_patterns,
    calculate_di_from_spherical_grid,
)


def _canonical_seed(design: Mapping[str, Any]) -> tuple[str, float]:
    encoded = json.dumps(design, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return digest, int(digest[:8], 16) / 0xFFFFFFFF


def _frequency_axis(start: float, stop: float, count: int, spacing: str) -> list[float]:
    try:
        start = float(start)
        stop = float(stop)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("dry-run frequency bounds must be finite numbers") from exc
    if not math.isfinite(start) or not math.isfinite(stop):
        raise ValueError("dry-run frequency bounds must be finite")
    if start <= 0.0 or stop <= start:
        raise ValueError("dry-run frequency bounds must be positive and increasing")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 401:
        raise ValueError("dry-run frequency count must be an integer from 1 to 401")
    if spacing not in {"linear", "log"}:
        raise ValueError("dry-run frequency spacing must be 'linear' or 'log'")
    if count == 1:
        return [round(float(start), 6)]
    if spacing == "linear":
        step = (stop - start) / (count - 1)
        return [round(start + index * step, 6) for index in range(count)]
    ratio = (stop / start) ** (1.0 / (count - 1))
    return [round(start * ratio**index, 6) for index in range(count)]


def _explicit_frequency_axis(frequencies_hz: Sequence[float]) -> list[float]:
    """Validate a caller-supplied sweep with the same strictness as the grid path."""

    try:
        values = [float(value) for value in frequencies_hz]
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("dry-run explicit frequencies must be finite numbers") from exc
    if not values:
        raise ValueError("dry-run explicit frequencies must not be empty")
    if len(values) > 401:
        raise ValueError("dry-run explicit frequencies must number at most 401")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("dry-run explicit frequencies must be finite")
    if any(value <= 0.0 for value in values):
        raise ValueError("dry-run explicit frequencies must be positive")
    if any(later <= earlier for earlier, later in zip(values, values[1:])):
        raise ValueError("dry-run explicit frequencies must be strictly ascending")
    return [round(value, 6) for value in values]


class DryRunEngine:
    """Build canned-but-plausible results that vary stably with the design."""

    name = "dryrun"

    def mesh_artifact(self, design: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        """Return a tiny valid-looking deterministic Gmsh 2.2 artifact."""

        digest, seed = _canonical_seed(design)
        scale = 0.08 + seed * 0.04
        msh = (
            "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"
            "$Nodes\n3\n"
            f"1 0 0 0\n2 {scale:.9f} 0 0\n3 0 {scale:.9f} 0\n"
            "$EndNodes\n$Elements\n1\n1 2 2 1 1 1 2 3\n$EndElements\n"
            f"// Waveguide Generator dryrun design sha256={digest}\n"
        )
        stats = {
            "vertex_count": 3,
            "triangle_count": 1,
            "source": "dryrun",
            "design_sha256": digest,
        }
        return msh, stats

    def solve(
        self,
        design: Mapping[str, Any],
        *,
        frequency_start_hz: float,
        frequency_end_hz: float,
        num_frequencies: int,
        frequency_spacing: str,
        frequencies_hz: Sequence[float] | None = None,
        polar_config: Mapping[str, Any] | None = None,
        mesh_validation_mode: str = "warn",
        verbose: bool = False,
    ) -> dict[str, Any]:
        """Produce the v1 mapper-shaped primary result bundle.

        The keys mirror v1 ``server/solver/result_mapping.py:340-355``. Values
        are synthetic and must never be interpreted as acoustic predictions.
        """

        digest, seed = _canonical_seed(design)
        if frequencies_hz is None:
            frequencies = _frequency_axis(
                frequency_start_hz, frequency_end_hz, num_frequencies, frequency_spacing
            )
        else:
            frequencies = _explicit_frequency_axis(frequencies_hz)
        polar = PolarConfig.model_validate(polar_config or {}).model_dump(mode="json")
        angle_start, angle_end, angle_count = polar["angle_range"]
        angles = [
            float(angle_start + (angle_end - angle_start) * index / (angle_count - 1))
            for index in range(angle_count)
        ]
        spl: list[float] = []
        phase: list[float] = []
        impedance_real: list[float] = []
        impedance_imaginary: list[float] = []
        patterns: dict[str, list[list[list[float]]]] = {
            axis: [] for axis in polar["enabled_axes"]
        }
        sim_type = (
            (design.get("simulation") or {}).get("sim_type")
            if isinstance(design.get("simulation"), Mapping)
            else None
        )
        hemisphere = sim_type == "infinite-baffle"
        corner = 700.0 + seed * 900.0
        distance_gain = -20.0 * math.log10(float(polar["distance"]) / 2.0)
        origin_gain = 0.125 if polar["observation_origin"] == "throat" else 0.0

        for frequency in frequencies:
            octaves = math.log2(max(frequency, 1.0) / 1000.0)
            ripple = 1.6 * math.sin(2.15 * octaves + seed * math.pi)
            level = 96.0 + seed * 3.0 + 1.25 * octaves + ripple + distance_gain + origin_gain
            spl.append(round(level, 4))
            phase.append(round(((42.0 * octaves + seed * 70.0 + 180.0) % 360.0) - 180.0, 4))
            resonance = math.exp(-((math.log(max(frequency, 1.0) / corner)) ** 2) / 0.42)
            impedance_real.append(round(1.0 + 0.42 * resonance, 5))
            impedance_imaginary.append(
                round(0.28 * math.sin(math.log(max(frequency, 1.0) / corner)) * resonance, 5)
            )

            beam_h = max(18.0, 92.0 / (1.0 + (frequency / (1500.0 + seed * 400.0)) ** 0.72))
            beam_v = max(14.0, beam_h * (0.73 + seed * 0.08))
            inclination = math.radians(float(polar["inclination"]))
            beam_by_axis = {
                "horizontal": (beam_h, 1.75),
                "vertical": (beam_v, 1.8),
                "diagonal": (
                    math.sqrt(
                        (beam_h * math.cos(inclination)) ** 2
                        + (beam_v * math.sin(inclination)) ** 2
                    ),
                    1.775,
                ),
            }
            for axis in polar["enabled_axes"]:
                beam, exponent = beam_by_axis[axis]
                reference = -12.0 * (abs(float(polar["norm_angle"])) / beam) ** exponent
                patterns[axis].append(
                    [
                        [
                            angle,
                            round(-12.0 * (abs(angle) / beam) ** exponent - reference, 4),
                        ]
                        for angle in angles
                    ]
                )
        directivity_index = calculate_di_from_polar_patterns(
            patterns,
            hemisphere=hemisphere,
        )
        plane_approximation = not any(value is not None for value in directivity_index)
        di_payload: list[float | None] | dict[str, list[float | None]]
        if plane_approximation:
            di_payload = calculate_plane_di_from_polar_patterns(patterns)
        else:
            di_payload = directivity_index

        observation = {
            "requested_distance_m": float(polar["distance"]),
            "effective_distance_m": float(polar["distance"]),
            "sound_speed_m_per_s": solver_sound_speed_m_per_s("hornlab_metal_bem"),
        }
        polar_limit = 90.0 if hemisphere else 180.0
        measured_both_orbit_sides = (
            float(angle_start) <= -polar_limit and float(angle_end) >= polar_limit
        ) or float(angle_end) - float(angle_start) >= 2.0 * polar_limit
        metadata: dict[str, Any] = {
            "engine": "dryrun",
            "synthetic": True,
            "design_sha256": digest,
            "warnings": ["Dry-run data is synthetic and is not an acoustic prediction."],
            "warning_count": 1,
            "failures": [],
            "failure_count": 0,
            "partial_success": False,
            "impedance_units": "Z/(rho*c)",
            "impedance_quantity": "specific_acoustic_impedance",
            "frequency_spacing": (
                "explicit" if frequencies_hz is not None else frequency_spacing
            ),
            "frequency_source": (
                "explicit_list" if frequencies_hz is not None else "generated_grid"
            ),
            "mesh_validation": {
                "mode": mesh_validation_mode,
                "valid": True,
                "synthetic": True,
            },
            "verbose": bool(verbose),
            "directivity": build_directivity_metadata(polar, observation),
            "directivity_index": {
                "available": (
                    any(value is not None for values in di_payload.values() for value in values)
                    if isinstance(di_payload, dict)
                    else any(value is not None for value in di_payload)
                ),
                "definition": (
                    "10log10(2 / integral(relative mean-square pressure * sin(theta) dtheta))"
                    if plane_approximation
                    else "10log10(reference-axis mean-square pressure / full-sphere mean-square pressure)"
                ),
                "method": (
                    "per_plane_axisymmetric_approximation"
                    if plane_approximation
                    else "horizontal_vertical_orbit_approximation"
                ),
                "domain": (
                    "per_plane_axisymmetric_approximation" if plane_approximation else "full_sphere"
                ),
                "power_average": "linear_mean_square_pressure",
                "planes_used": [
                    plane
                    for plane in patterns
                    if not isinstance(di_payload, dict)
                    or any(value is not None for value in di_payload.get(plane, []))
                ],
                "opposing_orbit_sides": (
                    "not_applicable"
                    if plane_approximation
                    else "measured" if measured_both_orbit_sides else "mirrored"
                ),
                "rear_hemisphere": (
                    "implicit_in_plane_integral"
                    if plane_approximation
                    else "zero_radiation" if hemisphere else "sampled"
                ),
            },
        }
        response: dict[str, Any] = {
            "frequencies": frequencies,
            "directivity": patterns,
            "directivity_phase": {},
            "spl_on_axis": {
                "frequencies": frequencies,
                "spl": spl,
                "phase_degrees": phase,
            },
            "impedance": {
                "frequencies": frequencies,
                "real": impedance_real,
                "imaginary": impedance_imaginary,
            },
            "di": {"frequencies": frequencies, "di": di_payload},
            "metadata": metadata,
        }

        if polar["spherical_sampling"]:
            theta_count = int(polar["spherical_theta_count"])
            phi_count = int(polar["spherical_phi_count"])
            theta_max = 90.0 if sim_type == "infinite-baffle" else 180.0
            theta = [theta_max * index / (theta_count - 1) for index in range(theta_count)]
            phi = [360.0 * index / phi_count for index in range(phi_count)]
            grids: list[list[list[float]]] = []
            for frequency in frequencies:
                beam_h = max(18.0, 92.0 / (1.0 + (frequency / (1500.0 + seed * 400.0)) ** 0.72))
                beam_v = max(14.0, beam_h * (0.73 + seed * 0.08))
                grid: list[list[float]] = []
                for theta_value in theta:
                    row = []
                    for phi_value in phi:
                        phi_rad = math.radians(phi_value)
                        inverse_beam_sq = (
                            math.cos(phi_rad) ** 2 / beam_h**2
                            + math.sin(phi_rad) ** 2 / beam_v**2
                        )
                        row.append(round(-12.0 * theta_value**1.8 * inverse_beam_sq**0.9, 4))
                    grid.append(row)
                grids.append(grid)
            hemisphere = theta_max <= 90.0
            response["balloon"] = {
                "frequencies": frequencies,
                "theta_deg": theta,
                "phi_deg": phi,
                "spl_norm_db": grids,
                "distance_m": float(polar["distance"]),
                "hemisphere": hemisphere,
            }
            directivity_index = calculate_di_from_spherical_grid(
                theta,
                phi,
                grids,
                hemisphere=hemisphere,
            )
            response["di"]["di"] = directivity_index
            metadata["directivity_index"].update(
                {
                    "available": any(value is not None for value in directivity_index),
                    "method": "spherical_grid",
                    "planes_used": [],
                    "opposing_orbit_sides": "not_applicable",
                }
            )
            summary = beam_shape_summary(theta, phi, grids, frequencies, hemisphere=hemisphere)
            if summary is not None:
                response["beam_shape"] = summary
            metadata["balloon_sampling"] = {
                "requested": True,
                "configured": True,
                "available": True,
                "status": "available",
            }
        else:
            metadata["balloon_sampling"] = {
                "requested": False,
                "configured": False,
                "available": False,
                "status": "disabled",
            }

        return response


__all__ = ["DryRunEngine"]

"""Deterministic, explicitly gated solve-pipeline exercise engine."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


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
            f"// WG2 dryrun design sha256={digest}\n"
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
    ) -> dict[str, Any]:
        """Produce the v1 mapper-shaped primary result bundle.

        The keys mirror v1 ``server/solver/result_mapping.py:340-355``. Values
        are synthetic and must never be interpreted as acoustic predictions.
        """

        digest, seed = _canonical_seed(design)
        frequencies = _frequency_axis(
            frequency_start_hz, frequency_end_hz, num_frequencies, frequency_spacing
        )
        angles = [float(value) for value in range(-90, 91, 5)]
        spl: list[float] = []
        phase: list[float] = []
        impedance_real: list[float] = []
        impedance_imaginary: list[float] = []
        horizontal: list[list[list[float]]] = []
        vertical: list[list[list[float]]] = []
        horizontal_di: list[float] = []
        vertical_di: list[float] = []
        corner = 700.0 + seed * 900.0

        for frequency in frequencies:
            octaves = math.log2(max(frequency, 1.0) / 1000.0)
            ripple = 1.6 * math.sin(2.15 * octaves + seed * math.pi)
            level = 96.0 + seed * 3.0 + 1.25 * octaves + ripple
            spl.append(round(level, 4))
            phase.append(round(((42.0 * octaves + seed * 70.0 + 180.0) % 360.0) - 180.0, 4))
            resonance = math.exp(-((math.log(max(frequency, 1.0) / corner)) ** 2) / 0.42)
            impedance_real.append(round(1.0 + 0.42 * resonance, 5))
            impedance_imaginary.append(
                round(0.28 * math.sin(math.log(max(frequency, 1.0) / corner)) * resonance, 5)
            )

            beam_h = max(18.0, 92.0 / (1.0 + (frequency / (1500.0 + seed * 400.0)) ** 0.72))
            beam_v = max(14.0, beam_h * (0.73 + seed * 0.08))
            h_pattern = [
                [angle, round(-12.0 * (abs(angle) / beam_h) ** 1.75, 4)] for angle in angles
            ]
            v_pattern = [
                [angle, round(-12.0 * (abs(angle) / beam_v) ** 1.8, 4)] for angle in angles
            ]
            horizontal.append(h_pattern)
            vertical.append(v_pattern)
            horizontal_di.append(round(3.0 + 10.0 * math.log10(180.0 / beam_h), 4))
            vertical_di.append(round(3.0 + 10.0 * math.log10(180.0 / beam_v), 4))

        return {
            "frequencies": frequencies,
            "directivity": {"horizontal": horizontal, "vertical": vertical},
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
            "di": {
                "frequencies": frequencies,
                "di": {"horizontal": horizontal_di, "vertical": vertical_di},
            },
            "metadata": {
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
                "balloon_sampling": {
                    "requested": False,
                    "configured": False,
                    "available": False,
                    "status": "disabled",
                },
            },
        }


__all__ = ["DryRunEngine"]

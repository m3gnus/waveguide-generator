"""Axisymmetric CircSym eligibility helpers for Waveguide Generator."""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np


VALID_SOLVER_MODES = {"full_3d", "circsym"}


def normalize_solver_mode(value: Any) -> str:
    raw = str(value or "full_3d").strip().lower().replace("-", "_")
    aliases = {
        "full": "full_3d",
        "3d": "full_3d",
        "full3d": "full_3d",
        "full_3d": "full_3d",
        "circ_sym": "circsym",
        "axisymmetric": "circsym",
        "axisym": "circsym",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in VALID_SOLVER_MODES:
        raise ValueError("solver_mode must be one of: full_3d, circsym.")
    return normalized


def solver_mode_from_request(request: Any) -> str:
    return normalize_solver_mode(getattr(request, "solver_mode", "full_3d"))


def _finite_number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


def circsym_axisymmetric_rejection_reasons(
    waveguide_params: Mapping[str, Any] | None,
) -> list[str]:
    params = waveguide_params if isinstance(waveguide_params, Mapping) else {}
    reasons: list[str] = []

    morph_target = _finite_number(params.get("morph_target", 0), None)
    if morph_target is None or not math.isclose(morph_target, 0.0, rel_tol=0.0, abs_tol=1.0e-9):
        reasons.append(f"morphTarget is {params.get('morph_target')!r}, not 0")

    enc_depth = _finite_number(params.get("enc_depth", 0.0), 0.0) or 0.0
    if enc_depth > 0.0:
        reasons.append(f"enclosure depth is {enc_depth:g} mm")

    return reasons


def validate_circsym_axisymmetric(waveguide_params: Mapping[str, Any] | None) -> None:
    params = waveguide_params if isinstance(waveguide_params, Mapping) else {}
    # CircSym cannot solve infinite baffle: the image-plane meridian seals the
    # mouth aperture into a driven closed cavity (omnidirectional near-zero-level
    # artifact). Guard here for a clear error before the mesher/solve.
    if str(params.get("sim_type", "2")).strip() == "1":
        raise ValueError(
            "CircSym does not support infinite baffle (Simulation Type = Infinite "
            "baffle): the image-plane meridian seals the mouth aperture. Use the "
            "full-3D solver for infinite baffle, or set Simulation Type to "
            "Free-standing."
        )
    reasons = circsym_axisymmetric_rejection_reasons(waveguide_params)
    if reasons:
        raise ValueError("CircSym requires a circular waveguide: " + "; ".join(reasons))


def reject_bempp_circsym_request(request: Any) -> None:
    if solver_mode_from_request(request) != "circsym":
        return
    raise ValueError(
        "CircSym requires the Metal backend. The BEMPP backend cannot solve "
        "axisymmetric CircSym requests; select solver_backend='metal' or use "
        "solver_mode='full_3d'."
    )

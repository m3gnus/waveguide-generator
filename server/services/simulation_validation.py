"""Validation helpers for simulation request flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from pydantic import ValidationError

from contracts import SimulationRequest, WaveguideParamsRequest
from services.solver_runtime import normalize_mesh_validation_mode


@dataclass(frozen=True)
class SimulationRequestValidation:
    mesh_strategy: str
    waveguide_params: Optional[Dict[str, Any]] = None


def validate_occ_adaptive_bem_shell(enc_depth: float, wall_thickness: float) -> None:
    """Adaptive BEM requires either enclosure volume or wall shell thickness."""
    if float(enc_depth) <= 0.0 and float(wall_thickness) <= 0.0:
        raise ValueError(
            "Adaptive BEM simulation requires a closed shell. "
            "Increase enclosure depth or wall thickness."
        )


def validate_submit_simulation_request(
    request: SimulationRequest,
) -> SimulationRequestValidation:
    triangle_count = len(request.mesh.indices) // 3
    if len(request.mesh.vertices) % 3 != 0:
        raise ValueError("Mesh vertices length must be divisible by 3.")
    if len(request.mesh.indices) % 3 != 0:
        raise ValueError("Mesh indices length must be divisible by 3.")
    if len(request.mesh.surfaceTags) != triangle_count:
        raise ValueError(
            f"Mesh surfaceTags length ({len(request.mesh.surfaceTags)}) "
            f"must match triangle count ({triangle_count})."
        )
    if not any(int(tag) == 2 for tag in request.mesh.surfaceTags):
        raise ValueError(
            "Mesh surfaceTags must include source tag 2 before solve submission."
        )
    if str(request.sim_type).strip() != "2":
        raise ValueError(
            "Only sim_type='2' (free-standing) is supported; "
            "infinite-baffle sim_type='1' was removed."
        )

    normalize_mesh_validation_mode(request.mesh_validation_mode)

    options = request.options if isinstance(request.options, dict) else {}
    mesh_opts = (
        options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
    )
    mesh_strategy = str(mesh_opts.get("strategy", "")).strip().lower()

    if mesh_strategy != "occ_adaptive":
        return SimulationRequestValidation(mesh_strategy=mesh_strategy)

    waveguide_params = mesh_opts.get("waveguide_params")
    if not isinstance(waveguide_params, dict):
        raise ValueError(
            "options.mesh.waveguide_params must be an object when "
            "options.mesh.strategy='occ_adaptive'."
        )

    try:
        validated_waveguide = WaveguideParamsRequest(**waveguide_params)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid options.mesh.waveguide_params: {exc.errors()}"
        ) from exc

    validate_occ_adaptive_bem_shell(
        validated_waveguide.enc_depth,
        validated_waveguide.wall_thickness,
    )

    normalized_waveguide_params = validated_waveguide.model_dump()
    # Active OCC solve path always builds full-domain meshes. Force quadrants=1234
    # at the submission boundary so the queued payload reflects the active contract.
    normalized_waveguide_params["quadrants"] = 1234

    return SimulationRequestValidation(
        mesh_strategy=mesh_strategy,
        waveguide_params=normalized_waveguide_params,
    )


def build_submit_simulation_request(
    request: SimulationRequest,
    validation: SimulationRequestValidation,
) -> SimulationRequest:
    """Build the request object that will actually be queued for submission."""
    if validation.mesh_strategy != "occ_adaptive" or not validation.waveguide_params:
        return request

    options = dict(request.options or {})
    mesh_opts = dict(options.get("mesh", {}))
    mesh_opts["waveguide_params"] = dict(validation.waveguide_params)
    options["mesh"] = mesh_opts
    return request.model_copy(update={"options": options}, deep=True)

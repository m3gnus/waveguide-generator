"""Validation helpers for simulation request flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from pydantic import ValidationError

from contracts import (
    SimulationRequest,
    WaveguideParamsRequest,
    normalize_contract_solver_backend,
)
from services.solver_runtime import normalize_mesh_validation_mode


@dataclass(frozen=True)
class SimulationRequestValidation:
    mesh_strategy: str
    waveguide_params: Optional[Dict[str, Any]] = None


def is_hornlab_mesher_strategy(mesh_strategy: str) -> bool:
    return str(mesh_strategy or "").strip().lower() == "hornlab_mesher"


def solver_backend_requires_full_domain_quadrants(solver_backend: Any) -> bool:
    return normalize_contract_solver_backend(solver_backend) == "bempp"


def solver_backend_requires_closed_shell(solver_backend: Any) -> bool:
    return normalize_contract_solver_backend(solver_backend) == "bempp"


def apply_solver_backend_quadrant_compatibility(
    request: SimulationRequest,
    solver_backend: Any,
) -> SimulationRequest:
    """Return a request whose HornLab mesher payload is compatible with backend limits."""
    if not solver_backend_requires_full_domain_quadrants(solver_backend):
        return request

    options = request.options if isinstance(request.options, dict) else {}
    mesh_opts = options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
    if not is_hornlab_mesher_strategy(str(mesh_opts.get("strategy", ""))):
        return request

    waveguide_params = mesh_opts.get("waveguide_params")
    if not isinstance(waveguide_params, dict):
        return request

    next_options = dict(options)
    next_mesh_opts = dict(mesh_opts)
    next_waveguide_params = dict(waveguide_params)
    next_waveguide_params["quadrants"] = 1234
    next_mesh_opts["waveguide_params"] = next_waveguide_params
    next_options["mesh"] = next_mesh_opts
    return request.model_copy(update={"options": next_options}, deep=True)


def validate_hornlab_mesher_bem_shell(enc_depth: float, wall_thickness: float) -> None:
    """HornLab mesher BEM requests require either enclosure volume or wall shell thickness."""
    if float(enc_depth) <= 0.0 and float(wall_thickness) <= 0.0:
        raise ValueError(
            "BEMPP simulation requires a closed shell. "
            "Increase enclosure depth or wall thickness."
        )


def validate_solver_backend_waveguide_compatibility(
    waveguide_params: Optional[Dict[str, Any]],
    solver_backend: Any,
) -> None:
    if not solver_backend_requires_closed_shell(solver_backend):
        return
    if not isinstance(waveguide_params, dict):
        return
    validate_hornlab_mesher_bem_shell(
        waveguide_params.get("enc_depth", 0.0),
        waveguide_params.get("wall_thickness", 0.0),
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

    if not is_hornlab_mesher_strategy(mesh_strategy):
        raise ValueError(
            "Simulation requests must use options.mesh.strategy='hornlab_mesher'. "
            "Client-side JS meshes are viewport-only and are not accepted as a solve pipeline."
        )

    waveguide_params = mesh_opts.get("waveguide_params")
    if not isinstance(waveguide_params, dict):
        raise ValueError(
            "options.mesh.waveguide_params must be an object when "
            "options.mesh.strategy='hornlab_mesher'."
        )

    try:
        validated_waveguide = WaveguideParamsRequest(**waveguide_params)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid options.mesh.waveguide_params: {exc.errors()}"
        ) from exc

    normalized_waveguide_params = validated_waveguide.model_dump()

    return SimulationRequestValidation(
        mesh_strategy=mesh_strategy,
        waveguide_params=normalized_waveguide_params,
    )


def build_submit_simulation_request(
    request: SimulationRequest,
    validation: SimulationRequestValidation,
) -> SimulationRequest:
    """Build the request object that will actually be queued for submission."""
    if not is_hornlab_mesher_strategy(validation.mesh_strategy) or not validation.waveguide_params:
        return request

    options = dict(request.options or {})
    mesh_opts = dict(options.get("mesh", {}))
    mesh_opts["strategy"] = "hornlab_mesher"
    mesh_opts["waveguide_params"] = dict(validation.waveguide_params)
    options["mesh"] = mesh_opts
    return request.model_copy(update={"options": options}, deep=True)

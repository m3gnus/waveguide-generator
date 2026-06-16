"""Validation helpers for simulation request flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from pydantic import ValidationError

from contracts import (
    SimulationRequest,
    WaveguideParamsRequest,
)
from services.solver_runtime import normalize_mesh_validation_mode


@dataclass(frozen=True)
class SimulationRequestValidation:
    mesh_strategy: str
    waveguide_params: Optional[Dict[str, Any]] = None


def normalize_waveguide_params_for_solver_backend(
    waveguide_params: Optional[Dict[str, Any]],
    solver_backend: str,
) -> Optional[Dict[str, Any]]:
    if waveguide_params is None:
        return None
    normalized = dict(waveguide_params)

    # No available BEM backend models a true infinite baffle, so an
    # infinite-baffle request (sim_type=1) is solved as free-standing
    # (sim_type=2). This rebuilds the mesh without the baffle surface and lets
    # the reduced-symmetry solve proceed instead of failing on the off-plane
    # mouth rim. See the infinite-baffle support investigation for re-enabling
    # a real half-space solve.
    sim_type_raw = normalized.get("sim_type")
    if sim_type_raw is not None:
        try:
            sim_type_int = int(float(sim_type_raw))
        except (TypeError, ValueError):
            sim_type_int = None
        if sim_type_int == 1:
            normalized["sim_type"] = 2
            # Half-symmetry quadrants (12 / 14) are preserved through the
            # coercion: the free-standing builder produces a valid half-model
            # mesh (open on the single cut plane) and the Metal solver reflects
            # it across that plane, so an IB-coerced half-model solves at the
            # reduced domain instead of falling back to the full domain.

    if str(solver_backend or "").strip().lower() == "bempp":
        normalized["quadrants"] = 1234
    return normalized


def is_hornlab_mesher_strategy(mesh_strategy: str) -> bool:
    return str(mesh_strategy or "").strip().lower() == "hornlab_mesher"


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
    if str(request.sim_type).strip() not in {"1", "2"}:
        raise ValueError("sim_type must be '1' (infinite-baffle) or '2' (free-standing).")

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

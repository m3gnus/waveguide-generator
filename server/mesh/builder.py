"""Full OCC solver-mesh construction through hornlab-waveguide-mesher.

This is the v2 equivalent of v1 ``server/solver/mesher_adapter.py:746-796``
and ``server/services/simulation_runner.py:430-489``.  It deliberately does
not consume the preview tessellation: the mesher writes the authoritative
Gmsh artifact and that artifact is parsed back for solver statistics/tags.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import meshio
import numpy as np

from server.design.schema import DesignConfig, Expr
from server.preview.translate import design_to_mesher_config
from server.solver.quadrants import FULL_DOMAIN_QUADRANTS, normalise_quadrants

from .cache import SolverMeshArtifactCache, SolverMeshCacheInfo
from .gmsh_worker import run_on_gmsh_worker
from .integrity import mesh_integrity_report, mesh_semantic_orientation_report


CANONICAL_SURFACE_TAGS = {1, 2, 3, 4, 12}
LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES = 18_000
SOLVER_MESH_CACHE_FORMAT_VERSION = 1

_solver_mesh_cache = SolverMeshArtifactCache()

CancelCallback = Callable[[], None]
ProgressCallback = Callable[[str, float, str], None]


def _number(value: Expr | None, fallback: float = 0.0) -> float:
    if value is None:
        return fallback
    if value.value is not None and math.isfinite(value.value):
        return float(value.value)
    try:
        parsed = float(value.raw or "")
    except (TypeError, ValueError):
        return fallback
    return parsed if math.isfinite(parsed) else fallback


def _strict_scalar(value: Expr | None, fallback: float, field: str) -> float:
    if value is None:
        return fallback
    if value.value is None or not math.isfinite(value.value):
        raise ValueError(f"{field} must be a finite scalar expression")
    return float(value.value)


def _option(options: Any, name: str, fallback: Any = None) -> Any:
    if isinstance(options, Mapping):
        return options.get(name, fallback)
    return getattr(options, name, fallback)


def _check_cancel(cancel_cb: CancelCallback | None) -> None:
    if cancel_cb is not None:
        cancel_cb()


def _progress(
    progress_cb: ProgressCallback | None,
    stage: str,
    progress: float,
    message: str,
) -> None:
    if progress_cb is not None:
        progress_cb(stage, float(progress), message)


def _solver_mesher_config(design: DesignConfig) -> dict[str, Any]:
    """Restore solver-domain controls hidden by preview translation.

    Preview always expands to 1234 quadrants; solve meshes preserve ATH's
    reduced-domain rules (v1 ``mesher_adapter.py:140-187,342-424``).
    """

    root = design.root
    if root.source.contours is not None and root.source.contours.strip():
        raise ValueError(
            "source contours are not supported by the HornLab solver-mesh path; "
            "only the single tag-2 throat source is implemented"
        )
    velocity = root.source.velocity
    if root.source.velocity_convention not in {None, "normal", "axial", "legacy"}:
        raise ValueError("source velocity convention must be normal, axial, or legacy")
    if root.source.velocity_convention in {None, "legacy"} and velocity is not None:
        raw_velocity = _number(velocity, 1.0)
        if raw_velocity not in {1.0, 2.0}:
            raise ValueError("source.velocity must be 1 (normal) or 2 (axial)")

    if root.enclosure is not None:
        # Reject a non-scalar depth here rather than letting the mesher decide
        # what a formula-valued box is.
        _strict_scalar(root.enclosure.depth, 0.0, "enclosure.depth")
    config = design_to_mesher_config(design)
    mesh = config.setdefault("mesh", {})
    quadrants = normalise_quadrants(
        _strict_scalar(root.mesh.quadrants, 1234.0, "mesh.quadrants")
    )
    mesh["quadrants"] = quadrants

    # A y-offset moves the y-cut rim away from its native symmetry plane.  V1
    # preserves the requested domain but omits that unsafe placement
    # (``mesher_adapter.py:147-156``).
    if quadrants in {1, 12} and abs(_number(root.mesh.vertical_offset)) > 0.0:
        mesh["verticalOffset"] = 0.0

    # No sharp-edge fallback here.  V1 flattened the enclosure roundover to zero
    # on any reduced domain whose edge radius reached its smallest margin
    # (``mesher_adapter.py:167-187``), because a roundover that ate its margin
    # used to leave a sliver front baffle that tore the symmetry-cut join open.
    # The mesher owns that clamp now (``_clamp_edge_roundover`` /
    # ``enclosure_box_bounds``), and it clamps to a smaller roundover instead of
    # discarding it -- so keeping the v1 rule here made a reduced solve model a
    # sharp box while the identical full-domain solve kept a 17 mm roundover.
    # That divergence is the bug, not the protection: the mesh the solver runs
    # on must not depend on which domain the resolver happened to pick.  If the
    # clamp ever regresses, ``off_plane_open_edge_count`` now reports the torn
    # seam directly rather than being pre-empted by a blunt geometry change.
    return config


def _symmetry_plane_axes(config: Mapping[str, Any]) -> tuple[int, ...]:
    """Return the coordinate axes a reduced mesh was cut on (0=x, 1=y).

    A reduced mesh is legitimately open along its cut planes; the solver's
    mirror closes it. Naming those axes lets the integrity report separate
    that from a genuine hole.
    """

    quadrants = normalise_quadrants((config.get("mesh") or {}).get("quadrants"))
    return {1: (0, 1), 12: (1,), 14: (0,), FULL_DOMAIN_QUADRANTS: ()}[quadrants]


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _solver_mesh_cache_key(design: DesignConfig) -> str:
    """Hash only inputs that can change the authoritative MSH artifact.

    Solve frequencies, polar sampling, and validation policy are intentionally
    absent: they operate after mesh generation.  Geometry and mesh options are
    already normalized by ``_solver_mesher_config``.
    """

    payload = {
        "cache_format_version": SOLVER_MESH_CACHE_FORMAT_VERSION,
        "artifact_options": {
            "format": "gmsh-msh-text",
            "units": "m",
        },
        "generator_versions": {
            "hornlab-waveguide-mesher": _distribution_version(
                "hornlab-waveguide-mesher"
            ),
            "gmsh": _distribution_version("gmsh"),
        },
        "mesher_config": _solver_mesher_config(design),
    }
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def clear_solver_mesh_cache() -> None:
    """Drop all reusable artifacts, for recovery or explicit recomputation."""

    _solver_mesh_cache.clear()


def solver_mesh_cache_info() -> SolverMeshCacheInfo:
    """Return bounded-cache usage without exposing mutable cache contents."""

    return _solver_mesh_cache.info()


def _triangles_and_tags(mesh: meshio.Mesh) -> tuple[np.ndarray, np.ndarray]:
    """Flatten Gmsh triangle blocks exactly as v1 ``mesher_adapter.py:427-441``."""

    triangles: list[np.ndarray] = []
    tags: list[np.ndarray] = []
    physical = mesh.cell_data.get("gmsh:physical") or mesh.cell_data.get("physical")
    for block_index, block in enumerate(mesh.cells):
        if block.type not in {"triangle", "triangle3"}:
            continue
        data = np.asarray(block.data, dtype=np.int64)
        triangles.append(data)
        if physical is not None and block_index < len(physical):
            tags.append(np.asarray(physical[block_index], dtype=np.int32))
        else:
            tags.append(np.ones(len(data), dtype=np.int32))
    if not triangles:
        return np.empty((0, 3), dtype=np.int64), np.empty((0,), dtype=np.int32)
    return np.vstack(triangles), np.concatenate(tags)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    return value


def _build_sync(
    design_dump: dict[str, Any],
    cancel_cb: CancelCallback | None,
) -> dict[str, Any]:
    """Build and inspect one artifact; called only by the gmsh worker."""

    try:
        from hornlab_mesher.config_builder import build_from_config
    except ImportError as exc:
        raise RuntimeError(
            "hornlab-waveguide-mesher is not installed; install the pinned server requirements"
        ) from exc

    design = DesignConfig.model_validate(design_dump)
    _check_cancel(cancel_cb)
    config = _solver_mesher_config(design)
    _check_cancel(cancel_cb)
    with tempfile.TemporaryDirectory(prefix="wg2-solver-mesh-") as temp_dir:
        mesh_path = Path(temp_dir) / "waveguide.msh"
        result = build_from_config(config, mesh_path)
        _check_cancel(cancel_cb)
        msh_text = mesh_path.read_text(encoding="utf-8", errors="replace")
        parsed = meshio.read(mesh_path)
        triangles, tags = _triangles_and_tags(parsed)
        vertices = np.asarray(parsed.points, dtype=float)
        _check_cancel(cancel_cb)

    if triangles.size == 0:
        raise RuntimeError(
            "hornlab-waveguide-mesher produced no triangular solver elements"
        )
    if (
        vertices.ndim != 2
        or vertices.shape[1:] != (3,)
        or not np.isfinite(vertices).all()
    ):
        raise RuntimeError(
            "hornlab-waveguide-mesher produced invalid or non-finite vertices"
        )
    unique_tags, unique_counts = np.unique(tags, return_counts=True)
    tag_count_values = {
        int(tag): int(count)
        for tag, count in zip(unique_tags, unique_counts, strict=True)
    }
    invalid_tags = sorted(set(tag_count_values) - CANONICAL_SURFACE_TAGS)
    if invalid_tags:
        raise RuntimeError(
            f"HornLab mesher returned unsupported surface tags: {invalid_tags}"
        )
    if 2 not in tag_count_values:
        raise RuntimeError("HornLab mesher returned no source-tagged elements (tag 2)")

    metadata = _json_safe(getattr(result, "metadata", None) or {})
    integrity = mesh_integrity_report(
        vertices,
        triangles,
        expected_volume_sign=(
            -1
            if str(config.get("mode", "")).strip().lower() == "infinite-baffle"
            else 1
        ),
        symmetry_plane_axes=_symmetry_plane_axes(config),
    )
    semantic_orientation = mesh_semantic_orientation_report(
        vertices,
        triangles,
        tags,
        mode=str(config.get("mode", "")),
    )
    integrity["semantic_orientation"] = semantic_orientation
    integrity["valid"] = bool(integrity["valid"] and semantic_orientation["valid"])
    tag_counts = {
        str(tag): tag_count_values.get(tag, 0) for tag in sorted(CANONICAL_SURFACE_TAGS)
    }
    bounds_min = np.min(vertices, axis=0)
    bounds_max = np.max(vertices, axis=0)
    domain_multiplier = float(metadata.get("meshDomainMultiplier", 1.0) or 1.0)
    if not math.isfinite(domain_multiplier) or domain_multiplier <= 0.0:
        domain_multiplier = 1.0
    triangle_count = int(len(triangles))
    max_edge_squared = 0.0
    for start, end in ((0, 1), (1, 2), (2, 0)):
        edge = vertices[triangles[:, end]] - vertices[triangles[:, start]]
        max_edge_squared = max(
            max_edge_squared,
            float(np.max(np.einsum("ij,ij->i", edge, edge))),
        )
    max_edge_mm = math.sqrt(max_edge_squared) * 1000.0
    scale = (
        _strict_scalar(root_scale, 1.0, "scale")
        if (root_scale := design.root.scale)
        else 1.0
    )
    max_edge_guard_mm = (
        _strict_scalar(design.root.mesh.max_edge, 0.0, "mesh.max_edge") * scale
        if design.root.mesh.max_edge is not None
        else None
    )
    if max_edge_guard_mm is not None:
        if max_edge_guard_mm <= 0.0:
            raise ValueError("mesh.max_edge must be greater than zero")
        if max_edge_mm > max_edge_guard_mm + 1.0e-9:
            raise RuntimeError(
                "Solver mesh exceeded mesh.max_edge guard: "
                f"{max_edge_mm:.6g} mm > {max_edge_guard_mm:.6g} mm"
            )
    full_domain_count = int(round(triangle_count * domain_multiplier))
    warnings: list[str] = []
    if full_domain_count > LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES:
        warnings.append(
            "Large solve mesh: "
            f"{triangle_count:,} triangles ({full_domain_count:,} full-domain equivalent). "
            "The solve may take significantly longer and use more memory."
        )
    if not integrity["valid"]:
        warnings.append(
            "Solver mesh contains invalid, degenerate, or non-manifold triangles."
        )
    off_plane_open_edges = int(integrity.get("off_plane_open_edge_count", 0))
    # Only the shell modes owe closure. A bare horn is an open sheet whose mouth
    # rim is free by construction (Metal disables its own open-edge guard for
    # exactly that reason), and the coupled infinite-baffle aperture has its own
    # contract check inside the mesher.
    if off_plane_open_edges and str(config.get("mode", "")).strip().lower() in {
        "enclosure",
        "freestanding",
    }:
        warnings.append(
            f"Solver mesh has {off_plane_open_edges:,} free edges away from its "
            "symmetry cut planes: the model is not closed, and sound will leak "
            "through the gap instead of being blocked by it."
        )

    stats = {
        "vertex_count": int(len(vertices)),
        "triangle_count": triangle_count,
        "tag_counts": tag_counts,
        "units": str(getattr(result, "units", "m")),
        "source": "hornlab_waveguide_mesher",
        "generated_by": "hornlab-waveguide-mesher",
        "bounds_m": {
            "min_x": float(bounds_min[0]),
            "min_y": float(bounds_min[1]),
            "min_z": float(bounds_min[2]),
            "max_x": float(bounds_max[0]),
            "max_y": float(bounds_max[1]),
            "max_z": float(bounds_max[2]),
        },
        "dimensions_m": {
            "width": float(bounds_max[0] - bounds_min[0]),
            "height": float(bounds_max[2] - bounds_min[2]),
            "depth": float(bounds_max[1] - bounds_min[1]),
        },
        "domain_multiplier": domain_multiplier,
        "full_domain_triangle_count": full_domain_count,
        "max_edge_mm": max_edge_mm,
        "max_edge_guard_mm": max_edge_guard_mm,
        "soft_warning_full_domain_triangle_limit": LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES,
        "warnings": warnings,
        "integrity": integrity,
    }
    return {
        "msh_text": msh_text,
        "stats": stats,
        "integrity": integrity,
        "metadata": metadata,
    }


async def build_solver_mesh(
    design: DesignConfig | Mapping[str, Any],
    options: Any,
    cancel_cb: CancelCallback | None = None,
    progress_cb: ProgressCallback | None = None,
    *,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    """Build the authoritative full OCC mesh on the single gmsh worker.

    ``cancel_cb`` is checked before configuration, immediately before/after the
    non-interruptible OCC call, and after parsing.  This matches v1's cooperative
    cancellation checkpoints at ``simulation_runner.py:347,438-443``.
    ``force_rebuild`` bypasses and refreshes the process-local artifact cache.
    """

    validated = (
        design
        if isinstance(design, DesignConfig)
        else DesignConfig.model_validate(design)
    )
    _check_cancel(cancel_cb)
    _progress(progress_cb, "mesh_prepare", 0.0, "Preparing HornLab solver mesh")
    cache_key = _solver_mesh_cache_key(validated)
    result = None if force_rebuild else _solver_mesh_cache.get(cache_key)
    cache_hit = result is not None
    if result is None:
        result = await run_on_gmsh_worker(
            _build_sync,
            validated.model_dump(mode="json"),
            cancel_cb,
        )
        _solver_mesh_cache.put(cache_key, result)
    _check_cancel(cancel_cb)
    _progress(progress_cb, "mesh_validate", 1.0, "Validated HornLab solver mesh")

    validation_mode = str(
        _option(options, "mesh_validation_mode", "warn") or "warn"
    ).lower()
    result["stats"]["mesh_cache_hit"] = cache_hit
    result["stats"]["mesh_cache_key"] = cache_key
    result["stats"]["mesh_validation_mode"] = validation_mode
    if validation_mode == "strict" and not result["integrity"]["valid"]:
        raise RuntimeError("Solver mesh failed strict topology integrity validation")
    if validation_mode == "off":
        result["stats"]["warnings"] = [
            warning
            for warning in result["stats"]["warnings"]
            if not warning.startswith("Solver mesh contains invalid")
        ]
    return result


__all__ = [
    "CANONICAL_SURFACE_TAGS",
    "LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES",
    "SOLVER_MESH_CACHE_FORMAT_VERSION",
    "build_solver_mesh",
    "clear_solver_mesh_cache",
    "solver_mesh_cache_info",
]

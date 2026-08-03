"""Full OCC solver-mesh construction through hornlab-waveguide-mesher.

This is the v2 equivalent of v1 ``server/solver/mesher_adapter.py:746-796``
and ``server/services/simulation_runner.py:430-489``.  It deliberately does
not consume the preview tessellation: the mesher writes the authoritative
Gmsh artifact and that artifact is parsed back for solver statistics/tags.
"""

from __future__ import annotations

import copy
import math
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import meshio
import numpy as np

from server.design.schema import DesignConfig, Expr
from server.preview.translate import design_to_mesher_config
from server.solver.quadrants import normalise_quadrants

from .gmsh_worker import run_on_gmsh_worker
from .integrity import mesh_integrity_report


CANONICAL_SURFACE_TAGS = {1, 2, 3, 4, 12}
LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES = 18_000

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

    enclosure_depth = (
        _strict_scalar(root.enclosure.depth, 0.0, "enclosure.depth")
        if root.enclosure is not None
        else 0.0
    )
    if root.simulation.sim_type == "infinite-baffle" and enclosure_depth > 0.0:
        raise ValueError(
            "Infinite baffle cannot be combined with an enclosure; set enclosure.depth=0 "
            "or use freestanding simulation mode."
        )

    config = copy.deepcopy(design_to_mesher_config(design))
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

    # A reduced enclosure roundover that consumes its smallest front margin can
    # tear the symmetry-cut join.  Use the same sharp-edge fallback as v1
    # ``mesher_adapter.py:167-187``.
    if root.enclosure is not None and enclosure_depth > 0.0 and quadrants != 1234:
        edge = _number(root.enclosure.edge_radius, 18.0)
        margins = [
            _number(root.enclosure.space_l),
            _number(root.enclosure.space_t),
            _number(root.enclosure.space_r),
            _number(root.enclosure.space_b),
        ]
        positive = [margin for margin in margins if margin > 0.0]
        if edge > 0.0 and positive and edge >= min(positive) - 1.0e-9:
            config.setdefault("enclosure", {})["edge"] = 0.0
    return config


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
        raise RuntimeError("hornlab-waveguide-mesher produced no triangular solver elements")
    if vertices.ndim != 2 or vertices.shape[1:] != (3,) or not np.isfinite(vertices).all():
        raise RuntimeError("hornlab-waveguide-mesher produced invalid or non-finite vertices")
    tag_values = [int(value) for value in tags.tolist()]
    invalid_tags = sorted(set(tag_values) - CANONICAL_SURFACE_TAGS)
    if invalid_tags:
        raise RuntimeError(f"HornLab mesher returned unsupported surface tags: {invalid_tags}")
    if 2 not in tag_values:
        raise RuntimeError("HornLab mesher returned no source-tagged elements (tag 2)")

    metadata = _json_safe(getattr(result, "metadata", None) or {})
    integrity = mesh_integrity_report(vertices, triangles)
    tag_counts = {str(tag): tag_values.count(tag) for tag in sorted(CANONICAL_SURFACE_TAGS)}
    bounds_min = np.min(vertices, axis=0)
    bounds_max = np.max(vertices, axis=0)
    domain_multiplier = float(metadata.get("meshDomainMultiplier", 1.0) or 1.0)
    if not math.isfinite(domain_multiplier) or domain_multiplier <= 0.0:
        domain_multiplier = 1.0
    triangle_count = int(len(triangles))
    full_domain_count = int(round(triangle_count * domain_multiplier))
    warnings: list[str] = []
    if full_domain_count > LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES:
        warnings.append(
            "Large solve mesh: "
            f"{triangle_count:,} triangles ({full_domain_count:,} full-domain equivalent). "
            "The solve may take significantly longer and use more memory."
        )
    if not integrity["valid"]:
        warnings.append("Solver mesh contains invalid, degenerate, or non-manifold triangles.")

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
        "soft_warning_full_domain_triangle_limit": LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES,
        "warnings": warnings,
        "integrity": integrity,
    }
    return {
        "msh_text": msh_text,
        "stats": stats,
        "integrity": integrity,
        "metadata": metadata,
        "canonical_mesh": {
            "vertices": vertices.reshape(-1).tolist(),
            "indices": triangles.reshape(-1).astype(int).tolist(),
            "surfaceTags": tag_values,
            "metadata": {
                "units": "m",
                "unitScaleToMeter": 1.0,
                "tagCounts": tag_counts,
                "generatedBy": "hornlab-waveguide-mesher",
                "mesherMetadata": metadata,
            },
        },
    }


async def build_solver_mesh(
    design: DesignConfig | Mapping[str, Any],
    options: Any,
    cancel_cb: CancelCallback | None = None,
    progress_cb: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Build the authoritative full OCC mesh on the single gmsh worker.

    ``cancel_cb`` is checked before configuration, immediately before/after the
    non-interruptible OCC call, and after parsing.  This matches v1's cooperative
    cancellation checkpoints at ``simulation_runner.py:347,438-443``.
    """

    validated = design if isinstance(design, DesignConfig) else DesignConfig.model_validate(design)
    _check_cancel(cancel_cb)
    _progress(progress_cb, "mesh_prepare", 0.0, "Preparing HornLab solver mesh")
    result = await run_on_gmsh_worker(
        _build_sync,
        validated.model_dump(mode="json"),
        cancel_cb,
    )
    _check_cancel(cancel_cb)
    _progress(progress_cb, "mesh_validate", 1.0, "Validated HornLab solver mesh")

    validation_mode = str(_option(options, "mesh_validation_mode", "warn") or "warn").lower()
    if validation_mode == "strict" and not result["integrity"]["valid"]:
        raise RuntimeError("Solver mesh failed strict topology integrity validation")
    return result


__all__ = [
    "CANONICAL_SURFACE_TAGS",
    "LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES",
    "build_solver_mesh",
]

"""Full OCC solver-mesh construction through hornlab-waveguide-mesher.

This is the v2 equivalent of v1 ``server/solver/mesher_adapter.py:746-796``
and ``server/services/simulation_runner.py:430-489``.  It deliberately does
not consume the preview tessellation: the mesher writes the authoritative
Gmsh artifact and that artifact is parsed back for solver statistics/tags.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.metadata
import json
import math
import tempfile
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from server.design.schema import DesignConfig, Expr
from server.preview.translate import design_to_mesher_config
from server.solver.quadrants import FULL_DOMAIN_QUADRANTS, normalise_quadrants

from .cache import SolverMeshArtifactCache, SolverMeshCacheInfo
from .gmsh_worker import run_on_gmsh_worker
from .integrity import (
    mesh_integrity_report,
    mesh_self_intersection_report,
    mesh_semantic_orientation_report,
)


if TYPE_CHECKING:  # pragma: no cover - meshio is imported where it is used
    import meshio


CANONICAL_SURFACE_TAGS = {1, 2, 3, 4, 12}
MOUTH_APERTURE_SURFACE_TAG = 12
LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES = 18_000
# The mesher can estimate and count triangles, but it cannot know the P1
# vertex/DOF count that governs the dense solver's memory. Keep this loose
# actual-domain limit as an artifact/preflight sanity check; the independent
# vertex/DOF guard below is the authoritative dense-memory safety limit.
MAX_SOLVER_MESH_ARTIFACT_TRIANGLES = 22_000
DENSE_SOLVER_MEMORY_LIMIT_BYTES = 8 * 1024**3
# 2: integrity now carries a self_intersection report.
SOLVER_MESH_CACHE_FORMAT_VERSION = 2

_solver_mesh_cache = SolverMeshArtifactCache()

CancelCallback = Callable[[], None]
ProgressCallback = Callable[[str, float, str], None]


def _strict_scalar(value: Expr | None, fallback: float, field: str) -> float:
    if value is None:
        return fallback
    number = value.constant_value()
    if number is None or not math.isfinite(number):
        raise ValueError(f"{field} must be a finite scalar expression")
    return float(number)


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


def _solver_mesher_config(
    design: DesignConfig, *, keep_placement: bool = False
) -> dict[str, Any]:
    """Restore solver-domain controls hidden by preview translation.

    Preview always expands to 1234 quadrants; solve meshes preserve ATH's
    reduced-domain rules (v1 ``mesher_adapter.py:140-187,342-424``).

    ``keep_placement`` is for the CAD exports only: they are the one consumer
    that wants ``mesh.vertical_offset`` applied (see below).
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
        raw_velocity = _strict_scalar(velocity, 1.0, "source.velocity")
        if raw_velocity not in {1.0, 2.0}:
            raise ValueError("source.velocity must be 1 (normal) or 2 (axial)")

    if root.enclosure is not None:
        # Reject a non-scalar depth here rather than letting the mesher decide
        # what a formula-valued box is.
        _strict_scalar(root.enclosure.depth, 0.0, "enclosure.depth")
    config = design_to_mesher_config(design)
    mesh = config.setdefault("mesh", {})
    quadrants, _ = _resolve_quadrants(design)
    mesh["quadrants"] = quadrants

    # ``mesh.vertical_offset`` is a rigid +y placement the mesher applies after
    # every cut plane has run at the origin.  The solve mesh is always built in
    # that recentred origin frame, whatever domain the resolver picked, so
    # results, mesh artifacts and field traces cannot depend on the choice --
    # and they could not follow the placement anyway, because the solver's
    # symmetry images only support mirror planes through the origin, which pins
    # a y-cut reduced mesh to y=0 forever.  A translation of the whole assembly
    # is acoustically inert (the observation frame is derived from the solved
    # mesh), so nothing is lost: the placement is applied only at the CAD
    # boundary, by the solid STEP and the CAD-link exports.
    _strict_scalar(root.mesh.vertical_offset, 0.0, "mesh.vertical_offset")
    if not keep_placement:
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


def _resolve_quadrants(design: DesignConfig) -> tuple[int, str | None]:
    """Resolve ATH quadrant semantics and describe any compatibility fallback."""

    declared_expr = design.root.mesh.quadrants
    declared = _strict_scalar(declared_expr, 1234.0, "mesh.quadrants")
    resolved = normalise_quadrants(declared)
    if declared_expr is None or declared == float(resolved):
        return resolved, None
    # normalise_quadrants truncates before matching, so a declared 12.5 lands on
    # the half-domain 12 rather than the quarter-domain fallback. Name the domain
    # actually solved instead of assuming the fallback.
    domain = {
        1: "a quarter-domain",
        12: "a half-domain (xz)",
        14: "a half-domain (yz)",
        FULL_DOMAIN_QUADRANTS: "a full-domain",
    }[resolved]
    return resolved, (
        f"mesh.quadrants was declared as {declared_expr.text()!r}, which ATH "
        f"compatibility resolves to {resolved}; the solver used {domain} mesh. "
        "Use 1, 12, 14, or 1234 to select the intended solve domain explicitly."
    )


def _domain_multiplier_for_quadrants(quadrants: int) -> int:
    return {1: 4, 12: 2, 14: 2, FULL_DOMAIN_QUADRANTS: 1}[quadrants]


def _mesh_policy(
    design: DesignConfig,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], int, int]:
    """Apply the non-bypassable artifact sanity cap to a mesher config.

    ``mesh.max_triangles`` remains the user's full-domain-equivalent warning
    threshold. The mesher still needs a hard limit, so a private copy replaces
    that threshold with the actual-domain artifact cap converted to its
    full-domain convention. Its estimate gate applies its own 1.25 safety
    margin; the exact 22,000-triangle limit is repeated on the parsed artifact.
    The legacy ``allow_large_mesh`` field is accepted on import and the wire,
    but cannot bypass either check. Dense memory is checked from used P1
    vertices after parsing because triangle counts cannot predict it.
    """

    warning_limit = int(
        round(
            _strict_scalar(
                design.root.mesh.max_triangles,
                float(LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES),
                "mesh.max_triangles",
            )
        )
    )
    if warning_limit <= 0:
        raise ValueError("mesh.max_triangles must be greater than zero")
    quadrants = normalise_quadrants((config.get("mesh") or {}).get("quadrants"))
    multiplier = _domain_multiplier_for_quadrants(quadrants)
    guarded = deepcopy(dict(config))
    guarded_mesh = guarded.setdefault("mesh", {})
    guarded_mesh["maxTriangles"] = (
        MAX_SOLVER_MESH_ARTIFACT_TRIANGLES * multiplier
    )
    guarded_mesh["allowLargeMesh"] = False
    return guarded, warning_limit, multiplier


def _enforce_artifact_triangle_ceiling(triangle_count: int) -> None:
    """Repeat the mesher artifact sanity check on the parsed output."""

    if triangle_count > MAX_SOLVER_MESH_ARTIFACT_TRIANGLES:
        raise RuntimeError(
            "Solver mesh contains "
            f"{triangle_count:,} actual-domain triangles, exceeding the "
            "non-bypassable solver-artifact sanity ceiling of "
            f"{MAX_SOLVER_MESH_ARTIFACT_TRIANGLES:,}. Coarsen the relevant "
            "mm mesh resolution before solving."
        )


def _dense_solver_memory_requirements(
    triangles: np.ndarray,
    quadrants: int,
    *,
    mode: str = "",
    tags: np.ndarray | None = None,
) -> dict[str, int]:
    """Return conservative Metal and symmetry-expanded BEMPP storage.

    Dense-system dimensions follow used P1 vertices, not triangle count. The
    11 complex64 dense systems cover the concurrent Metal workload. Coupled
    infinite-baffle Metal adds one P0 velocity unknown per aperture triangle.
    A BEMPP symmetry solve additionally operates on mirrored full geometry and
    keeps three reduced-row by full-trial arrays. Taking the larger backend
    estimate prevents symmetry or aperture coupling from understating memory.
    """

    used_vertex_count = int(np.unique(triangles).size)
    multiplier = _domain_multiplier_for_quadrants(normalise_quadrants(quadrants))
    aperture_triangle_count = 0
    if (
        str(mode).strip().lower() == "infinite-baffle"
        and tags is not None
    ):
        # This is a physical-group contract, not optional mesher metadata. A
        # missing or spoofed apertureTag value therefore cannot undercount the
        # coupled P0 unknowns.
        aperture_triangle_count = int(
            np.count_nonzero(tags == MOUTH_APERTURE_SURFACE_TAG)
        )
    metal_dof_count = used_vertex_count + aperture_triangle_count
    metal_bytes = 11 * 8 * metal_dof_count**2
    bempp_bytes = 3 * 8 * multiplier * used_vertex_count**2
    return {
        "used_vertex_count": used_vertex_count,
        "aperture_triangle_count": aperture_triangle_count,
        "metal_dof_count": metal_dof_count,
        "domain_multiplier": multiplier,
        "metal_bytes_per_dof_squared": 11 * 8,
        "bempp_bytes_per_vertex_squared": 3 * 8 * multiplier,
        "metal_bytes": metal_bytes,
        "bempp_bytes": bempp_bytes,
        "estimated_bytes": max(metal_bytes, bempp_bytes),
    }


def _enforce_dense_solver_memory_ceiling(
    triangles: np.ndarray,
    quadrants: int,
    *,
    mode: str = "",
    tags: np.ndarray | None = None,
) -> dict[str, int]:
    """Fail before assembly when conservative dense storage exceeds 8 GiB."""

    requirements = _dense_solver_memory_requirements(
        triangles,
        quadrants,
        mode=mode,
        tags=tags,
    )
    used_vertex_count = requirements["used_vertex_count"]
    estimated_bytes = requirements["estimated_bytes"]
    if estimated_bytes > DENSE_SOLVER_MEMORY_LIMIT_BYTES:
        multiplier = _domain_multiplier_for_quadrants(normalise_quadrants(quadrants))
        coupled = (
            f" plus {requirements['aperture_triangle_count']:,} aperture P0 DOFs"
            if requirements["aperture_triangle_count"]
            else ""
        )
        raise RuntimeError(
            "Solver mesh uses "
            f"{used_vertex_count:,} P1 vertex/DOFs{coupled} in a "
            f"{multiplier}x symmetry domain, with a conservative dense-solver "
            "memory estimate of "
            f"{estimated_bytes / 1024**3:.2f} GiB. This exceeds the "
            f"{DENSE_SOLVER_MEMORY_LIMIT_BYTES / 1024**3:.0f} GiB safety "
            "ceiling; coarsen the relevant mm mesh resolution before solving."
        )
    return requirements


def _require_closed_acoustic_topology(
    config: Mapping[str, Any], integrity: Mapping[str, Any]
) -> None:
    """Reject leaks and invalid topology for models that claim to be closed."""

    mode = str(config.get("mode", "")).strip().lower()
    if mode not in {"enclosure", "freestanding", "infinite-baffle"}:
        return
    off_plane_open_edges = int(integrity.get("off_plane_open_edge_count", 0))
    invalid = not bool(integrity.get("valid", False))
    if not invalid and off_plane_open_edges == 0:
        return
    reasons = []
    if invalid:
        reasons.append("invalid, degenerate, non-manifold, or misoriented topology")
    if off_plane_open_edges:
        reasons.append(
            f"{off_plane_open_edges:,} free edges away from symmetry cut planes"
        )
    raise RuntimeError(
        f"Closed {mode} acoustic model rejected: " + " and ".join(reasons) + "."
    )


SELF_INTERSECTION_WARNING_PREFIX = "Solver mesh self-intersects"


def _self_intersection_warnings(report: Mapping[str, Any]) -> list[str]:
    """Describe overlapping triangles without guessing which surfaces they are.

    The detector sees geometry, not roles: every rigid surface shares one
    physical tag, so the message names the count, the size and the place, and
    offers the free-standing thin-wall case as the usual cause rather than
    asserting it.
    """

    if not report.get("checked"):
        return []
    crossings = int(report.get("proper_crossing_count", 0))
    coplanar = int(report.get("coplanar_overlap_count", 0))
    if crossings + coplanar == 0:
        return []
    samples = report.get("samples") or []
    where = ""
    if samples:
        worst = samples[0]
        x, y, z = (float(value) * 1000.0 for value in worst["location_m"])
        where = (
            f", the largest spanning {float(worst['extent_m']) * 1000.0:.1f} mm "
            f"near (x {x:.1f}, y {y:.1f}, z {z:.1f}) mm"
        )
    overlaps = f"{crossings:,} triangle pairs cross"
    if coplanar:
        overlaps += f" and {coplanar:,} overlap in-plane"
    return [
        f"{SELF_INTERSECTION_WARNING_PREFIX}: {overlaps}{where}. Surfaces that "
        "pass through each other are not the boundary the design describes, so "
        "the solved field is not the field of the intended geometry. On a "
        "free-standing waveguide this is usually a thin wall meshed at a coarse "
        "rear resolution: increase Wall Thickness or reduce Rear Resolution."
    ]

def _large_mesh_warnings(
    *,
    triangle_count: int,
    full_domain_count: int,
    budget_full_domain: int,
    domain_multiplier: float,
    metadata: Mapping[str, Any],
) -> list[str]:
    """Describe an advisory budget overrun without deciding whether to solve."""

    if full_domain_count <= budget_full_domain:
        return []
    effective_budget = max(1, int(round(budget_full_domain / domain_multiplier)))
    dominant = metadata.get("meshTriangleDominantRegion")
    dominant_mm = metadata.get("meshTriangleDominantTargetMm")
    advice = (
        f" The heaviest region is the {dominant} at {float(dominant_mm):.3g} mm."
        if dominant and isinstance(dominant_mm, (int, float))
        else ""
    )
    return [
        "Large solve mesh: "
        f"{triangle_count:,} triangles against a warning threshold of "
        f"{effective_budget:,} ({full_domain_count:,} vs {budget_full_domain:,} "
        "full-domain equivalent). The solve will continue, but may take "
        "significantly longer and use more memory."
        f"{advice} Coarsen that mm resolution or raise mesh.max_triangles."
    ]


def _symmetry_plane_axes(config: Mapping[str, Any]) -> tuple[int, ...]:
    """Return the coordinate axes a reduced mesh was cut on (0=x, 1=y).

    A reduced mesh is legitimately open along its cut planes; the solver's
    mirror closes it. Naming those axes lets the integrity report separate
    that from a genuine hole.
    """

    quadrants = normalise_quadrants((config.get("mesh") or {}).get("quadrants"))
    return {1: (0, 1), 12: (1,), 14: (0,), FULL_DOMAIN_QUADRANTS: ()}[quadrants]


@functools.lru_cache(maxsize=None)
def _distribution_version(name: str) -> str:
    # Uncached this walked sys.path looking for *.dist-info twice per mesh
    # build -- including on a cache hit, because it feeds the cache key itself.
    # An installed version cannot change inside a running process.
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


def _triangles_and_tags(mesh: "meshio.Mesh") -> tuple[np.ndarray, np.ndarray]:
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
    # Deferred with the mesher for the same reason: meshio drags in its CLI and
    # rich, and only a real build ever needs to parse an artifact.
    import meshio

    design = DesignConfig.model_validate(design_dump)
    _check_cancel(cancel_cb)
    config, warning_limit_full_domain, expected_domain_multiplier = _mesh_policy(
        design, _solver_mesher_config(design)
    )
    _check_cancel(cancel_cb)
    with tempfile.TemporaryDirectory(prefix="wg2-solver-mesh-") as temp_dir:
        mesh_path = Path(temp_dir) / "waveguide.msh"
        # The user budget is advice. _mesh_policy replaced it in this private
        # config with the independent artifact sanity cap. The mesher applies
        # its 1.25 estimate margin before building; the exact cap is repeated on
        # the parsed artifact below.
        try:
            result = build_from_config(config, mesh_path, allow_large_mesh=False)
        except Exception as exc:
            detail = str(exc)
            if "triangle" in detail.lower() and (
                "effective limit" in detail.lower()
                or "pre-mesh safety margin" in detail.lower()
            ):
                raise RuntimeError(
                    "Solver mesh exceeds the non-bypassable actual-domain "
                    f"artifact sanity ceiling of "
                    f"{MAX_SOLVER_MESH_ARTIFACT_TRIANGLES:,} triangles. "
                    "Coarsen the relevant mm mesh resolution before solving."
                ) from exc
            raise
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
    mode = str(config.get("mode", "")).strip().lower()
    quadrants = normalise_quadrants((config.get("mesh") or {}).get("quadrants"))
    triangle_count = int(len(triangles))
    _enforce_artifact_triangle_ceiling(triangle_count)
    metadata = _json_safe(getattr(result, "metadata", None) or {})
    dense_memory = _enforce_dense_solver_memory_ceiling(
        triangles,
        quadrants,
        mode=mode,
        tags=tags,
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

    integrity = mesh_integrity_report(
        vertices,
        triangles,
        expected_volume_sign=(
            -1
            if mode == "infinite-baffle"
            else 1
        ),
        symmetry_plane_axes=_symmetry_plane_axes(config),
    )
    semantic_orientation = mesh_semantic_orientation_report(
        vertices,
        triangles,
        tags,
        mode=mode,
    )
    integrity["semantic_orientation"] = semantic_orientation
    integrity["valid"] = bool(integrity["valid"] and semantic_orientation["valid"])
    # Deliberately NOT folded into integrity["valid"]. That flag means shape,
    # degeneracy, manifoldness and orientation, it is consumed by the results UI
    # and the render CLI, and -- decisively -- _require_closed_acoustic_topology
    # below raises on it before mesh_validation_mode is ever read, so folding a
    # crossing in here would hard-fail closed models with no escape hatch. The
    # severity decision belongs to build_solver_mesh, where the mode is known.
    integrity["self_intersection"] = mesh_self_intersection_report(
        vertices, triangles
    )
    _require_closed_acoustic_topology(config, integrity)
    tag_counts = {
        str(tag): tag_count_values.get(tag, 0) for tag in sorted(CANONICAL_SURFACE_TAGS)
    }
    bounds_min = np.min(vertices, axis=0)
    bounds_max = np.max(vertices, axis=0)
    # Quadrants are the authoritative solve-domain contract. Mesher metadata is
    # diagnostic and must not be able to understate warnings or memory.
    domain_multiplier = float(expected_domain_multiplier)
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
    # Prefer the design's own budget over the built-in threshold so the warning
    # names the number the user set. They coincide by default: mesh.max_triangles
    # defaults to the same 18,000 full-domain equivalent.
    budget_full_domain = warning_limit_full_domain
    metadata.update(
        {
            # Preserve the established metadata meaning even though the private
            # mesher config used the independent safety ceiling.
            "meshTriangleLimit": budget_full_domain,
            "meshEffectiveTriangleLimit": max(
                1, int(round(budget_full_domain / domain_multiplier))
            ),
            "meshTriangleLimitExceeded": full_domain_count > budget_full_domain,
            "meshArtifactActualDomainTriangleLimit": MAX_SOLVER_MESH_ARTIFACT_TRIANGLES,
            "meshDenseMemoryLimitBytes": DENSE_SOLVER_MEMORY_LIMIT_BYTES,
            "meshDenseMemoryEstimateBytes": dense_memory["estimated_bytes"],
            "meshDenseMetalEstimateBytes": dense_memory["metal_bytes"],
            "meshDenseBemppEstimateBytes": dense_memory["bempp_bytes"],
            "meshAllowLarge": bool(
                _strict_scalar(
                    design.root.mesh.allow_large_mesh,
                    0.0,
                    "mesh.allow_large_mesh",
                )
            ),
        }
    )
    warnings = _large_mesh_warnings(
        triangle_count=triangle_count,
        full_domain_count=full_domain_count,
        budget_full_domain=budget_full_domain,
        domain_multiplier=domain_multiplier,
        metadata=metadata,
    )
    if not integrity["valid"]:
        warnings.append(
            "Solver mesh contains invalid, degenerate, or non-manifold triangles."
        )
    warnings.extend(_self_intersection_warnings(integrity["self_intersection"]))
    fold = metadata.get("outerOffsetFold")
    if fold:
        # The mesher has always detected this and only logged it, on the
        # grounds that the acoustic surface is unaffected. That is true of the
        # analytic profile and false of the boundary the solver integrates
        # over, and a log line reaches nobody reading a polar plot.
        warnings.append(
            f"Outer wall offset folds on itself: {fold}. The requested wall "
            "thickness exceeds the throat's curvature radius there, so the "
            "rear shell doubles back through itself. Reduce Wall Thickness or "
            "open the throat curvature."
        )
    off_plane_open_edges = int(integrity.get("off_plane_open_edge_count", 0))
    # Only a bare horn owns an intentionally open mouth rim. Closed shell and
    # coupled infinite-baffle models have already failed above if any free edge
    # lies away from a legitimate symmetry cut plane.
    if off_plane_open_edges and str(config.get("mode", "")).strip().lower() in {
        "enclosure",
        "freestanding",
        "infinite-baffle",
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
        "soft_warning_full_domain_triangle_limit": budget_full_domain,
        "artifact_actual_domain_triangle_limit": MAX_SOLVER_MESH_ARTIFACT_TRIANGLES,
        "dense_solver_memory_limit_bytes": DENSE_SOLVER_MEMORY_LIMIT_BYTES,
        "dense_solver_memory_estimate_bytes": dense_memory["estimated_bytes"],
        "dense_solver_used_vertex_count": dense_memory["used_vertex_count"],
        "dense_solver_metal_dof_count": dense_memory["metal_dof_count"],
        "dense_solver_metal_estimate_bytes": dense_memory["metal_bytes"],
        "dense_solver_bempp_estimate_bytes": dense_memory["bempp_bytes"],
        "dense_solver_domain_multiplier": dense_memory["domain_multiplier"],
        "dense_solver_metal_bytes_per_dof_squared": dense_memory[
            "metal_bytes_per_dof_squared"
        ],
        "dense_solver_bempp_bytes_per_vertex_squared": dense_memory[
            "bempp_bytes_per_vertex_squared"
        ],
        "dense_solver_aperture_triangle_count": dense_memory[
            "aperture_triangle_count"
        ],
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
    _, quadrants_warning = _resolve_quadrants(validated)
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
    if quadrants_warning is not None:
        result["stats"].setdefault("warnings", []).append(quadrants_warning)
    self_intersection = result["integrity"].get("self_intersection") or {}
    crossings = int(self_intersection.get("proper_crossing_count", 0)) + int(
        self_intersection.get("coplanar_overlap_count", 0)
    )
    # Ahead of the topology check on purpose: both raise the same way, and a
    # crossing names a cause the user can act on where "failed topology
    # integrity validation" does not.
    if validation_mode == "strict" and crossings:
        raise RuntimeError(
            f"Solver mesh failed strict validation: {crossings:,} self-intersecting "
            "triangle pairs. Increase Wall Thickness or reduce Rear Resolution."
        )
    if validation_mode == "strict" and not result["integrity"]["valid"]:
        raise RuntimeError("Solver mesh failed strict topology integrity validation")
    if validation_mode == "off":
        result["stats"]["warnings"] = [
            warning
            for warning in result["stats"]["warnings"]
            if not warning.startswith("Solver mesh contains invalid")
            and not warning.startswith(SELF_INTERSECTION_WARNING_PREFIX)
        ]
    return result


__all__ = [
    "CANONICAL_SURFACE_TAGS",
    "DENSE_SOLVER_MEMORY_LIMIT_BYTES",
    "LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES",
    "MAX_SOLVER_MESH_ARTIFACT_TRIANGLES",
    "MOUTH_APERTURE_SURFACE_TAG",
    "SELF_INTERSECTION_WARNING_PREFIX",
    "SOLVER_MESH_CACHE_FORMAT_VERSION",
    "build_solver_mesh",
    "clear_solver_mesh_cache",
    "solver_mesh_cache_info",
]

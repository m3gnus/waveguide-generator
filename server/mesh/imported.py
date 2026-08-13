"""Authoritative imported-STEP mesh construction for CAD-return ingestion.

Imported physical tags are a separate namespace.  This module therefore uses
opaque mesher roles and an explicit tag map and never passes imported geometry
through the parametric surface-tag contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Any

import numpy as np

from server.mesh.builder import (
    DENSE_SOLVER_MEMORY_LIMIT_BYTES,
    MAX_SOLVER_MESH_ARTIFACT_TRIANGLES,
    _enforce_artifact_triangle_ceiling,
    _enforce_dense_solver_memory_ceiling,
)
from server.mesh.integrity import mesh_integrity_report


TAG_NAMESPACE = "wg-import-v1"
RIGID_TAG = 1
FIRST_SOURCE_TAG = 101
AREA_REL_TOLERANCE = 0.01
PLANE_DISTANCE_MM = 0.05
NORMAL_ANGLE_DEG = 0.1
# OCC's linear target size alone can leave tight fillets and curved imported
# baffles visibly faceted. This asks Gmsh for 24 elements around a full circle,
# while the floor below prevents tiny CAD details from exploding solve cost.
IMPORTED_CURVATURE_SEGMENTS = 24
IMPORTED_CURVATURE_REFINEMENT_LIMIT = 2.0
# The CAD viewport is a presentation artifact, not an acoustic discretisation.
# Its sizing is therefore scale/curvature driven and explicitly capped for the
# browser instead of inheriting source-frequency or rigid-wall solve targets.
IMPORTED_VIEWPORT_CURVATURE_SEGMENTS = 48
IMPORTED_VIEWPORT_SCALE_DIVISOR = 140.0
IMPORTED_VIEWPORT_MIN_SIZE_MM = 1.0
IMPORTED_VIEWPORT_MAX_SIZE_MM = 6.0
IMPORTED_VIEWPORT_MIN_REFINEMENT = 4.0
IMPORTED_VIEWPORT_MAX_TRIANGLES = 350_000
IMPORTED_VIEWPORT_MAX_ATTEMPTS = 3
IMPORTED_VIEWPORT_COARSENING_FACTOR = 1.5


class ImportedMeshError(ValueError):
    """A typed imported-geometry refusal."""


class RoleResolutionError(ImportedMeshError):
    """Source roles could not be resolved without guessing."""

    def __init__(
        self, message: str, *, area_drift_sources: Iterable[str] = ()
    ) -> None:
        self.area_drift_sources = tuple(str(source_id) for source_id in area_drift_sources)
        super().__init__(message)


class ImportedMeshDependencyError(RuntimeError):
    """The native STEP/mesh engine is not installed or importable."""


def validate_imported_sizes(
    sources: Iterable[Mapping[str, Any]],
    sizes: Mapping[str, Any],
    *,
    skipped_source_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Require explicit positive sizes covering every non-skipped source."""

    skipped = set(skipped_source_ids)
    expected = {str(source["id"]) for source in sources} - skipped
    rigid = sizes.get("rigid_size_mm")
    transition = sizes.get("transition_mm")
    source_sizes = sizes.get("source_size_mm")
    for name, value in (("rigid_size_mm", rigid), ("transition_mm", transition)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ImportedMeshError(f"mesh.{name} must be an explicit finite positive mm value")
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ImportedMeshError(f"mesh.{name} must be an explicit finite positive mm value")
    if not isinstance(source_sizes, Mapping):
        raise ImportedMeshError("mesh.source_size_mm must be an object keyed by source_id")
    actual = {str(key) for key in source_sizes}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {missing}")
        if extra:
            details.append(f"extra {extra}")
        raise ImportedMeshError(
            "mesh.source_size_mm must cover exactly every non-skipped source: "
            + "; ".join(details)
        )
    normalized: dict[str, float] = {}
    for source_id, value in source_sizes.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ImportedMeshError(f"mesh.source_size_mm[{source_id!r}] must be finite and positive")
        number = float(value)
        if not math.isfinite(number) or number <= 0.0:
            raise ImportedMeshError(f"mesh.source_size_mm[{source_id!r}] must be finite and positive")
        normalized[str(source_id)] = number
    return {
        "rigid_size_mm": float(rigid),
        "transition_mm": float(transition),
        "source_size_mm": normalized,
    }


def imported_tessellation_settings(sizes: Mapping[str, Any]) -> dict[str, float | int]:
    """Bounded curvature-aware OCC tessellation derived from validated sizes.

    Curvature may refine to half the finest explicit target, never arbitrarily
    towards zero on a small decorative fillet. The user's rigid target remains
    the global maximum edge-size request.
    """

    values = [float(sizes["rigid_size_mm"]), *map(float, sizes["source_size_mm"].values())]
    finest = min(values)
    return {
        "curvature_segments_per_2pi": IMPORTED_CURVATURE_SEGMENTS,
        "mesh_size_min_mm": finest / IMPORTED_CURVATURE_REFINEMENT_LIMIT,
        "mesh_size_max_mm": float(sizes["rigid_size_mm"]),
        "algorithm": 6,
    }


def imported_viewport_tessellation_settings(
    bounds_mm: Iterable[float], *, retry: int = 0
) -> dict[str, float | int]:
    """Return bounded visual-only sizing for a full-domain OCC model."""

    bounds = np.asarray(list(bounds_mm), dtype=float)
    if bounds.shape != (6,) or not np.isfinite(bounds).all():
        raise ImportedMeshError("viewport meshing: model bounds are invalid")
    diagonal = float(np.linalg.norm(bounds[3:] - bounds[:3]))
    if diagonal <= 0.0:
        raise ImportedMeshError("viewport meshing: model bounds are empty")
    if retry < 0 or retry >= IMPORTED_VIEWPORT_MAX_ATTEMPTS:
        raise ImportedMeshError("viewport meshing: invalid coarsening retry")
    base_max = min(
        IMPORTED_VIEWPORT_MAX_SIZE_MM,
        max(IMPORTED_VIEWPORT_MIN_SIZE_MM, diagonal / IMPORTED_VIEWPORT_SCALE_DIVISOR),
    )
    coarsening = IMPORTED_VIEWPORT_COARSENING_FACTOR**retry
    mesh_max = base_max * coarsening
    return {
        "model_diagonal_mm": diagonal,
        "mesh_size_min_mm": mesh_max / IMPORTED_VIEWPORT_MIN_REFINEMENT,
        "mesh_size_max_mm": mesh_max,
        "curvature_segments_per_2pi": max(
            16,
            int(round(IMPORTED_VIEWPORT_CURVATURE_SEGMENTS / coarsening)),
        ),
        "algorithm": 6,
        "retry": retry,
    }


def allocate_imported_tags(
    sources: Iterable[Mapping[str, Any]], *, skipped_source_ids: Iterable[str] = ()
) -> dict[str, Any]:
    """Allocate deterministic collision-free tags and their complete meaning."""

    skipped = set(skipped_source_ids)
    tag_map: dict[str, dict[str, Any]] = {
        str(RIGID_TAG): {"source_id": None, "instance_id": None, "role": "rigid"}
    }
    source_tags: dict[str, int] = {}
    tag = FIRST_SOURCE_TAG
    for source in sources:
        source_id = str(source["id"])
        if source_id in skipped:
            continue
        source_tags[source_id] = tag
        tag_map[str(tag)] = {
            "source_id": source_id,
            "instance_id": source.get("instance_id"),
            "role": str(source["role"]),
        }
        tag += 1
    return {
        "tag_namespace": TAG_NAMESPACE,
        "rigid_tag": RIGID_TAG,
        "source_tags": source_tags,
        "tag_map": tag_map,
    }


def rigid_inverse(matrix: Any, *, tolerance: float = 1.0e-6) -> np.ndarray:
    """Validate a right-handed rigid placement and return its inverse."""

    value = np.asarray(matrix, dtype=float)
    if value.shape != (4, 4) or not np.isfinite(value).all():
        raise ImportedMeshError("normalisation: assembly_from_link must be a finite 4x4 matrix")
    if not np.allclose(value[3], [0.0, 0.0, 0.0, 1.0], atol=tolerance, rtol=0.0):
        raise ImportedMeshError("normalisation: assembly_from_link last row must be [0,0,0,1]")
    rotation = value[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=tolerance, rtol=0.0):
        raise ImportedMeshError("normalisation: assembly_from_link rotation is not orthonormal within 1e-6")
    determinant = float(np.linalg.det(rotation))
    if determinant < 0.0:
        raise ImportedMeshError(
            f"normalisation: assembly_from_link is mirrored (det={determinant:.9g}); chirality is unsupported"
        )
    if abs(determinant - 1.0) > tolerance:
        raise ImportedMeshError(
            f"normalisation: assembly_from_link determinant {determinant:.9g} is not +1 within 1e-6"
        )
    inverse = np.eye(4)
    inverse[:3, :3] = rotation.T
    inverse[:3, 3] = -(rotation.T @ value[:3, 3])
    return inverse


def _transform_point(matrix: np.ndarray, point: Iterable[float]) -> np.ndarray:
    homogeneous = np.append(np.asarray(tuple(point), dtype=float), 1.0)
    return (matrix @ homogeneous)[:3]


def _transform_direction(matrix: np.ndarray, direction: Iterable[float]) -> np.ndarray:
    return matrix[:3, :3] @ np.asarray(tuple(direction), dtype=float)


def geometry_candidate_matches(candidate: Mapping[str, Any], contract: Mapping[str, Any]) -> bool:
    """Apply the linked-throat geometry gates to one measured face."""

    diameter = float(contract["throat_diameter_mm"])
    axis_limit = max(0.10, 0.005 * diameter)
    return (
        bool(candidate.get("planar"))
        and float(candidate.get("plane_distance_mm", math.inf)) <= PLANE_DISTANCE_MM
        and float(candidate.get("normal_angle_deg", math.inf)) <= NORMAL_ANGLE_DEG
        and float(candidate.get("centroid_axis_distance_mm", math.inf)) <= axis_limit
        and abs(float(candidate.get("area_mm2", 0.0)) - float(contract["expected_disc_area_mm2"]))
        / float(contract["expected_disc_area_mm2"])
        <= AREA_REL_TOLERANCE
    )


def resolve_instance_source(
    source: Mapping[str, Any],
    candidates: Iterable[Mapping[str, Any]],
    painted_faces: Iterable[int],
    *,
    local_body_state: str,
) -> dict[str, Any]:
    """Implement the five-row linked-throat geometry/paint decision table."""

    matches = [dict(item) for item in candidates if bool(item.get("matches", True))]
    painted = {int(item) for item in painted_faces}
    source_id = str(source["id"])
    if not matches:
        raise RoleResolutionError(
            f"role resolution: source {source_id!r} has no geometric throat candidate; "
            f"local_body_state={local_body_state!r}"
        )
    if len(matches) >= 2:
        faces = [int(item["face_id"]) for item in matches]
        raise RoleResolutionError(
            f"role resolution: source {source_id!r} has {len(matches)} geometric throat candidates {faces}"
        )
    face = int(matches[0]["face_id"])
    extras = sorted(painted - {face})
    if extras:
        method = "geometry-overrode-paint"
    elif face in painted:
        method = "geometry+paint-corroborated"
    else:
        method = "geometry"
    return {
        "source_id": source_id,
        "surfaces": [face],
        "method": method,
        "painted": face in painted,
        "demoted_painted_surfaces": extras,
        "candidate": matches[0],
    }


def resolve_user_source(
    source: Mapping[str, Any],
    mechanisms: Mapping[str, Iterable[int]],
    *,
    face_areas_mm2: Mapping[int, float],
    connected_components: int,
    vocabulary: Mapping[str, Iterable[str]] | None = None,
    allow_area_drift: bool = False,
) -> dict[str, Any]:
    """Resolve all declared user selectors with agreement and drift gates."""

    source_id = str(source["id"])
    resolved = {name: frozenset(int(face) for face in faces) for name, faces in mechanisms.items()}
    nonempty = {name: faces for name, faces in resolved.items() if faces}
    if not nonempty:
        if bool(source.get("required")):
            vocab = vocabulary or {}
            shells = ", ".join(sorted(vocab.get("shell_names", ()))) or "(none)"
            styles = ", ".join(sorted(vocab.get("appearance_labels", ()))) or "(none)"
            raise RoleResolutionError(
                f"role resolution: required source {source_id!r} is missing. "
                f"Available shell names: {shells}. Available style names: {styles}"
            )
        return {"source_id": source_id, "skipped": True, "reason": "optional source selectors matched no faces"}
    sets = list(nonempty.values())
    if any(faces != sets[0] for faces in sets[1:]):
        detail = {name: sorted(faces) for name, faces in resolved.items()}
        raise RoleResolutionError(
            f"role resolution: source {source_id!r} selector mechanisms disagree: {detail}"
        )
    faces = sorted(sets[0])
    expected_components = int(source["expected_connected_components"])
    if connected_components != expected_components:
        raise RoleResolutionError(
            f"role resolution: source {source_id!r} has {connected_components} connected components; "
            f"expected {expected_components} for {source['patch_policy']}"
        )
    observed = source["observed"]
    if len(faces) != int(observed["face_count"]):
        raise RoleResolutionError(
            f"role resolution: source {source_id!r} face count changed from "
            f"{observed['face_count']} to {len(faces)}"
        )
    area = sum(float(face_areas_mm2[face]) for face in faces)
    expected_area = float(observed["total_area_mm2"])
    drift = abs(area - expected_area) / expected_area
    if drift > AREA_REL_TOLERANCE and not allow_area_drift:
        raise RoleResolutionError(
            f"role resolution: source {source_id!r} area drift is {drift:.3%} "
            f"({expected_area:.9g} -> {area:.9g} mm^2), exceeding 1%",
            area_drift_sources=[source_id],
        )
    return {
        "source_id": source_id,
        "surfaces": faces,
        "method": "declared-selectors",
        "origins": sorted(nonempty),
        "connected_components": connected_components,
        "area_mm2": area,
        "area_relative_drift": drift,
        "area_drift_overridden": drift > AREA_REL_TOLERANCE,
        "skipped": False,
    }


def polar_grid_from_symmetry(symmetry_report: Mapping[str, Any]) -> dict[str, Any]:
    """Pin full sweeps on rejected planes and one-sided sweeps only on cuts."""

    planes = symmetry_report.get("planes") or {}
    axes: dict[str, dict[str, Any]] = {}
    x_accepted = bool((planes.get("x0") or {}).get("accepted"))
    y_accepted = bool((planes.get("y0") or {}).get("accepted"))
    # Polar one-sidedness follows the transverse mirror planes, not a fictitious
    # front/back polar axis: horizontal <- x0, vertical <- y0, and a diagonal
    # sweep is one-sided only when both transverse mirrors were accepted. z0 has
    # no implication for any observation plane swept by the solver.
    for axis, plane, accepted in (
        ("horizontal", "x0", x_accepted),
        ("vertical", "y0", y_accepted),
        ("diagonal", "x0+y0", x_accepted and y_accepted),
    ):
        axes[axis] = {
            "plane": plane,
            "symmetry_accepted": accepted,
            "minimum_deg": 0.0 if accepted else -180.0,
            "maximum_deg": 180.0,
            "may_widen_not_narrow": True,
        }
    return {"axes": axes, "cut_planes": list(symmetry_report.get("cut_planes") or [])}


def _face_components(gmsh: Any, faces: Iterable[int]) -> int:
    faces = {int(face) for face in faces}
    if not faces:
        return 0
    edges = {
        face: {abs(tag) for dim, tag in gmsh.model.getBoundary([(2, face)], oriented=False) if dim == 1}
        for face in faces
    }
    count = 0
    remaining = set(faces)
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            current = stack.pop()
            neighbors = {other for other in remaining if edges[current] & edges[other]}
            remaining -= neighbors
            stack.extend(neighbors)
    return count


def _lookup_faces(
    labels: Iterable[str],
    groups: Mapping[str, list[int]],
    *,
    requested_labels: Iterable[str] | None = None,
) -> set[int]:
    """Resolve case-insensitive labels with the legacy guarded port alias."""

    result: set[int] = set()
    folded = {key.casefold(): value for key, value in groups.items()}
    label_values = tuple(labels)
    selected = {str(label).casefold() for label in label_values}
    requested = {
        str(label).casefold()
        for label in (label_values if requested_labels is None else requested_labels)
    }
    for label in selected:
        matches = folded.get(label)
        if matches is None and label in {"port_exit_l", "port_exit_r"}:
            # The generic label is a fallback only.  If it was explicitly
            # requested it owns its own match and must not satisfy an alias.
            if "port_exit" not in requested:
                matches = folded.get("port_exit")
        result.update(matches or ())
    return result


def _advanced_face_identifier_surfaces(
    identifiers: Iterable[int],
    face_order: Iterable[int],
    ordered_surfaces: Iterable[int],
) -> set[int]:
    """Map checksum-bound STEP ADVANCED_FACE identifiers to OCC surfaces."""

    mapping = {
        int(identifier): int(surface)
        for identifier, surface in zip(face_order, ordered_surfaces, strict=True)
    }
    selected: set[int] = set()
    for raw_identifier in identifiers:
        identifier = int(raw_identifier)
        try:
            selected.add(mapping[identifier])
        except KeyError as exc:
            raise RoleResolutionError(
                "role resolution: advanced_face_indices contains unknown "
                f"STEP ADVANCED_FACE identifier {identifier}; available identifiers "
                f"are {sorted(mapping)}"
            ) from exc
    return selected


def _assert_disjoint_source_claims(
    resolutions: Mapping[str, Mapping[str, Any]],
) -> None:
    owner_by_face: dict[int, str] = {}
    for source_id, resolution in resolutions.items():
        if resolution.get("skipped"):
            continue
        for raw_face in resolution.get("surfaces", ()):
            face = int(raw_face)
            previous = owner_by_face.get(face)
            if previous is not None and previous != source_id:
                raise RoleResolutionError(
                    f"role resolution: sources {previous!r} and {source_id!r} "
                    f"both resolve STEP/OCC face {face}"
                )
            owner_by_face[face] = source_id


def _reconcile_source_paint(
    sources: Iterable[Mapping[str, Any]],
    resolutions: Mapping[str, dict[str, Any]],
    painted_by_source: Mapping[str, set[int]],
    *,
    skipped_source_ids: Iterable[str] = (),
    skipped_paint: Iterable[int] = (),
) -> tuple[set[int], list[dict[str, Any]], list[int]]:
    """Reconcile shared labels only after every live source owns its faces."""

    source_list = list(sources)
    skipped = set(skipped_source_ids)
    _assert_disjoint_source_claims(resolutions)
    claims_by_source = {
        source_id: {int(face) for face in outcome.get("surfaces", ())}
        for source_id, outcome in resolutions.items()
        if not outcome.get("skipped")
    }
    claimed = set().union(*claims_by_source.values()) if claims_by_source else set()
    label_owners: dict[str, set[str]] = {}
    for source in source_list:
        source_id = str(source["id"])
        if source_id in skipped:
            continue
        for label in source["selectors"].get("appearance_labels", ()):
            label_owners.setdefault(str(label).casefold(), set()).add(source_id)

    explained_paint = {int(face) for face in skipped_paint} | claimed
    findings: list[dict[str, Any]] = []
    for source in source_list:
        source_id = str(source["id"])
        outcome = resolutions[source_id]
        linked = source["selectors"].get("linked_throat")
        if linked is None or outcome.get("skipped"):
            continue
        own = claims_by_source[source_id]
        other_claimed = claimed - own
        painted = painted_by_source.get(source_id, set())
        extras = sorted(painted - own - other_claimed)
        outcome["painted"] = bool(own & painted)
        outcome["demoted_painted_surfaces"] = extras
        if extras:
            outcome["method"] = "geometry-overrode-paint"
            findings.append(
                {
                    "kind": "geometry-overrode-paint",
                    "source_id": source_id,
                    "surfaces": extras,
                }
            )
            labels = {
                str(label).casefold()
                for label in source["selectors"].get("appearance_labels", ())
            }
            if labels and all(len(label_owners.get(label, ())) == 1 for label in labels):
                explained_paint.update(extras)
        elif own & painted:
            outcome["method"] = "geometry+paint-corroborated"
        else:
            outcome["method"] = "geometry"
            findings.append({"kind": "source-paint-missing", "source_id": source_id})

    paint_sets = [
        {int(face) for face in faces} for faces in painted_by_source.values()
    ]
    relevant_painted = set().union(
        *paint_sets, {int(face) for face in skipped_paint}
    )
    return claimed, findings, sorted(relevant_painted - explained_paint)


def _post_cut_source_area_record(
    source_id: str,
    *,
    parent_area_mm2: float,
    retained_child_area_mm2: float,
    predicted_retained_fraction: float,
) -> dict[str, float]:
    retained_fraction = (
        retained_child_area_mm2 / parent_area_mm2 if parent_area_mm2 else 0.0
    )
    relative_share_error = (
        abs(retained_fraction - predicted_retained_fraction)
        / predicted_retained_fraction
        if predicted_retained_fraction
        else math.inf
    )
    record = {
        "parent_area_mm2": parent_area_mm2,
        "retained_child_area_mm2": retained_child_area_mm2,
        "retained_fraction": retained_fraction,
        "predicted_retained_fraction": predicted_retained_fraction,
        "relative_share_error": relative_share_error,
    }
    if relative_share_error > AREA_REL_TOLERANCE:
        raise ImportedMeshError(
            f"symmetry: source {source_id!r} retained fraction "
            f"{retained_fraction:.9g} differs from predicted post-cut share "
            f"{predicted_retained_fraction:.9g} by {relative_share_error:.3%}"
        )
    return record


def _surface_normal(gmsh: Any, surface: int) -> np.ndarray:
    lower, upper = gmsh.model.getParametrizationBounds(2, surface)
    params = [(float(lower[0]) + float(upper[0])) / 2.0, (float(lower[1]) + float(upper[1])) / 2.0]
    normal = np.asarray(gmsh.model.getNormal(surface, params), dtype=float).reshape(-1, 3)[0]
    length = float(np.linalg.norm(normal))
    return normal / length if length > 0.0 else normal


def _physical_name(tag: int, source_id: str, instance_id: Any, role: str) -> str:
    instance = "null" if instance_id is None else str(instance_id)
    return (
        f"wg-import-v1|tag={tag}|source_id={source_id}|"
        f"instance_id={instance}|role={role}"
    )


def _mesh_arrays(mesh: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    triangles = np.asarray(mesh.cells_dict.get("triangle", []), dtype=np.int64)
    try:
        tags = np.asarray(mesh.cell_data_dict["gmsh:physical"]["triangle"], dtype=np.int32)
    except KeyError as exc:
        raise ImportedMeshError("meshing: imported mesh has no physical tags") from exc
    return np.asarray(mesh.points, dtype=float), triangles, tags


def _geometry_fingerprint(geometries: Iterable[Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(
            [
                {"center_mm": list(center), "area_mm2": area}
                for center, area in geometries
            ],
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _surface_model_bounds_mm(gmsh: Any, surfaces: Iterable[int]) -> tuple[float, ...]:
    boxes = [gmsh.model.getBoundingBox(2, int(surface)) for surface in surfaces]
    if not boxes:
        raise ImportedMeshError("viewport meshing: imported model has no surfaces")
    lower = np.min(np.asarray([box[:3] for box in boxes], dtype=float), axis=0)
    upper = np.max(np.asarray([box[3:] for box in boxes], dtype=float), axis=0)
    return tuple(float(value) for value in np.concatenate((lower, upper)))


def build_imported_viewport_mesh(
    assembly_path: str | Path,
    manifest: Mapping[str, Any],
    recipe: Mapping[str, Any],
    *,
    expected_geometry_hash: str,
    tag_allocation: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one advisory full-domain display mesh in a fresh OCC model.

    This pass deliberately runs after the acoustic artifact is complete. It
    reimports the same STEP with the winning healing options and proves the
    normalized geometry fingerprint before tessellating. Any exception is
    caught by the ingestion caller and can never trigger solver healing.
    """

    try:
        import gmsh
        import meshio
        from hornlab_mesher import OccSurfaceRole
        from hornlab_mesher.step_import import (
            StepFaceGroup,
            StepLabelSelector,
            anchor_surface_order,
            gmsh_surface_geometries,
            gmsh_surface_tags,
            postprocess_mesh,
        )
    except ImportError as exc:
        raise ImportedMeshDependencyError(
            "CAD viewport meshing requires hornlab-waveguide-mesher, gmsh, and meshio"
        ) from exc

    matrix = np.asarray(recipe.get("normalisation_matrix"), dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ImportedMeshError("viewport meshing: normalization recipe is invalid")
    reference = [
        (tuple(float(value) for value in item[0]), float(item[1]))
        for item in recipe.get("surface_order_reference", ())
    ]
    raw_groups = recipe.get("groups_by_tag")
    if not isinstance(raw_groups, Mapping):
        raise ImportedMeshError("viewport meshing: physical-group recipe is missing")

    gmsh.clear()
    try:
        gmsh.model.add("wgreturn-viewport")
        selected_healing = {str(value) for value in recipe.get("healing_options", ())}
        for option_name in {
            "Geometry.OCCFixDegenerated",
            "Geometry.OCCFixSmallEdges",
            "Geometry.OCCFixSmallFaces",
            "Geometry.OCCSewFaces",
        }:
            gmsh.option.setNumber(option_name, 1 if option_name in selected_healing else 0)
        imported = gmsh.model.occ.importShapes(str(assembly_path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        if not imported:
            raise ImportedMeshError("viewport meshing: assembly STEP contains no OCC geometry")
        if not np.allclose(matrix, np.eye(4)):
            gmsh.model.occ.affineTransform(imported, matrix.reshape(-1).tolist())
            gmsh.model.occ.synchronize()

        surfaces = gmsh_surface_tags()
        geometries = gmsh_surface_geometries(surfaces)
        observed_hash = _geometry_fingerprint(geometries)
        if observed_hash != expected_geometry_hash:
            raise ImportedMeshError(
                "viewport meshing: normalized geometry fingerprint differs from solve truth"
            )
        ordered_surfaces = (
            anchor_surface_order(surfaces, geometries, reference)
            if reference
            else surfaces
        )

        source_by_id = {str(source["id"]): source for source in manifest["sources"]}
        source_specs = []
        for raw_tag, raw_indices in raw_groups.items():
            tag = int(raw_tag)
            indices = [int(value) for value in raw_indices]
            if any(index < 0 or index >= len(ordered_surfaces) for index in indices):
                raise ImportedMeshError("viewport meshing: physical-group surface index is invalid")
            selected = [int(ordered_surfaces[index]) for index in indices]
            if not selected:
                continue
            gmsh.model.addPhysicalGroup(2, selected, tag)
            if tag == RIGID_TAG:
                gmsh.model.setPhysicalName(2, tag, "wg-import-v1|rigid")
                continue
            tag_entry = tag_allocation["tag_map"].get(str(tag))
            if not isinstance(tag_entry, Mapping) or tag_entry.get("source_id") is None:
                raise ImportedMeshError(f"viewport meshing: unknown physical tag {tag}")
            source_id = str(tag_entry["source_id"])
            source = source_by_id[source_id]
            role = str(source["role"])
            gmsh.model.setPhysicalName(
                2,
                tag,
                _physical_name(tag, source_id, source.get("instance_id"), role),
            )
            source_specs.append(
                StepFaceGroup(
                    source_id,
                    StepLabelSelector(source_id),
                    OccSurfaceRole(role),
                    tag=tag,
                    resolution_mm=0.0,
                )
            )

        bounds_mm = _surface_model_bounds_mm(gmsh, surfaces)
        last_error: Exception | None = None
        for retry in range(IMPORTED_VIEWPORT_MAX_ATTEMPTS):
            settings = imported_viewport_tessellation_settings(bounds_mm, retry=retry)
            try:
                gmsh.model.mesh.clear()
                points = [tag for dim, tag in gmsh.model.getEntities(0) if dim == 0]
                if points:
                    gmsh.model.mesh.setSize(
                        [(0, point) for point in points],
                        float(settings["mesh_size_max_mm"]),
                    )
                gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
                gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 1)
                gmsh.option.setNumber("Mesh.MeshSizeMin", settings["mesh_size_min_mm"])
                gmsh.option.setNumber("Mesh.MeshSizeMax", settings["mesh_size_max_mm"])
                gmsh.option.setNumber(
                    "Mesh.MeshSizeFromCurvature",
                    settings["curvature_segments_per_2pi"],
                )
                gmsh.option.setNumber("Mesh.Algorithm", settings["algorithm"])
                gmsh.option.setNumber("Mesh.SaveAll", 0)
                gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
                gmsh.option.setNumber("Mesh.Binary", 0)
                gmsh.model.mesh.generate(2)
                with tempfile.TemporaryDirectory(prefix="wg2-imported-viewport-") as temporary:
                    raw_path = Path(temporary) / "raw.msh"
                    gmsh.write(str(raw_path))
                    raw_mesh = meshio.read(raw_path)
                    processed, repair, topology = postprocess_mesh(
                        raw_mesh,
                        source_specs,
                        symmetry_planes=(),
                        tolerance=5.0e-3,
                    )
                    points_mm, triangles, tags = _mesh_arrays(processed)
                    if len(triangles) > IMPORTED_VIEWPORT_MAX_TRIANGLES:
                        raise ImportedMeshError(
                            "viewport meshing: generated "
                            f"{len(triangles):,} triangles, exceeding the "
                            f"{IMPORTED_VIEWPORT_MAX_TRIANGLES:,} browser ceiling"
                        )
                    if len(triangles) == 0:
                        raise ImportedMeshError("viewport meshing: generated no triangles")
                    processed.points = points_mm * 1.0e-3
                    final_path = Path(temporary) / "viewport.msh"
                    meshio.write(final_path, processed, file_format="gmsh22", binary=False)
                    msh_text = final_path.read_text(encoding="utf-8", errors="replace")
                bounds_min = np.min(points_mm, axis=0) * 1.0e-3
                bounds_max = np.max(points_mm, axis=0) * 1.0e-3
                unique_tags, counts = np.unique(tags, return_counts=True)
                return {
                    "msh_text": msh_text,
                    "stats": {
                        "vertex_count": int(len(points_mm)),
                        "triangle_count": int(len(triangles)),
                        "tag_counts": {
                            str(int(tag)): int(count)
                            for tag, count in zip(unique_tags, counts, strict=True)
                        },
                        "units": "m",
                        "domain": "full",
                        "bounds_m": {
                            "min_x": float(bounds_min[0]),
                            "min_y": float(bounds_min[1]),
                            "min_z": float(bounds_min[2]),
                            "max_x": float(bounds_max[0]),
                            "max_y": float(bounds_max[1]),
                            "max_z": float(bounds_max[2]),
                        },
                    },
                    "metadata": {
                        "purpose": "cad-viewport",
                        "source_geometry_hash": observed_hash,
                        "tessellation": settings,
                        "triangle_ceiling": IMPORTED_VIEWPORT_MAX_TRIANGLES,
                        "attempt_count": retry + 1,
                        "postprocess": repair,
                        "topology": topology,
                    },
                }
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise ImportedMeshError(
            f"viewport meshing: all bounded tessellation attempts failed: {last_error}"
        ) from last_error
    finally:
        gmsh.clear()


def build_imported_mesh(
    assembly_path: str | Path,
    manifest: Mapping[str, Any],
    sizes: Mapping[str, Any],
    *,
    skipped_source_ids: Iterable[str] = (),
    options: Mapping[str, Any] | None = None,
    include_viewport_mesh: bool = True,
) -> dict[str, Any]:
    """Build, tag, post-process and inspect one validated CAD-return STEP."""

    try:
        import gmsh
        import meshio
        from hornlab_mesher import (
            OccSurfaceGroup,
            OccSurfaceRole,
            OccSurfaceSelector,
            Region,
            auto_cut_occ_geometry,
            estimate_mesh_cost,
        )
        from hornlab_mesher.step_import import (
            StepFaceGroup,
            StepLabelSelector,
            advanced_face_order,
            anchor_surface_order,
            gmsh_surface_geometries,
            gmsh_surface_tags,
            mesh_frequency_validation,
            parse_named_shell_faces,
            parse_styled_face_groups,
            postprocess_mesh,
            run_occ_healing_fallbacks,
        )
    except ImportError as exc:
        raise ImportedMeshDependencyError(
            "CAD-return ingestion requires hornlab-waveguide-mesher, gmsh, and meshio"
        ) from exc

    source_list = list(manifest["sources"])
    skipped = set(skipped_source_ids)
    declared = {str(source["id"]): source for source in source_list}
    unknown_skips = skipped - set(declared)
    required_skips = {source_id for source_id in skipped if bool(declared[source_id]["required"])}
    if unknown_skips:
        raise ImportedMeshError(f"role resolution: unknown skipped source ids {sorted(unknown_skips)}")
    if required_skips:
        raise ImportedMeshError(f"role resolution: required sources cannot be skipped: {sorted(required_skips)}")
    normalized_sizes = validate_imported_sizes(source_list, sizes, skipped_source_ids=skipped)
    tessellation = imported_tessellation_settings(normalized_sizes)
    allocation = allocate_imported_tags(source_list, skipped_source_ids=skipped)
    options = dict(options or {})
    raw_area_drift_overrides = options.get("area_drift_overrides", ())
    if not isinstance(raw_area_drift_overrides, (list, tuple, set, frozenset)):
        raise ImportedMeshError("role resolution: areaDriftOverrides must be an array of source ids")
    area_drift_overrides = {str(source_id) for source_id in raw_area_drift_overrides}
    unknown_area_overrides = area_drift_overrides - set(declared)
    if unknown_area_overrides:
        raise ImportedMeshError(
            "role resolution: areaDriftOverrides names unknown sources "
            f"{sorted(unknown_area_overrides)}"
        )

    instances = list(manifest["instances"])
    instance_by_id = {str(instance["instance_id"]): instance for instance in instances}
    anchor_id = manifest["coordinate_system"].get("solver_anchor_instance_id")
    if anchor_id is None and len(instances) == 1:
        anchor_id = instances[0]["instance_id"]
    normalization = np.eye(4) if anchor_id is None else rigid_inverse(instance_by_id[str(anchor_id)]["assembly_from_link"])
    normalisation_record = {
        "anchor_instance_id": anchor_id,
        "assembly_frame_is_solver_frame": anchor_id is None,
        "matrix": normalization.tolist(),
        "rigid_tolerance": 1.0e-6,
        "post_transform_gate_mm": PLANE_DISTANCE_MM,
    }

    # The unhealed import supplies the topology reference used by the fallback
    # ladder.  Only mesh-generation failure is allowed to enter that ladder.
    def attempt(
        *,
        occ_healing_options: Iterable[str] = (),
        surface_order_reference: list[Any] | None = None,
    ) -> dict[str, Any]:
        gmsh.clear()
        gmsh.model.add("wgreturn-import")
        healing_options = tuple(occ_healing_options)
        all_healing = {
            "Geometry.OCCFixDegenerated",
            "Geometry.OCCFixSmallEdges",
            "Geometry.OCCFixSmallFaces",
            "Geometry.OCCSewFaces",
        }
        for option_name in all_healing:
            gmsh.option.setNumber(option_name, 1 if option_name in healing_options else 0)
        imported = gmsh.model.occ.importShapes(str(assembly_path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        if not imported:
            raise ImportedMeshError("STEP import + normalisation: assembly STEP contains no OCC geometry")
        transform_values = normalization.reshape(-1).tolist()
        if not np.allclose(normalization, np.eye(4)):
            gmsh.model.occ.affineTransform(imported, transform_values)
            gmsh.model.occ.synchronize()
        surfaces = gmsh_surface_tags()
        face_order = advanced_face_order(Path(assembly_path))
        if len(surfaces) != len(face_order):
            raise ImportedMeshError(
                "STEP import + normalisation: STEP/Gmsh surface count mismatch "
                f"({len(face_order)} ADVANCED_FACE records, {len(surfaces)} OCC surfaces)"
            )
        geometries = gmsh_surface_geometries(surfaces)
        transformed_geometry_hash = _geometry_fingerprint(geometries)
        ordered_surfaces = surfaces
        reanchor_residuals: list[dict[str, Any]] = []
        if surface_order_reference is not None:
            ordered_surfaces = anchor_surface_order(surfaces, geometries, surface_order_reference)
            geometry_by_tag = dict(zip(surfaces, geometries, strict=True))
            for index, (reference_center, reference_area) in enumerate(surface_order_reference):
                tag = ordered_surfaces[index]
                healed_center, healed_area = geometry_by_tag[tag]
                area_rel = abs(float(healed_area) - float(reference_area)) / max(float(reference_area), 1.0e-12)
                centroid_mm = float(np.linalg.norm(np.asarray(healed_center) - np.asarray(reference_center)))
                reanchor_residuals.append(
                    {
                        "reference_index": index,
                        "surface": int(tag),
                        "area_relative_error": area_rel,
                        "centroid_distance_mm": centroid_mm,
                    }
                )
        face_to_surface = dict(zip(face_order, ordered_surfaces, strict=True))
        named_step = parse_named_shell_faces(Path(assembly_path))
        styled_step = parse_styled_face_groups(Path(assembly_path))
        named = {name: [face_to_surface[face] for face in faces if face in face_to_surface] for name, faces in named_step.items()}
        styled = {name: [face_to_surface[face] for face in faces if face in face_to_surface] for name, faces in styled_step.items()}

        volume_count = len(gmsh.model.getEntities(3))
        body_count = volume_count or len(named) or (1 if surfaces else 0)
        expected_bodies = int(manifest["assembly"]["n_bodies_expected"])
        if body_count != expected_bodies:
            raise ImportedMeshError(
                f"scope gate: STEP contains {body_count} exterior bodies; manifest declares {expected_bodies}"
            )

        transformed_contracts: dict[str, dict[str, Any]] = {}
        for instance in instances:
            contract = instance.get("source_contract")
            if contract is None:
                continue
            placement = np.asarray(instance["assembly_from_link"], dtype=float)
            total = normalization @ placement
            plane = contract["throat_plane_link"]
            axis = contract["axis_link"]
            transformed = {
                **contract,
                "plane_origin_mm": _transform_point(total, plane["origin_mm"]),
                "plane_normal": _transform_direction(total, plane["normal"]),
                "axis_origin_mm": _transform_point(total, axis["origin_mm"]),
                "axis_direction": _transform_direction(total, axis["direction"]),
            }
            transformed_contracts[str(instance["instance_id"])] = transformed
        if anchor_id is not None:
            anchor_contract = transformed_contracts.get(str(anchor_id))
            if anchor_contract is None:
                raise ImportedMeshError("STEP import + normalisation: anchor source_contract is missing")
            axis = np.asarray(anchor_contract["axis_direction"], dtype=float)
            axis /= np.linalg.norm(axis)
            reference = np.asarray([1.0, 0.0, 0.0], dtype=float)
            if abs(float(np.dot(reference, axis))) > 0.9:
                reference = np.asarray([0.0, 1.0, 0.0], dtype=float)
            horizontal = reference - float(np.dot(reference, axis)) * axis
            horizontal /= np.linalg.norm(horizontal)
            vertical = np.cross(axis, horizontal)
            throat_origin_m = (
                np.asarray(anchor_contract["plane_origin_mm"], dtype=float) * 1.0e-3
            )
            normalisation_record["anchor_manifest_throat_plane"] = {
                "origin_mm": [float(value) for value in anchor_contract["plane_origin_mm"]],
                "normal": [float(value) for value in anchor_contract["plane_normal"]],
            }
            normalisation_record["anchor_throat_frame"] = {
                "axis": [float(value) for value in axis],
                "origin_m": [float(value) for value in throat_origin_m],
                "u": [float(value) for value in horizontal],
                "v": [float(value) for value in vertical],
                "mouth_center_m": [float(value) for value in throat_origin_m],
                "source_center_m": [float(value) for value in throat_origin_m],
            }

        resolutions: dict[str, dict[str, Any]] = {}
        painted_by_source: dict[str, set[int]] = {}
        skipped_paint: set[int] = set()
        findings: list[dict[str, Any]] = []
        areas = {surface: float(gmsh.model.occ.getMass(2, surface)) for surface in surfaces}
        requested_appearance_labels = [
            label
            for source in source_list
            for label in source["selectors"].get("appearance_labels", ())
        ]
        requested_shell_names = [
            label
            for source in source_list
            for label in source["selectors"].get("shell_names", ())
        ]
        for source in source_list:
            source_id = str(source["id"])
            selectors = source["selectors"]
            if source_id in skipped:
                resolutions[source_id] = {"source_id": source_id, "skipped": True, "reason": "explicitly skipped by request"}
                skipped_paint.update(
                    _lookup_faces(
                        selectors.get("appearance_labels", ()),
                        styled,
                        requested_labels=requested_appearance_labels,
                    )
                )
                continue
            linked = selectors.get("linked_throat")
            if linked is not None:
                instance_id = str(linked["instance_id"])
                contract = transformed_contracts.get(instance_id)
                if contract is None:
                    state = instance_by_id[instance_id]["body_evidence"]["local_body_state"]
                    raise RoleResolutionError(
                        f"role resolution: source {source_id!r} has no source_contract; local_body_state={state!r}"
                    )
                plane_origin = np.asarray(contract["plane_origin_mm"], dtype=float)
                plane_normal = np.asarray(contract["plane_normal"], dtype=float)
                plane_normal /= np.linalg.norm(plane_normal)
                axis_origin = np.asarray(contract["axis_origin_mm"], dtype=float)
                axis_direction = np.asarray(contract["axis_direction"], dtype=float)
                axis_direction /= np.linalg.norm(axis_direction)
                measured: list[dict[str, Any]] = []
                for surface in surfaces:
                    center = np.asarray(gmsh.model.occ.getCenterOfMass(2, surface), dtype=float)
                    planar = str(gmsh.model.getType(2, surface)).casefold() == "plane"
                    try:
                        face_normal = _surface_normal(gmsh, surface)
                        angle = math.degrees(math.acos(min(1.0, max(-1.0, abs(float(np.dot(face_normal, plane_normal)))))))
                    except Exception:
                        angle = math.inf
                    delta = center - axis_origin
                    axis_distance = float(np.linalg.norm(delta - np.dot(delta, axis_direction) * axis_direction))
                    candidate = {
                        "face_id": surface,
                        "planar": planar,
                        "plane_distance_mm": abs(float(np.dot(center - plane_origin, plane_normal))),
                        "normal_angle_deg": angle,
                        "centroid_axis_distance_mm": axis_distance,
                        "area_mm2": areas[surface],
                    }
                    candidate["matches"] = geometry_candidate_matches(candidate, contract)
                    if candidate["matches"]:
                        measured.append(candidate)
                painted = _lookup_faces(
                    selectors.get("appearance_labels", ()),
                    styled,
                    requested_labels=requested_appearance_labels,
                )
                painted_by_source[source_id] = painted
                if not measured and instance_id == str(anchor_id):
                    plausible = [
                        {
                            "face_id": surface,
                            "planar": str(gmsh.model.getType(2, surface)).casefold()
                            == "plane",
                            "plane_distance_mm": abs(
                                float(
                                    np.dot(
                                        np.asarray(
                                            gmsh.model.occ.getCenterOfMass(2, surface),
                                            dtype=float,
                                        )
                                        - plane_origin,
                                        plane_normal,
                                    )
                                )
                            ),
                            "area_mm2": areas[surface],
                        }
                        for surface in surfaces
                    ]
                    if plausible:
                        expected_area = float(contract["expected_disc_area_mm2"])
                        nearest = min(
                            plausible,
                            key=lambda item: (
                                abs(item["area_mm2"] - expected_area) / expected_area,
                                item["plane_distance_mm"],
                            ),
                        )
                        raise ImportedMeshError(
                            "STEP import + normalisation: anchor throat face did not "
                            "resolve after placement; nearest area-compatible face "
                            f"{nearest['face_id']} has plane residual "
                            f"{nearest['plane_distance_mm']:.9g} mm "
                            f"(planar={nearest['planar']})"
                        )
                outcome = resolve_instance_source(
                    source,
                    measured,
                    (),
                    local_body_state=str(instance_by_id[instance_id]["body_evidence"]["local_body_state"]),
                )
                if instance_id == str(anchor_id):
                    candidate = outcome["candidate"]
                    normalisation_record["anchor_resolved_throat"] = {
                        "source_id": source_id,
                        "surface": int(candidate["face_id"]),
                        "plane_residual_mm": float(candidate["plane_distance_mm"]),
                        "normal_residual_deg": float(candidate["normal_angle_deg"]),
                        "plane_gate_mm": PLANE_DISTANCE_MM,
                        "normal_gate_deg": NORMAL_ANGLE_DEG,
                    }
                    if (
                        float(candidate["plane_distance_mm"]) > PLANE_DISTANCE_MM
                        or float(candidate["normal_angle_deg"]) > NORMAL_ANGLE_DEG
                    ):
                        raise ImportedMeshError(
                            "STEP import + normalisation: resolved anchor throat face "
                            f"{candidate['face_id']} has plane residual "
                            f"{candidate['plane_distance_mm']:.9g} mm and normal residual "
                            f"{candidate['normal_angle_deg']:.9g} deg"
                        )
            else:
                mechanisms: dict[str, set[int]] = {}
                if selectors.get("appearance_labels"):
                    mechanisms["appearance_labels"] = _lookup_faces(
                        selectors["appearance_labels"],
                        styled,
                        requested_labels=requested_appearance_labels,
                    )
                if selectors.get("shell_names"):
                    mechanisms["shell_names"] = _lookup_faces(
                        selectors["shell_names"],
                        named,
                        requested_labels=requested_shell_names,
                    )
                if selectors.get("advanced_face_indices"):
                    mechanisms["advanced_face_indices"] = (
                        _advanced_face_identifier_surfaces(
                            selectors["advanced_face_indices"],
                            face_order,
                            ordered_surfaces,
                        )
                    )
                union = set().union(*mechanisms.values()) if mechanisms else set()
                outcome = resolve_user_source(
                    source,
                    mechanisms,
                    face_areas_mm2=areas,
                    connected_components=_face_components(gmsh, union),
                    vocabulary={"shell_names": named, "appearance_labels": styled},
                    allow_area_drift=source_id in area_drift_overrides,
                )
                painted_by_source[source_id] = set(
                    mechanisms.get("appearance_labels", ())
                )
                if outcome.get("area_drift_overridden"):
                    findings.append(
                        {
                            "kind": "source-area-drift-override",
                            "source_id": source_id,
                            "area_relative_drift": outcome["area_relative_drift"],
                        }
                    )
            resolutions[source_id] = outcome

        claimed, paint_findings, unclaimed = _reconcile_source_paint(
            source_list,
            resolutions,
            painted_by_source,
            skipped_source_ids=skipped,
            skipped_paint=skipped_paint,
        )
        findings.extend(paint_findings)
        if unclaimed:
            raise RoleResolutionError(
                f"role resolution: painted source faces match no declared source: {unclaimed}"
            )

        rigid = sorted(set(surfaces) - claimed)
        surface_order_index = {
            int(surface): index for index, surface in enumerate(ordered_surfaces)
        }
        viewport_groups_by_tag: dict[str, list[int]] = {
            str(RIGID_TAG): [surface_order_index[int(surface)] for surface in rigid]
        }
        for source in source_list:
            source_id = str(source["id"])
            outcome = resolutions.get(source_id) or {}
            if outcome.get("skipped"):
                continue
            source_tag = int(allocation["source_tags"][source_id])
            viewport_groups_by_tag[str(source_tag)] = [
                surface_order_index[int(surface)] for surface in outcome["surfaces"]
            ]
        groups = [
            OccSurfaceGroup("rigid", OccSurfaceSelector(rigid), OccSurfaceRole("rigid"))
        ]
        for source in source_list:
            source_id = str(source["id"])
            outcome = resolutions.get(source_id) or {}
            if outcome.get("skipped"):
                continue
            groups.append(
                OccSurfaceGroup(
                    source_id,
                    OccSurfaceSelector(outcome["surfaces"]),
                    # Symmetry is permitted within one source identity only.
                    # The display role (HF/MF/etc.) is intentionally too broad.
                    OccSurfaceRole(source_id),
                )
            )
        source_bboxes = {
            source_id: {
                int(face): tuple(
                    float(value) for value in gmsh.model.getBoundingBox(2, int(face))
                )
                for face in resolution.get("surfaces", ())
            }
            for source_id, resolution in resolutions.items()
            if not resolution.get("skipped")
        }
        cut = auto_cut_occ_geometry(groups)
        cut_groups = {group.name: list(group.selector.surface_tags) for group in cut.groups}
        cut_area_provenance: dict[str, dict[str, float]] = {}
        for source in source_list:
            source_id = str(source["id"])
            if source_id not in cut_groups:
                continue
            before_area = sum(areas[face] for face in resolutions[source_id]["surfaces"])
            after_area = sum(float(gmsh.model.occ.getMass(2, face)) for face in cut_groups[source_id])
            tolerance = float(cut.report.get("tolerance_step_units") or 0.0)
            predicted_area = 0.0
            for face in resolutions[source_id]["surfaces"]:
                face_fraction = 1.0
                for plane in cut.planes:
                    axis = {"x0": 0, "y0": 1, "z0": 2}[plane]
                    bbox = source_bboxes[source_id][int(face)]
                    coincident = (
                        max(abs(float(bbox[axis])), abs(float(bbox[axis + 3])))
                        <= tolerance
                    )
                    if not coincident:
                        face_fraction *= 0.5
                predicted_area += areas[int(face)] * face_fraction
            predicted_fraction = predicted_area / before_area if before_area else 0.0
            cut_area_provenance[source_id] = _post_cut_source_area_record(
                source_id,
                parent_area_mm2=before_area,
                retained_child_area_mm2=after_area,
                predicted_retained_fraction=predicted_fraction,
            )

        gmsh.model.removePhysicalGroups()
        rigid_surfaces = cut_groups.get("rigid", [])
        if rigid_surfaces:
            gmsh.model.addPhysicalGroup(2, rigid_surfaces, RIGID_TAG)
            gmsh.model.setPhysicalName(2, RIGID_TAG, "wg-import-v1|rigid")
        step_specs = []
        for source in source_list:
            source_id = str(source["id"])
            if source_id in skipped or resolutions[source_id].get("skipped"):
                continue
            tag = allocation["source_tags"][source_id]
            selected = cut_groups[source_id]
            gmsh.model.addPhysicalGroup(2, selected, tag)
            gmsh.model.setPhysicalName(
                2,
                tag,
                _physical_name(tag, source_id, source.get("instance_id"), str(source["role"])),
            )
            step_specs.append(
                StepFaceGroup(
                    source_id,
                    StepLabelSelector(source_id),
                    OccSurfaceRole(str(source["role"])),
                    tag=tag,
                    resolution_mm=normalized_sizes["source_size_mm"][source_id],
                )
            )

        all_points = [tag for dim, tag in gmsh.model.getEntities(0) if dim == 0]
        if all_points:
            gmsh.model.mesh.setSize([(0, point) for point in all_points], normalized_sizes["rigid_size_mm"])
        fields: list[int] = []
        for spec in step_specs:
            surfaces_for_source = cut_groups[spec.name]
            distance = gmsh.model.mesh.field.add("Distance")
            gmsh.model.mesh.field.setNumbers(distance, "SurfacesList", surfaces_for_source)
            threshold = gmsh.model.mesh.field.add("Threshold")
            gmsh.model.mesh.field.setNumber(threshold, "InField", distance)
            gmsh.model.mesh.field.setNumber(threshold, "SizeMin", spec.resolution_mm)
            gmsh.model.mesh.field.setNumber(threshold, "SizeMax", normalized_sizes["rigid_size_mm"])
            gmsh.model.mesh.field.setNumber(threshold, "DistMin", 0.0)
            gmsh.model.mesh.field.setNumber(threshold, "DistMax", normalized_sizes["transition_mm"])
            fields.append(threshold)
        if fields:
            minimum = gmsh.model.mesh.field.add("Min")
            gmsh.model.mesh.field.setNumbers(minimum, "FieldsList", fields)
            gmsh.model.mesh.field.setAsBackgroundMesh(minimum)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 1)
        gmsh.option.setNumber("Mesh.MeshSizeMin", tessellation["mesh_size_min_mm"])
        gmsh.option.setNumber("Mesh.MeshSizeMax", tessellation["mesh_size_max_mm"])
        gmsh.option.setNumber(
            "Mesh.MeshSizeFromCurvature",
            tessellation["curvature_segments_per_2pi"],
        )
        gmsh.option.setNumber("Mesh.Algorithm", tessellation["algorithm"])

        mesh_error: Exception | None = None
        try:
            gmsh.model.mesh.generate(2)
        except Exception as exc:  # only this failure is entitled to healing
            mesh_error = exc
        state = {
            "mesh_generation_error": mesh_error,
            "surface_order_reference": geometries,
            "surface_order": ordered_surfaces,
            "reanchor_residuals": reanchor_residuals,
            "role_resolution": resolutions,
            "role_findings": findings,
            "symmetry": cut.report,
            "post_cut_source_areas": cut_area_provenance,
            "source_specs": step_specs,
            "cut_groups": cut_groups,
            "topology_occ": {
                "points": len(gmsh.model.getEntities(0)),
                "curves": len(gmsh.model.getEntities(1)),
                "surfaces": len(gmsh.model.getEntities(2)),
                "volumes": len(gmsh.model.getEntities(3)),
            },
            "transformed_geometry_hash": transformed_geometry_hash,
            "viewport_recipe": {
                "normalisation_matrix": normalization.tolist(),
                "healing_options": list(healing_options),
                "surface_order_reference": [
                    [list(center), float(area)]
                    for center, area in (surface_order_reference or geometries)
                ],
                "groups_by_tag": viewport_groups_by_tag,
            },
        }
        if mesh_error is not None:
            return state

        with tempfile.TemporaryDirectory(prefix="wg2-imported-mesh-") as temporary:
            raw_path = Path(temporary) / "raw.msh"
            gmsh.write(str(raw_path))
            raw_mesh = meshio.read(raw_path)
            processed, repair, topology = postprocess_mesh(
                raw_mesh,
                step_specs,
                symmetry_planes=cut.planes,
                tolerance=5.0e-3,
                symmetry_snap_tolerance=1.0e-4,
            )
            points_mm, triangles, tags = _mesh_arrays(processed)
            frequency = mesh_frequency_validation(
                points_mm,
                triangles,
                tags,
                step_specs,
                unit_scale_to_m=1.0e-3,
                requested_max_frequency_hz=None,
                transition_mm=normalized_sizes["transition_mm"],
            )
            processed.points = points_mm * 1.0e-3
            final_path = Path(temporary) / "imported.msh"
            meshio.write(final_path, processed, file_format="gmsh22", binary=False)
            msh_text = final_path.read_text(encoding="utf-8", errors="replace")
        expected_physical_names = {
            RIGID_TAG: "wg-import-v1|rigid",
            **{
                int(allocation["source_tags"][str(source["id"])]): _physical_name(
                    int(allocation["source_tags"][str(source["id"])]),
                    str(source["id"]),
                    source.get("instance_id"),
                    str(source["role"]),
                )
                for source in source_list
                if str(source["id"]) in allocation["source_tags"]
                and not resolutions[str(source["id"])].get("skipped")
            },
        }
        missing_physical_names = {
            tag: name
            for tag, name in expected_physical_names.items()
            if f'"{name}"' not in msh_text
        }
        if missing_physical_names:
            raise ImportedMeshError(
                "meshing: written artifact is missing allocated physical names "
                f"{missing_physical_names}"
            )
        if len(triangles) == 0:
            raise ImportedMeshError("meshing: imported mesh contains no triangles")
        _enforce_artifact_triangle_ceiling(int(len(triangles)))
        dense = _enforce_dense_solver_memory_ceiling(triangles, 1234, tags=tags)
        integrity = mesh_integrity_report(points_mm * 1.0e-3, triangles, symmetry_plane_axes=tuple({"x0": 0, "y0": 1}[plane] for plane in cut.planes if plane in {"x0", "y0"}))
        if not integrity.get("valid"):
            raise ImportedMeshError("meshing: postprocessed imported mesh failed topology integrity checks")
        bounds_min = np.min(points_mm, axis=0) * 1.0e-3
        bounds_max = np.max(points_mm, axis=0) * 1.0e-3
        unique_tags, counts = np.unique(tags, return_counts=True)
        tag_counts = {str(int(tag)): int(count) for tag, count in zip(unique_tags, counts, strict=True)}
        regions = []
        for surface in cut_groups.get("rigid", []):
            regions.append(Region(float(gmsh.model.occ.getMass(2, surface)), normalized_sizes["rigid_size_mm"], label="rigid", role="shadow"))
        for spec in step_specs:
            for surface in cut_groups[spec.name]:
                regions.append(Region(float(gmsh.model.occ.getMass(2, surface)), spec.resolution_mm, label=spec.name, role="source"))
        sizing_estimate = estimate_mesh_cost(regions).to_dict()
        state.update(
            {
                "msh_text": msh_text,
                "stats": {
                    "vertex_count": int(len(points_mm)),
                    "triangle_count": int(len(triangles)),
                    "tag_counts": tag_counts,
                    "units": "m",
                    "source": "hornlab_waveguide_mesher_step_import",
                    "generated_by": "hornlab-waveguide-mesher",
                    "bounds_m": {"min_x": float(bounds_min[0]), "min_y": float(bounds_min[1]), "min_z": float(bounds_min[2]), "max_x": float(bounds_max[0]), "max_y": float(bounds_max[1]), "max_z": float(bounds_max[2])},
                    "domain_multiplier": float(2 ** len(cut.planes)),
                    "artifact_actual_domain_triangle_limit": MAX_SOLVER_MESH_ARTIFACT_TRIANGLES,
                    "dense_solver_memory_limit_bytes": DENSE_SOLVER_MEMORY_LIMIT_BYTES,
                    "dense_solver_memory_estimate_bytes": dense["estimated_bytes"],
                    "warnings": list(frequency.get("warnings") or []),
                    "integrity": integrity,
                },
                "integrity": integrity,
                "metadata": {
                    "tag_namespace": TAG_NAMESPACE,
                    "tag_map": allocation["tag_map"],
                    "postprocess": repair,
                    "topology": topology,
                    "mesh_frequency_validation": frequency,
                    "occ_tessellation": tessellation,
                },
                "sizing_estimate": sizing_estimate,
            }
        )
        return state

    unhealed = attempt()
    if unhealed.get("mesh_generation_error") is None:
        result = unhealed
        healing = {
            "attempted": False,
            "performed": False,
            "mode": "none",
            "trigger": "unhealed-mesh-succeeded",
            "options": [],
            "original_mesh_error": None,
            "rejected_attempts": [],
            "topology_before": unhealed["topology_occ"],
            "topology_after": unhealed["topology_occ"],
            "selector_reanchor": {"required": False, "matched_surfaces": len(unhealed["surface_order"]), "max_area_relative_error": 0.0, "max_centroid_distance_mm": 0.0, "residuals": [], "gates": {"area_rel": 0.02, "centroid_mm": 5.0}},
            "roles_became_ambiguous": [],
        }
    else:
        original = unhealed["mesh_generation_error"]
        result, mode, rejected_attempts = run_occ_healing_fallbacks(
            attempt,
            original_mesh_error=original,
            original_traceback=original.__traceback__,
            surface_order_reference=unhealed["surface_order_reference"],
        )
        mode_options = {
            "sew": ["Geometry.OCCSewFaces"],
            "full": ["Geometry.OCCFixDegenerated", "Geometry.OCCFixSmallEdges", "Geometry.OCCFixSmallFaces", "Geometry.OCCSewFaces"],
        }[mode]
        healing = {
            "attempted": True,
            "performed": True,
            "mode": mode,
            "trigger": "unhealed-mesh-failed",
            "options": mode_options,
            "original_mesh_error": str(original),
            "rejected_attempts": rejected_attempts,
            "topology_before": unhealed["topology_occ"],
            "topology_after": result["topology_occ"],
            "selector_reanchor": {
                "required": True,
                "matched_surfaces": len(result["surface_order"]),
                "max_area_relative_error": max((item["area_relative_error"] for item in result["reanchor_residuals"]), default=0.0),
                "max_centroid_distance_mm": max((item["centroid_distance_mm"] for item in result["reanchor_residuals"]), default=0.0),
                "residuals": result["reanchor_residuals"],
                "gates": {"area_rel": 0.02, "centroid_mm": 5.0},
            },
            "roles_became_ambiguous": [],
        }
    result.pop("mesh_generation_error", None)
    result.pop("surface_order_reference", None)
    result.pop("surface_order", None)
    result.pop("reanchor_residuals", None)
    result.pop("source_specs", None)
    result.pop("cut_groups", None)
    result["normalisation"] = normalisation_record
    result["tag_allocation"] = allocation
    result["healing"] = healing
    result["polar_grid_derivation"] = polar_grid_from_symmetry(result["symmetry"])
    if include_viewport_mesh:
        try:
            viewport = build_imported_viewport_mesh(
                assembly_path,
                manifest,
                result["viewport_recipe"],
                expected_geometry_hash=str(result["transformed_geometry_hash"]),
                tag_allocation=allocation,
            )
            result["viewport_msh_text"] = viewport["msh_text"]
            result["viewport_mesh"] = {
                "available": True,
                "stats": viewport["stats"],
                "metadata": viewport["metadata"],
            }
        except Exception as exc:
            result["viewport_mesh"] = {
                "available": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
    return result


__all__ = [
    "FIRST_SOURCE_TAG",
    "ImportedMeshError",
    "ImportedMeshDependencyError",
    "RoleResolutionError",
    "TAG_NAMESPACE",
    "allocate_imported_tags",
    "build_imported_mesh",
    "build_imported_viewport_mesh",
    "geometry_candidate_matches",
    "imported_viewport_tessellation_settings",
    "polar_grid_from_symmetry",
    "resolve_instance_source",
    "resolve_user_source",
    "rigid_inverse",
    "validate_imported_sizes",
]

"""Contract-compatible STEP, binary STL, and profile CSV builders."""

from __future__ import annotations

import copy
import math
from pathlib import Path
import struct
import tempfile
from typing import Any, Mapping

import meshio
import numpy as np

from server.design.schema import DesignConfig, Expr
from server.mesh.builder import _solver_mesher_config, _triangles_and_tags
from server.mesh.gmsh_worker import run_on_gmsh_worker
from server.preview.translate import design_to_mesher_config


def _number(value: Expr | None, fallback: float) -> float:
    if value is not None and value.value is not None and math.isfinite(value.value):
        return float(value.value)
    if value is not None:
        try:
            parsed = float(value.raw or "")
        except (TypeError, ValueError):
            pass
        else:
            if math.isfinite(parsed):
                return parsed
    return fallback


def smooth_segments(design: DesignConfig) -> tuple[int, int, int]:
    """Apply v1's smooth-export segment clamps and four-way angular snap."""

    mesh = design.root.mesh
    length = min(160, max(60, int(math.floor(max(3 * _number(mesh.length_segments, 0), 60) + 0.5))))
    angular_raw = max(2 * _number(mesh.angular_segments, 0), 100)
    angular = min(240, max(100, int(math.ceil(angular_raw / 4.0) * 4)))
    corner = min(12, max(4, int(math.floor(max(2 * _number(mesh.corner_segments, 0), 4) + 0.5))))
    return length, angular, corner


def _profile_angular(value: float) -> int:
    count = max(4, int(math.floor(value + 0.5)))
    if count % 4 == 0:
        return count
    return max(8, int(math.ceil(count / 8.0) * 8))


def _prepared_design(
    design: DesignConfig,
    *,
    restore_length: bool = False,
    profile_sampling: bool = False,
) -> DesignConfig:
    payload = copy.deepcopy(design.model_dump(mode="json"))
    root = payload
    original_length = _number(
        design.root.mesh.length_segments,
        40 if profile_sampling else 20,
    )
    length, angular, corner = smooth_segments(design)
    if profile_sampling:
        root["mesh"]["length_segments"] = max(1, int(math.floor(original_length + 0.5)))
        root["mesh"]["angular_segments"] = _profile_angular(
            _number(design.root.mesh.angular_segments, 40)
        )
    else:
        root["mesh"]["length_segments"] = max(1, int(math.floor(original_length + 0.5))) if restore_length else length
        root["mesh"]["angular_segments"] = angular
        root["mesh"]["corner_segments"] = corner
    root["mesh"]["quadrants"] = 1234
    root["mesh"]["wall_thickness"] = 0
    root["mesh"]["vertical_offset"] = 0
    if root.get("enclosure") is not None:
        root["enclosure"]["depth"] = 0
    root.setdefault("simulation", {})["sim_type"] = "freestanding"
    return DesignConfig.model_validate(root)


def _bare_grid_config(
    design: DesignConfig,
    *,
    restore_length: bool,
    profile_sampling: bool = False,
) -> dict[str, Any]:
    prepared = _prepared_design(
        design,
        restore_length=restore_length,
        profile_sampling=profile_sampling,
    )
    config = design_to_mesher_config(prepared)
    config["mode"] = "bare"
    config.pop("enclosure", None)
    mesh = config.setdefault("mesh", {})
    mesh.update(
        {
            "quadrants": 1234,
            "wallThickness": 0.0,
            "verticalOffset": 0.0,
            "scaleToMetres": False,
        }
    )
    return config


def _inner_grid(
    design: DesignConfig,
    *,
    restore_length: bool,
    profile_sampling: bool = False,
) -> np.ndarray:
    try:
        from hornlab_mesher.viewport import build_viewport_geometry_from_config
    except ImportError as exc:
        raise RuntimeError(
            "hornlab-waveguide-mesher viewport geometry is unavailable; install the pinned server requirements"
        ) from exc
    geometry = build_viewport_geometry_from_config(
        _bare_grid_config(
            design,
            restore_length=restore_length,
            profile_sampling=profile_sampling,
        )
    )
    grid = geometry.get("grid") if isinstance(geometry, Mapping) else None
    if not isinstance(grid, Mapping):
        raise RuntimeError("HornLab mesher returned no inner point grid")
    n_phi = int(grid.get("grid_n_phi") or 0)
    n_length = int(grid.get("grid_n_length") or 0)
    points = np.asarray(grid.get("inner_points"), dtype=float)
    expected = n_phi * (n_length + 1) * 3
    if n_phi < 4 or n_length < 1 or points.size != expected:
        raise RuntimeError(
            f"HornLab inner point grid has {points.size} values; expected {expected}"
        )
    points = points.reshape(n_phi, n_length + 1, 3)
    if not np.isfinite(points).all():
        raise RuntimeError("HornLab inner point grid contains non-finite coordinates")
    return points


def _assert_step(step_text: str) -> None:
    required = (
        "ISO-10303-21",
        "END-ISO-10303-21",
        "ADVANCED_FACE",
        "B_SPLINE_SURFACE",
    )
    if not step_text.strip() or any(token not in step_text for token in required):
        raise RuntimeError("STEP export did not contain valid surface geometry")


def _write_step(inner_points: np.ndarray) -> str:
    import gmsh

    initialized_here = False
    step_path: Path | None = None
    try:
        if not gmsh.isInitialized():
            gmsh.initialize()
            initialized_here = True
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Geometry.Tolerance", 1e-8)
        gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-8)
        gmsh.clear()
        gmsh.model.add("WaveguideInnerSurface")
        wire_tags: list[int] = []
        construction_curves: list[int] = []
        n_phi, n_columns, _ = inner_points.shape
        for column in range(n_columns):
            point_tags = [
                int(gmsh.model.occ.addPoint(*(float(value) for value in inner_points[index, column])))
                for index in [*range(n_phi), 0]
            ]
            curve = int(gmsh.model.occ.addBSpline(point_tags))
            construction_curves.append(curve)
            wire_tags.append(int(gmsh.model.occ.addWire([curve], checkClosed=True)))
        gmsh.model.occ.addThruSections(
            wire_tags, makeSolid=False, makeRuled=True, maxDegree=1
        )
        gmsh.model.occ.remove([(1, tag) for tag in construction_curves], recursive=True)
        gmsh.model.occ.synchronize()
        with tempfile.NamedTemporaryFile(prefix="waveguide-inner-", suffix=".step", delete=False) as handle:
            step_path = Path(handle.name)
        gmsh.write(str(step_path))
        text = step_path.read_text(encoding="utf-8", errors="replace")
        _assert_step(text)
        return text
    finally:
        if step_path is not None:
            step_path.unlink(missing_ok=True)
        if initialized_here and gmsh.isInitialized():
            gmsh.finalize()


def _build_step_sync(design_dump: dict[str, Any]) -> str:
    design = DesignConfig.model_validate(design_dump)
    return _write_step(_inner_grid(design, restore_length=True))


async def build_step(design: DesignConfig) -> str:
    """Build the open, ruled, full-domain HornLab inner acoustic surface."""

    return await run_on_gmsh_worker(_build_step_sync, design.model_dump(mode="json"))


def binary_stl(
    vertices_m: list[float] | np.ndarray,
    indices: list[int] | np.ndarray,
    surface_tags: list[int] | np.ndarray,
    model_name: str = "MWG Horn",
) -> bytes:
    """Serialize tag-1 horn triangles as v1-compatible transformed millimetres."""

    points = np.asarray(vertices_m, dtype=float).reshape(-1, 3)
    triangles = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
    tags = np.asarray(surface_tags, dtype=np.int64).reshape(-1)
    if len(tags) != len(triangles):
        raise ValueError("surface tag count does not match triangle count")
    triangles = triangles[tags == 1]
    if not len(triangles):
        raise RuntimeError("solver mesh contains no horn inner-surface triangles (tag 1)")
    if np.any(triangles < 0) or np.any(triangles >= len(points)):
        raise ValueError("triangle index is outside the vertex buffer")

    # HornLab's solver tuple is (x, vertical, axial), whereas v1's local
    # geometry tuple is (x, axial, vertical). Applying the binding v1
    # (x, y, z) -> (x, -z, y) mapping therefore yields this solver-boundary
    # transform: (x, vertical, axial) -> (x, -vertical, axial).
    transformed = np.column_stack(
        (points[:, 0] * 1000.0, -points[:, 1] * 1000.0, points[:, 2] * 1000.0)
    )
    output = bytearray(84 + len(triangles) * 50)
    header = model_name.encode("utf-8")[:79]
    output[: len(header)] = header
    struct.pack_into("<I", output, 80, len(triangles))
    offset = 84
    for triangle in triangles:
        v0, v1, v2 = transformed[triangle]
        normal = np.cross(v1 - v0, v2 - v0)
        magnitude = float(np.linalg.norm(normal))
        if magnitude > 0:
            normal /= magnitude
        struct.pack_into(
            "<12fH",
            output,
            offset,
            *(float(value) for value in normal),
            *(float(value) for value in v0),
            *(float(value) for value in v1),
            *(float(value) for value in v2),
            0,
        )
        offset += 50
    return bytes(output)


def _build_stl_mesh_sync(design_dump: dict[str, Any]) -> dict[str, list[Any]]:
    """Build an authoritative Gmsh mesh while retaining the dense export grid."""

    try:
        from hornlab_mesher.config_builder import build_from_config
    except ImportError as exc:
        raise RuntimeError(
            "hornlab-waveguide-mesher is unavailable; install the pinned server requirements"
        ) from exc
    design = DesignConfig.model_validate(design_dump)
    config = _solver_mesher_config(design)
    config.setdefault("mesh", {}).update(
        {"topology": "legacy", "preserveGrid": True, "scaleToMetres": True}
    )
    with tempfile.TemporaryDirectory(prefix="wg2-stl-mesh-") as temp_dir:
        mesh_path = Path(temp_dir) / "waveguide.msh"
        build_from_config(config, mesh_path)
        parsed = meshio.read(mesh_path)
        triangles, tags = _triangles_and_tags(parsed)
        vertices = np.asarray(parsed.points, dtype=float)
    return {
        "vertices": vertices.reshape(-1).tolist(),
        "indices": triangles.reshape(-1).astype(int).tolist(),
        "surfaceTags": tags.astype(int).tolist(),
    }


async def build_stl(design: DesignConfig, model_name: str = "MWG Horn") -> bytes:
    """Build the densified full-domain solver mesh and retain its horn surface."""

    canonical = await run_on_gmsh_worker(
        _build_stl_mesh_sync,
        _prepared_design(design).model_dump(mode="json"),
    )
    return binary_stl(
        canonical.get("vertices", []),
        canonical.get("indices", []),
        canonical.get("surfaceTags", []),
        model_name,
    )


def profile_csv(inner_points_mm: np.ndarray, kind: str) -> str:
    """Format one v1 Fusion CSV artifact from a phi-major inner point grid."""

    points = np.asarray(inner_points_mm, dtype=float)
    if points.ndim != 3 or points.shape[2] != 3:
        raise ValueError("profile points must have shape (angular, axial, 3)")
    angular, axial_count, _ = points.shape
    if kind == "profiles":
        rows = ["# x_cm;y_cm;z_cm"]
        for phi in range(angular):
            for length in range(axial_count):
                # Mesher points are (x, vertical, axial-position), already the v1 CSV
                # order (x, local-z, local-y) after the local/solver axis swap.
                x, vertical, axial_position = points[phi, length] * 0.1
                rows.append(f"{x:.6f};{vertical:.6f};{axial_position:.6f}")
            rows.append("")
    elif kind == "slices":
        rows = ["# slices: x_cm;y_cm;z_cm"]
        for length in range(axial_count):
            for phi in range(angular + 1):
                x, vertical, axial_position = points[phi % angular, length] * 0.1
                rows.append(f"{x:.6f};{vertical:.6f};{axial_position:.6f}")
            rows.append("")
    else:
        raise ValueError("profile CSV kind must be 'profiles' or 'slices'")
    return "\r\n".join(rows) + "\r\n"


def build_profiles(design: DesignConfig, kind: str) -> str:
    """Rebuild the bare, uniform-ring inner surface without vertical offset."""

    return profile_csv(
        _inner_grid(design, restore_length=True, profile_sampling=True),
        kind,
    )


__all__ = [
    "binary_stl",
    "build_profiles",
    "build_step",
    "build_stl",
    "profile_csv",
    "smooth_segments",
]

"""Adapter from Waveguide Generator payloads to hornlab-waveguide-mesher."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Mapping

import meshio
import numpy as np

try:
    from hornlab_mesher.config_builder import build_from_config
    from hornlab_mesher.viewport import build_viewport_geometry_from_config
except ImportError:  # pragma: no cover - exercised by runtime availability checks
    build_from_config = None  # type: ignore[assignment]
    build_viewport_geometry_from_config = None  # type: ignore[assignment]


class HornlabMesherUnavailable(RuntimeError):
    """Raised when the packaged mesher dependency is not installed."""


def _clean_dict(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _number_list(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = str(value).split(",")
    out: list[float] = []
    for item in raw_items:
        try:
            number = float(str(item).strip())
        except (TypeError, ValueError):
            continue
        if np.isfinite(number):
            out.append(number)
    return out


def _first_number(value: Any) -> float | None:
    numbers = _number_list(value)
    return numbers[0] if numbers else None


def _normalize_formula(value: Any) -> str:
    raw = str(value or "R-OSSE").strip().upper().replace("_", "-")
    if raw == "ROSSE":
        return "R-OSSE"
    if raw not in {"OSSE", "R-OSSE"}:
        raise ValueError(f"formula_type '{value}' is not supported. Supported types: 'R-OSSE', 'OSSE'.")
    return raw


def _normalize_source_shape(value: Any) -> Any:
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return value
    # Waveguide Generator legacy contract: 1=spherical/rounded cap, 2=flat disc.
    # hornlab-waveguide-mesher contract: 1=rounded cap, 0=flat disc.
    if numeric == 2:
        return 0
    return numeric


def _finite_float(value: Any, fallback: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    return numeric if np.isfinite(numeric) else fallback


def waveguide_payload_to_mesher_config(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Translate the existing backend request payload into mesher public config."""

    formula = _normalize_formula(payload.get("formula_type") or payload.get("formula"))
    profile: dict[str, Any] = _clean_dict(
        {
            "formula": formula,
            "r0": payload.get("r0"),
            "a": payload.get("a"),
            "a0": payload.get("a0"),
            "k": payload.get("k"),
            "q": payload.get("q"),
            "throatExtLength": payload.get("throat_ext_length"),
            "throatExtAngle": payload.get("throat_ext_angle"),
            "slotLength": payload.get("slot_length"),
        }
    )
    if formula == "OSSE":
        profile.update(
            _clean_dict(
                {
                    "L": payload.get("L"),
                    "n": payload.get("n"),
                    "s": payload.get("s"),
                    "h": payload.get("h"),
                    "rot": payload.get("rot"),
                    "throatProfile": payload.get("throat_profile"),
                    "circArcRadius": payload.get("circ_arc_radius"),
                    "circArcTermAngle": payload.get("circ_arc_term_angle"),
                }
            )
        )
    else:
        profile.update(
            _clean_dict(
                {
                    "R": payload.get("R"),
                    "r": payload.get("r"),
                    "b": payload.get("b"),
                    "m": payload.get("m"),
                    "tmax": payload.get("tmax"),
                }
            )
        )

    enc_depth = float(payload.get("enc_depth") or 0.0)
    mode = "enclosure" if enc_depth > 0.0 else "freestanding"
    config = {
        "formula": formula,
        "mode": mode,
        "profile": profile,
        "mesh": _clean_dict(
            {
                "angularSegments": payload.get("n_angular"),
                "lengthSegments": payload.get("n_length"),
                "cornerSegments": payload.get("corner_segments"),
                "quadrants": payload.get("quadrants", 1234),
                "wallThickness": payload.get("wall_thickness"),
                "throatResolution": payload.get("throat_res"),
                "mouthResolution": payload.get("mouth_res"),
                "rearResolution": payload.get("rear_res"),
                "encFrontResolution": _first_number(payload.get("enc_front_resolution")),
                "encBackResolution": _first_number(payload.get("enc_back_resolution")),
                "scaleToMetres": True,
            }
        ),
        "cross_section": {
            "exponent": 2.0,
            "aspectRatio": 1.0,
        },
        "morph": _clean_dict(
            {
                "morphTarget": payload.get("morph_target"),
                "morphWidth": payload.get("morph_width"),
                "morphHeight": payload.get("morph_height"),
                "morphCorner": payload.get("morph_corner"),
                "morphRate": payload.get("morph_rate"),
                "morphFixed": payload.get("morph_fixed"),
                "morphAllowShrinkage": payload.get("morph_allow_shrinkage"),
            }
        ),
        "gcurve": _clean_dict(
            {
                "gcurveType": payload.get("gcurve_type"),
                "gcurveWidth": payload.get("gcurve_width"),
                "gcurveAspectRatio": payload.get("gcurve_aspect_ratio"),
                "gcurveDist": payload.get("gcurve_dist"),
                "gcurveRot": payload.get("gcurve_rot"),
                "gcurveSf": payload.get("gcurve_sf"),
                "gcurveSeN": payload.get("gcurve_se_n"),
                "gcurveSfA": payload.get("gcurve_sf_a"),
                "gcurveSfB": payload.get("gcurve_sf_b"),
                "gcurveSfM1": payload.get("gcurve_sf_m1"),
                "gcurveSfM2": payload.get("gcurve_sf_m2"),
                "gcurveSfN1": payload.get("gcurve_sf_n1"),
                "gcurveSfN2": payload.get("gcurve_sf_n2"),
                "gcurveSfN3": payload.get("gcurve_sf_n3"),
            }
        ),
        "source": _clean_dict(
            {
                "sourceShape": _normalize_source_shape(payload.get("source_shape")),
                "sourceRadius": payload.get("source_radius"),
                "sourceCurv": payload.get("source_curv"),
            }
        ),
    }

    if enc_depth > 0.0:
        config["enclosure"] = _clean_dict(
            {
                "depth": enc_depth,
                "space_l": payload.get("enc_space_l"),
                "space_t": payload.get("enc_space_t"),
                "space_r": payload.get("enc_space_r"),
                "space_b": payload.get("enc_space_b"),
                "edge": payload.get("enc_edge"),
                "edgeType": payload.get("enc_edge_type"),
                "frontMeshSize": _first_number(payload.get("enc_front_resolution")),
                "backMeshSize": _first_number(payload.get("enc_back_resolution")),
            }
        )

    return config


def _triangles_and_tags(mesh: meshio.Mesh) -> tuple[np.ndarray, np.ndarray]:
    triangles: list[np.ndarray] = []
    tags: list[np.ndarray] = []
    physical_data = mesh.cell_data.get("gmsh:physical") or mesh.cell_data.get("physical")
    for block_index, cell_block in enumerate(mesh.cells):
        if cell_block.type not in {"triangle", "triangle3"}:
            continue
        triangles.append(np.asarray(cell_block.data, dtype=np.int64))
        if physical_data is not None and block_index < len(physical_data):
            tags.append(np.asarray(physical_data[block_index], dtype=np.int32))
        else:
            tags.append(np.ones(len(cell_block.data), dtype=np.int32))
    if not triangles:
        return np.empty((0, 3), dtype=np.int64), np.empty((0,), dtype=np.int32)
    return np.vstack(triangles), np.concatenate(tags)


def _canonical_mesh_from_msh(path: Path) -> dict[str, Any]:
    mesh = meshio.read(path)
    triangles, tags = _triangles_and_tags(mesh)
    vertices = np.asarray(mesh.points, dtype=float)
    tag_counts = {str(tag): int(np.count_nonzero(tags == tag)) for tag in (1, 2, 3, 4)}
    return {
        "vertices": vertices.reshape(-1).tolist(),
        "indices": triangles.reshape(-1).astype(int).tolist(),
        "surfaceTags": tags.astype(int).tolist(),
        "metadata": {
            "units": "m",
            "unitScaleToMeter": 1.0,
            "tagCounts": tag_counts,
            "generatedBy": "hornlab-waveguide-mesher",
        },
    }


def _reshape_point_grid(raw: Any, n_phi: int, n_length: int, name: str) -> np.ndarray:
    points = np.asarray(raw, dtype=float)
    expected = n_phi * (n_length + 1) * 3
    if points.size != expected:
        raise RuntimeError(f"{name} has {points.size} values; expected {expected}.")
    return points.reshape(n_phi, n_length + 1, 3)


def _write_inner_surface_step(inner_points: np.ndarray) -> str:
    """Write a single-layer inner horn surface STEP file from mesher point-grid data."""

    import gmsh

    if inner_points.ndim != 3 or inner_points.shape[2] != 3:
        raise RuntimeError("inner_points must be shaped (n_phi, n_length + 1, 3).")
    n_phi, n_cols, _ = inner_points.shape
    if n_phi < 4 or n_cols < 2:
        raise RuntimeError("STEP export requires at least 4 angular samples and 2 axial rings.")

    initialized_here = False
    step_path = None
    try:
        if not gmsh.isInitialized():
            gmsh.initialize()
            initialized_here = True
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Geometry.Tolerance", 1e-8)
        gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-8)
        gmsh.clear()
        gmsh.model.add("WaveguideInnerSurface")

        degree_u = min(3, max(1, n_phi - 1))
        degree_v = min(3, max(1, n_cols - 1))
        point_tags: list[int] = []
        for j in range(n_cols):
            for i in list(range(n_phi)) + [0]:
                x, y, z = inner_points[i, j]
                point_tags.append(
                    gmsh.model.occ.addPoint(float(x), float(y), float(z))
                )
        gmsh.model.occ.addBSplineSurface(
            point_tags,
            n_phi + 1,
            degreeU=degree_u,
            degreeV=degree_v,
        )
        gmsh.model.occ.synchronize()

        with tempfile.NamedTemporaryFile(prefix="waveguide-inner-", suffix=".step", delete=False) as tmp:
            step_path = Path(tmp.name)
        gmsh.write(str(step_path))
        return step_path.read_text(encoding="utf-8", errors="replace")
    finally:
        if step_path is not None:
            step_path.unlink(missing_ok=True)
        if initialized_here and gmsh.isInitialized():
            gmsh.finalize()


def build_inner_surface_step(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build a STEP surface body for only the acoustic inner horn surface.

    Wall thickness, rear caps, source caps, and enclosure surfaces are deliberately
    excluded so CAD users can add those features later in SolidWorks.
    """

    if build_viewport_geometry_from_config is None:
        raise HornlabMesherUnavailable("hornlab-waveguide-mesher viewport API is not installed.")

    step_payload = dict(payload)
    step_payload["quadrants"] = 1234
    step_payload["enc_depth"] = 0.0
    step_payload["wall_thickness"] = 0.0
    config = waveguide_payload_to_mesher_config(step_payload)
    config["mode"] = "bare"
    config.setdefault("mesh", {})["wallThickness"] = 0.0
    config.pop("enclosure", None)

    viewport_geometry = build_viewport_geometry_from_config(config)
    grid = viewport_geometry.get("grid") or {}
    n_phi = int(grid.get("grid_n_phi") or 0)
    n_length = int(grid.get("grid_n_length") or 0)
    inner_points = _reshape_point_grid(grid.get("inner_points"), n_phi, n_length, "inner_points")
    step_text = _write_inner_surface_step(inner_points)

    return {
        "step_text": step_text,
        "stats": {
            "units": "mm",
            "surfaceBody": "inner_horn",
            "singleLayer": True,
            "hasWallThickness": False,
            "hasEnclosure": False,
            "hasSourceCap": False,
            "ringCount": n_phi,
            "lengthSteps": n_length,
        },
    }


VIEWPORT_GEOMETRY_ANGULAR_SEGMENTS = 128
VIEWPORT_GEOMETRY_LENGTH_SEGMENTS = 64


def _viewport_config(payload: Mapping[str, Any]) -> dict[str, Any]:
    config = waveguide_payload_to_mesher_config(payload)
    mesh = config.setdefault("mesh", {})
    # The browser preview is no longer driven by the legacy Preview Angular/
    # Length Segments controls. HornLab mesher owns the surface and Gmsh owns
    # the triangle tessellation; these fixed grid values only describe the
    # surface sent to the mesher geometry builder.
    mesh["angularSegments"] = VIEWPORT_GEOMETRY_ANGULAR_SEGMENTS
    mesh["lengthSegments"] = VIEWPORT_GEOMETRY_LENGTH_SEGMENTS
    mesh["quadrants"] = 1234
    mesh["preserveGrid"] = False
    return config


def _viewport_vertices_from_canonical(canonical: Mapping[str, Any]) -> list[float]:
    raw_vertices = canonical.get("vertices")
    points = np.asarray(raw_vertices, dtype=float)
    if points.size % 3 != 0:
        raise RuntimeError("HornLab mesher canonical vertices must contain xyz triples.")
    points = points.reshape(-1, 3)
    if not np.all(np.isfinite(points)):
        raise RuntimeError("HornLab mesher canonical vertices contain non-finite coordinates.")
    points_mm = points * 1000.0
    viewport_points = np.column_stack((points_mm[:, 0], points_mm[:, 2], points_mm[:, 1]))
    return viewport_points.reshape(-1).tolist()


def _sorted_viewport_triangles(
    canonical: Mapping[str, Any],
) -> tuple[list[int], list[int], dict[str, dict[str, int]]]:
    raw_indices = np.asarray(canonical.get("indices"), dtype=np.int64)
    if raw_indices.size % 3 != 0:
        raise RuntimeError("HornLab mesher canonical indices must contain triangle triples.")
    triangles = raw_indices.reshape(-1, 3)
    tags = np.asarray(canonical.get("surfaceTags"), dtype=np.int32)
    if len(tags) != len(triangles):
        raise RuntimeError("HornLab mesher canonical surface tags must match triangle count.")

    source_mask = tags == 2
    enclosure_mask = tags == 3
    wall_mask = ~source_mask & ~enclosure_mask

    ordered_parts = [
        ("horn", wall_mask),
        ("enclosure", enclosure_mask),
        ("throat_disc", source_mask),
    ]
    ordered_triangles: list[np.ndarray] = []
    ordered_tags: list[np.ndarray] = []
    groups: dict[str, dict[str, int]] = {}
    cursor = 0
    for name, mask in ordered_parts:
        part_triangles = triangles[mask]
        if len(part_triangles) == 0:
            continue
        ordered_triangles.append(part_triangles)
        ordered_tags.append(tags[mask])
        groups[name] = {"start": cursor, "end": cursor + len(part_triangles)}
        cursor += len(part_triangles)

    if "horn" in groups:
        groups["inner_wall"] = dict(groups["horn"])
        groups["horn_wall"] = dict(groups["horn"])
    if "throat_disc" in groups:
        groups["source"] = dict(groups["throat_disc"])

    if not ordered_triangles:
        return [], [], groups
    sorted_triangles = np.vstack(ordered_triangles)
    sorted_tags = np.concatenate(ordered_tags)
    return sorted_triangles.reshape(-1).astype(int).tolist(), sorted_tags.astype(int).tolist(), groups


def build_viewport_mesh(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build viewport triangles from the HornLab mesher Gmsh geometry output."""

    if build_from_config is None:
        raise HornlabMesherUnavailable(
            "hornlab-waveguide-mesher is not installed. Install server requirements."
        )

    config = _viewport_config(payload)
    with tempfile.TemporaryDirectory(prefix="wg-viewport-mesher-") as tmp_dir:
        mesh_path = Path(tmp_dir) / "viewport.msh"
        result = build_from_config(config, mesh_path)
        canonical = _canonical_mesh_from_msh(mesh_path)

    vertices = _viewport_vertices_from_canonical(canonical)
    indices, surface_tags, groups = _sorted_viewport_triangles(canonical)
    metadata = canonical.get("metadata") if isinstance(canonical.get("metadata"), Mapping) else {}

    return {
        "vertices": vertices,
        "indices": indices,
        "groups": groups,
        "surfaceTags": surface_tags,
        "metadata": {
            "generatedBy": "hornlab-waveguide-mesher",
            "source": "hornlab_waveguide_mesher_gmsh",
            "units": "mm",
            "formula": result.formula,
            "mode": result.mode,
            "vertexCount": len(vertices) // 3,
            "triangleCount": len(indices) // 3,
            "tagCounts": metadata.get("tagCounts", {}),
            "physicalGroups": result.physical_groups,
            "viewportGeometrySegments": {
                "angular": VIEWPORT_GEOMETRY_ANGULAR_SEGMENTS,
                "length": VIEWPORT_GEOMETRY_LENGTH_SEGMENTS,
            },
            "samplingMode": "gmsh_surface_mesh",
        },
    }


def build_waveguide_mesh(
    payload: Mapping[str, Any],
    *,
    include_canonical: bool = False,
    cancellation_callback=None,
) -> dict[str, Any]:
    """Compatibility wrapper replacing the deleted app-owned mesh builder."""

    if build_from_config is None:
        raise HornlabMesherUnavailable(
            "hornlab-waveguide-mesher is not installed. Install server requirements."
        )
    if cancellation_callback:
        cancellation_callback()

    config = waveguide_payload_to_mesher_config(payload)
    with tempfile.TemporaryDirectory(prefix="wg-hornlab-mesher-") as tmp_dir:
        mesh_path = Path(tmp_dir) / "waveguide.msh"
        result = build_from_config(config, mesh_path)
        if cancellation_callback:
            cancellation_callback()
        msh_text = mesh_path.read_text(encoding="utf-8", errors="replace")
        canonical = _canonical_mesh_from_msh(mesh_path)

    stats = {
        "vertexCount": int(result.n_vertices),
        "triangleCount": int(result.n_triangles),
        "tagCounts": canonical["metadata"]["tagCounts"],
        "units": result.units,
        "source": "hornlab_waveguide_mesher",
        "generatedBy": "hornlab-waveguide-mesher",
    }
    out: dict[str, Any] = {
        "msh_text": msh_text,
        "stats": stats,
    }
    if include_canonical:
        out["canonical_mesh"] = canonical
    return out

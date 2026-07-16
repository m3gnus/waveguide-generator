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
    if raw not in {"OSSE", "R-OSSE", "ICW"}:
        raise ValueError(
            f"formula_type '{value}' is not supported. Supported types: 'R-OSSE', 'OSSE', 'ICW'."
        )
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


# Source.Velocity enum (ABEC/ATH convention): 1 = normal velocity (a uniformly
# breathing cap), 2 = axial (rigid-piston motion, v_n = U * (n_hat . axis) --
# the realistic wavefront for a dome/cone). Both are supported: axial is a
# solve-time boundary condition in the metal-bem solver (config.source_motion),
# NOT a geometry change, so the mesher path accepts either and the solve layer
# applies it.
_SOURCE_VELOCITY_TO_MOTION = {1: "normal", 2: "axial"}


def source_motion_from_payload(payload: Mapping[str, Any]) -> str:
    """Map the WG ``source_velocity`` enum onto a metal-bem ``source_motion``.

    ``1 -> "normal"`` (uniform normal velocity), ``2 -> "axial"`` (rigid piston).
    An unset/blank value defaults to ``"normal"``; any other value is rejected.
    This is the single source of truth for which velocity enums are accepted --
    ``_reject_unsupported_source_payload`` reuses it so the accept/reject sets
    cannot drift apart.
    """
    velocity = payload.get("source_velocity")
    if velocity is None or (isinstance(velocity, str) and not velocity.strip()):
        return "normal"
    try:
        numeric_velocity = float(velocity)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "source_velocity must be 1 (normal) or 2 (axial) for the HornLab "
            f"mesher path; got {velocity!r}"
        ) from exc
    motion = (
        _SOURCE_VELOCITY_TO_MOTION.get(int(numeric_velocity))
        if np.isfinite(numeric_velocity) and numeric_velocity == int(numeric_velocity)
        else None
    )
    if motion is None:
        raise ValueError(
            "source_velocity must be 1 (normal) or 2 (axial) for the HornLab "
            f"mesher path; got {velocity!r}"
        )
    return motion


def _reject_unsupported_source_payload(payload: Mapping[str, Any]) -> None:
    contours = payload.get("source_contours")
    if contours is not None and str(contours).strip():
        raise ValueError(
            "source contours are not supported by the HornLab mesher path; "
            "only the single cap/disc throat source is implemented"
        )
    # Validate the velocity enum (normal/axial). Rejects unsupported values.
    source_motion_from_payload(payload)


def _quadrants_leading_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    sign = 1
    if text.startswith(("+", "-")):
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    digits = []
    for char in text:
        if not char.isdigit():
            break
        digits.append(char)
    return sign * int("".join(digits)) if digits else 0


def _normalise_quadrants(value: Any) -> int:
    leading = _quadrants_leading_int(value)
    return leading if leading in {12, 14, 1234} else 1


def _solver_safe_vertical_offset(payload: Mapping[str, Any]) -> Any:
    vertical_offset = payload.get("vertical_offset")
    if _normalise_quadrants(payload.get("quadrants", 1234)) in {1, 12}:
        # A nonzero y-offset moves y-cut free edges off y=0, which Metal native
        # symmetry rejects. The design layer's auto-symmetry already selects
        # quadrants=14 for offset designs; explicit legacy y-cut payloads keep
        # their requested symmetry domain and omit the unsafe placement.
        if abs(_finite_float(vertical_offset, 0.0)) > 0.0:
            return 0.0
    return vertical_offset


def _positive_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) and numeric > 0.0 else None


def _solver_safe_enclosure_edge(payload: Mapping[str, Any]) -> Any:
    edge = _finite_float(payload.get("enc_edge"), 18.0)
    enc_depth = _finite_float(payload.get("enc_depth"), 0.0)
    if edge <= 0.0 or enc_depth <= 0.0:
        return payload.get("enc_edge")
    if _normalise_quadrants(payload.get("quadrants", 1234)) == 1234:
        return payload.get("enc_edge")

    margins = [
        _finite_float(payload.get(name), 0.0)
        for name in ("enc_space_l", "enc_space_t", "enc_space_r", "enc_space_b")
    ]
    positive_margins = [value for value in margins if value > 0.0]
    if positive_margins and edge >= min(positive_margins) - 1.0e-9:
        # A reduced-domain enclosure whose roundover consumes the smallest
        # baffle margin is geometrically degenerate at the symmetry cut. Some
        # mesher versions tear the front-to-side-wall join after clamping; a
        # sharp edge preserves the requested box extents and keeps the solver
        # boundary closed.
        return 0.0
    return payload.get("enc_edge")


def waveguide_payload_to_mesher_config(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Translate the existing backend request payload into mesher public config."""

    _reject_unsupported_source_payload(payload)

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
            "_athLengthMode": payload.get("length_mode"),
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
    elif formula == "ICW":
        # ICW is solved by the mesher's intrinsic-curvature kernel. Emit only its
        # own size targets and shape inputs; OSSE/R-OSSE shape keys (n, s, rot, m,
        # r, b, tmax) are REJECTED by the mesher's config_builder under ICW, so
        # they must never be forwarded here. r0/a0/k/q from the shared block above
        # are accepted by the ICW validator.
        coverage_angle = _positive_float(payload.get("coverage_angle"))
        coverage_params = (
            {
                "coverage_angle": coverage_angle,
                "hold_start": payload.get("hold_start"),
                "hold_end": payload.get("hold_end"),
            }
            if coverage_angle is not None
            else {}
        )
        profile.update(
            _clean_dict(
                {
                    "L": payload.get("L"),
                    "R": payload.get("R"),
                    "termination": payload.get("termination"),
                    "n_coeff": payload.get("n_coeff"),
                    "theta1_deg": payload.get("theta1_deg"),
                    # Rollback axial target; the mesher's ICW kernel requires one
                    # of depth / x_aperture / x_setback. Ignored for flat_baffle.
                    "depth": payload.get("depth"),
                    **coverage_params,
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
    wall_thickness = _finite_float(payload.get("wall_thickness"), 0.0)
    sim_type = str(payload.get("sim_type") or "2").strip()
    if enc_depth > 0.0:
        mode = "enclosure"
    elif sim_type == "1":
        mode = "infinite-baffle"
    elif wall_thickness > 0.0:
        mode = "freestanding"
    else:
        mode = "bare"
    config = {
        "formula": formula,
        "mode": mode,
        "profile": profile,
        "mesh": _clean_dict(
            {
                "angularSegments": payload.get("n_angular"),
                "lengthSegments": payload.get("n_length"),
                "cornerSegments": payload.get("corner_segments"),
                "samplingMode": payload.get("sampling_mode"),
                "zMapPoints": payload.get("z_map_points"),
                "quadrants": payload.get("quadrants", 1234),
                "wallThickness": payload.get("wall_thickness"),
                "throatResolution": payload.get("throat_res"),
                "mouthResolution": payload.get("mouth_res"),
                "rearResolution": payload.get("rear_res"),
                "apertureResolutionScale": payload.get("aperture_resolution_scale"),
                "maxTriangles": payload.get("max_triangles"),
                "allowLargeMesh": payload.get("allow_large_mesh"),
                "encFrontResolution": _first_number(payload.get("enc_front_resolution")),
                "encBackResolution": _first_number(payload.get("enc_back_resolution")),
                "verticalOffset": _solver_safe_vertical_offset(payload),
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
                "edge": _solver_safe_enclosure_edge(payload),
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


def _json_safe_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe_metadata(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_metadata(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe_metadata(value.tolist())
    if isinstance(value, np.generic):
        return _json_safe_metadata(value.item())
    return value


def _metadata_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    safe = _json_safe_metadata(value)
    return dict(safe) if isinstance(safe, dict) else {}


def _tag_counts_from_tags(tags: np.ndarray) -> dict[str, int]:
    counts = {str(tag): 0 for tag in (1, 2, 3, 4)}
    for raw_tag in np.asarray(tags, dtype=np.int32).tolist():
        tag = int(raw_tag)
        key = str(tag)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _tag_counts_from_msh(path: Path) -> dict[str, int]:
    mesh = meshio.read(path)
    _, tags = _triangles_and_tags(mesh)
    return _tag_counts_from_tags(tags)


def _canonical_mesh_from_msh(path: Path, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    mesh = meshio.read(path)
    triangles, tags = _triangles_and_tags(mesh)
    vertices = np.asarray(mesh.points, dtype=float)
    tag_counts = _tag_counts_from_tags(tags)
    mesher_metadata = _metadata_dict(metadata)
    canonical_metadata = {
        "units": "m",
        "unitScaleToMeter": 1.0,
        "tagCounts": tag_counts,
        "generatedBy": "hornlab-waveguide-mesher",
    }
    canonical_metadata.update(mesher_metadata)
    canonical_metadata["mesherMetadata"] = mesher_metadata
    return {
        "vertices": vertices.reshape(-1).tolist(),
        "indices": triangles.reshape(-1).astype(int).tolist(),
        "surfaceTags": tags.astype(int).tolist(),
        "metadata": canonical_metadata,
    }


def _reshape_point_grid(raw: Any, n_phi: int, n_length: int, name: str) -> np.ndarray:
    points = np.asarray(raw, dtype=float)
    expected = n_phi * (n_length + 1) * 3
    if points.size != expected:
        raise RuntimeError(f"{name} has {points.size} values; expected {expected}.")
    return points.reshape(n_phi, n_length + 1, 3)


def _assert_step_has_geometry(step_text: str) -> None:
    if not isinstance(step_text, str) or not step_text.strip():
        raise RuntimeError("STEP export produced an empty file.")
    if "ISO-10303-21" not in step_text or "END-ISO-10303-21" not in step_text:
        raise RuntimeError("STEP export did not produce a valid STEP exchange file.")
    if "ADVANCED_FACE" not in step_text:
        raise RuntimeError("STEP export produced a file without surface face geometry.")
    if "B_SPLINE_SURFACE" not in step_text:
        raise RuntimeError("STEP export produced a file without the horn surface geometry.")


def _write_inner_surface_step(inner_points: np.ndarray) -> str:
    """Write a single-layer inner horn surface STEP file from mesher point-grid data.

    The STEP contains one bounded ruled loft through all sampled section rings.
    A single smooth B-spline surface imports cleanly but can overshoot the mouth
    boundary; ruled section faces stay inside the sampled waveguide envelope.
    """

    import gmsh

    if inner_points.ndim != 3 or inner_points.shape[2] != 3:
        raise RuntimeError("inner_points must be shaped (n_phi, n_length + 1, 3).")
    if not np.all(np.isfinite(inner_points)):
        raise RuntimeError("inner_points contains non-finite coordinates.")
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

        wire_tags: list[int] = []
        construction_curve_tags: list[int] = []
        for j in range(n_cols):
            point_tags: list[int] = []
            for i in list(range(n_phi)) + [0]:
                x, y, z = inner_points[i, j]
                point_tags.append(
                    int(gmsh.model.occ.addPoint(float(x), float(y), float(z)))
                )
            curve = int(gmsh.model.occ.addBSpline(point_tags))
            construction_curve_tags.append(curve)
            wire_tags.append(int(gmsh.model.occ.addWire([curve], checkClosed=True)))
        gmsh.model.occ.addThruSections(
            wire_tags,
            makeSolid=False,
            makeRuled=True,
            maxDegree=1,
        )
        gmsh.model.occ.remove(
            [(1, tag) for tag in construction_curve_tags],
            recursive=True,
        )
        gmsh.model.occ.synchronize()

        with tempfile.NamedTemporaryFile(prefix="waveguide-inner-", suffix=".step", delete=False) as tmp:
            step_path = Path(tmp.name)
        gmsh.write(str(step_path))
        step_text = step_path.read_text(encoding="utf-8", errors="replace")
        _assert_step_has_geometry(step_text)
        return step_text
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
    if payload.get("step_body", "inner_surface") != "inner_surface":
        raise ValueError(
            f"unsupported STEP body '{payload.get('step_body')}'; supported body: 'inner_surface'"
        )

    step_payload = dict(payload)
    step_payload["step_body"] = "inner_surface"
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
            "stepBody": "inner_surface",
            "surfaceBody": "inner_horn",
            "singleLayer": True,
            "hasWallThickness": False,
            "hasEnclosure": False,
            "hasSourceCap": False,
            "hasThroatPlate": False,
            "ringCount": n_phi,
            "lengthSteps": n_length,
        },
    }


VIEWPORT_GEOMETRY_MIN_ANGULAR_SEGMENTS = 8
VIEWPORT_GEOMETRY_MAX_ANGULAR_SEGMENTS = 256
VIEWPORT_GEOMETRY_MIN_LENGTH_SEGMENTS = 4
VIEWPORT_GEOMETRY_MAX_LENGTH_SEGMENTS = 160
VIEWPORT_GEOMETRY_DEFAULT_ANGULAR_SEGMENTS = 96
VIEWPORT_GEOMETRY_DEFAULT_LENGTH_SEGMENTS = 48


def _clamp_segments(value: Any, lo: int, hi: int, fallback: int) -> int:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, numeric))


def _round_point_list(values: Any, ndigits: int = 6) -> Any:
    """Round display-coordinate lists so the JSON payload stays small."""
    if values is None:
        return None
    return np.round(np.asarray(values, dtype=float), ndigits).tolist()


def _rounded_viewport_grid(grid: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(grid)
    out["inner_points"] = _round_point_list(grid.get("inner_points"))
    out["outer_points"] = _round_point_list(grid.get("outer_points"))
    return out


def _rounded_viewport_enclosure(enclosure: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if enclosure is None:
        return None
    out = dict(enclosure)
    for key in ("mouth_points", "front_outer_points", "back_outer_points"):
        if key in out:
            out[key] = _round_point_list(out[key])
    rings = []
    for ring in enclosure.get("profile_rings") or []:
        rounded = dict(ring)
        rounded["points"] = _round_point_list(ring.get("points"))
        rings.append(rounded)
    out["profile_rings"] = rings
    return out


def build_viewport_geometry(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build fast viewport geometry: horn point grids plus enclosure rings.

    No Gmsh involved — hornlab-waveguide-mesher samples its canonical profile
    math and returns the horn point grid (and outer wall grid when a
    freestanding wall is configured) plus enclosure profile rings so the
    browser can run a cheap display tessellation. Responds in milliseconds,
    unlike the Gmsh surface build, so it is safe to call per keystroke.
    """

    if build_viewport_geometry_from_config is None:
        raise HornlabMesherUnavailable(
            "hornlab-waveguide-mesher is not installed. Install server requirements."
        )

    config = waveguide_payload_to_mesher_config(payload)
    mesh = config.setdefault("mesh", {})
    # The display contract mirrors ATH's full exported geometry. Symmetry-
    # reduced domains remain a solve/export concern.
    mesh["quadrants"] = 1234
    mesh["angularSegments"] = _clamp_segments(
        mesh.get("angularSegments"),
        VIEWPORT_GEOMETRY_MIN_ANGULAR_SEGMENTS,
        VIEWPORT_GEOMETRY_MAX_ANGULAR_SEGMENTS,
        VIEWPORT_GEOMETRY_DEFAULT_ANGULAR_SEGMENTS,
    )
    mesh["lengthSegments"] = _clamp_segments(
        mesh.get("lengthSegments"),
        VIEWPORT_GEOMETRY_MIN_LENGTH_SEGMENTS,
        VIEWPORT_GEOMETRY_MAX_LENGTH_SEGMENTS,
        VIEWPORT_GEOMETRY_DEFAULT_LENGTH_SEGMENTS,
    )

    result = build_viewport_geometry_from_config(config)
    grid = _rounded_viewport_grid(result.get("grid") or {})

    return {
        "formula": result.get("formula"),
        "mode": result.get("mode"),
        "params": result.get("params"),
        "grid": grid,
        "enclosure": _rounded_viewport_enclosure(result.get("enclosure")),
        "metadata": {
            "generatedBy": "hornlab-waveguide-mesher",
            "source": "hornlab_waveguide_mesher_point_grid",
            "units": "mm",
            "gridNPhi": int(grid.get("grid_n_phi") or 0),
            "gridNLength": int(grid.get("grid_n_length") or 0),
            "samplingMode": grid.get("sampling_mode"),
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
        mesher_metadata = _metadata_dict(getattr(result, "metadata", None))
        if cancellation_callback:
            cancellation_callback()
        msh_text = mesh_path.read_text(encoding="utf-8", errors="replace")
        # The canonical payload converts every vertex/index/tag into Python
        # lists — significant CPU/RAM on solve-density meshes. Callers that
        # only want the .msh (include_canonical=False) still need tagCounts
        # for stats, which a plain array pass provides cheaply.
        if include_canonical:
            canonical = _canonical_mesh_from_msh(mesh_path, mesher_metadata)
            tag_counts = canonical["metadata"]["tagCounts"]
        else:
            canonical = None
            tag_counts = _tag_counts_from_msh(mesh_path)

    stats = {
        "vertexCount": int(result.n_vertices),
        "triangleCount": int(result.n_triangles),
        "tagCounts": tag_counts,
        "units": result.units,
        "source": "hornlab_waveguide_mesher",
        "generatedBy": "hornlab-waveguide-mesher",
        "metadata": mesher_metadata,
    }
    out: dict[str, Any] = {
        "msh_text": msh_text,
        "stats": stats,
        "metadata": mesher_metadata,
    }
    if include_canonical:
        out["canonical_mesh"] = canonical
    return out

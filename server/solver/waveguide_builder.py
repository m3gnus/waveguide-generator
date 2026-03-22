"""
Waveguide mesh builder using Gmsh OCC (OpenCASCADE) Python API.

Generates Gmsh-authored .msh (and optional STL) from ATH-format parameters
directly inside Gmsh using parametric BSpline curves and ThruSections surfaces.

This is the architecturally correct approach per ATH section 3.3.1:
  "for each slice a smooth spline curve is created (controlled by the grid
   points), surface stripes between each adjacent pair of slices are created,
   each stripe is meshed independently."

Supported formula types:
  R-OSSE  — Radius-parameterized OSSE (mouth radius R drives axial length)
  OSSE    — Classic OSSE with explicit Length, Term.s/n/q

Optional features (both formula types):
  Throat extension   — throat_ext_angle / throat_ext_length
  Slot               — slot_length (initial straight segment)
  Circular arc       — throat_profile=3 with circ_arc_* params
  Profile rotation   — rot
  Guiding curves     — gcurve_type / gcurve_sf / gcurve_se_*
  Morph              — morph_target rect or circle
  h-adjustment       — h bulge parameter (OSSE only)

Physical group names follow the ABEC/ATH convention:
  SD1G0     (tag 1) - inner horn wall surface
  SD1D1001  (tag 2) - throat source disc (driving element)
  SD2G0     (tag 3) - outer/rear/mouth surfaces (free-standing only)
"""

from __future__ import annotations

import logging
import math
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

import numpy as np

from .deps import GMSH_AVAILABLE, gmsh
from .gmsh_utils import gmsh_lock, parse_msh_stats, GmshMeshingError
from .waveguide_mesh_extraction import (
    _build_surface_identity_by_entity,
    _count_triangle_identities,
    _extract_canonical_mesh_from_model,
    _orient_and_validate_canonical_mesh,
)


FACE_IDENTITY_ORDER: Tuple[str, ...] = (
    "inner_wall",
    "outer_wall",
    "mouth_rim",
    "throat_return",
    "rear_cap",
    "horn_wall",
    "throat_disc",
    "enc_front",
    "enc_side",
    "enc_rear",
    "enc_edge",
)


# Horn profile computation functions (extracted to waveguide_profiles.py)
# ---------------------------------------------------------------------------
from .waveguide_profiles import (  # noqa: E402
    _expression_to_callable,
    _make_callable,
    _run_cancellation_callback,
    _get_float,
    _get_int,
    _get_bool,
    _parse_number_list,
    _compute_rosse_profile,
    _compute_osse_base_radius,
    _compute_osse_term_radius,
    _compute_osse_radius_at,
    _invert_osse_coverage_angle,
    _evaluate_circular_arc,
    _parse_superformula_params,
    _compute_guiding_curve_radius,
    _compute_coverage_from_guiding_curve,
    _build_osse_callables,
    _compute_osse_profile_arrays,
    _get_rounded_rect_radius,
    _get_morph_target_radius,
    _apply_morph,
    _compute_morph_target_info,
    _compute_phi_values,
    _compute_point_grids,
    _compute_outer_points,
)


# Gmsh geometry construction (OCC kernel)
# ---------------------------------------------------------------------------

def _make_wire(points_2d: np.ndarray, closed: bool = True) -> Tuple[int, List[int], Tuple[int, int]]:
    """Create a BSpline wire from an (n, 3) array of 3D points. Returns (wire_tag, curve_tags, (first_pt, last_pt))."""
    n = points_2d.shape[0]
    pt_tags = [gmsh.model.occ.addPoint(float(points_2d[i, 0]), float(points_2d[i, 1]), float(points_2d[i, 2])) for i in range(n)]
    first_pt = pt_tags[0]
    last_pt = pt_tags[-1]
    if closed:
        pt_tags.append(pt_tags[0])
    spline = gmsh.model.occ.addBSpline(pt_tags)
    return gmsh.model.occ.addWire([spline]), [spline], (first_pt, last_pt)


def _make_closed_wire_and_loop(points_2d: np.ndarray) -> Tuple[int, int]:
    """Create a closed BSpline wire and its curve loop tag."""
    n = points_2d.shape[0]
    pt_tags = [gmsh.model.occ.addPoint(float(points_2d[i, 0]), float(points_2d[i, 1]), float(points_2d[i, 2])) for i in range(n)]
    pt_tags.append(pt_tags[0])
    spline = gmsh.model.occ.addBSpline(pt_tags)
    wire = gmsh.model.occ.addWire([spline])
    try:
        loop = gmsh.model.occ.addCurveLoop([spline], reorient=True)
    except TypeError:
        loop = gmsh.model.occ.addCurveLoop([spline])
    return int(wire), int(loop)


def _build_surface_from_points(
    points: np.ndarray, closed: bool = True
) -> List[Tuple[int, int]]:
    """Build horn surface as BSpline surface patches fitted to the point grid.

    Creates continuous BSpline surface(s) directly from the (n_phi, n_len, 3)
    point grid using ``addBSplineSurface``.  This replaces the previous
    per-phi longitudinal-wire strip approach which imposed n_phi angular
    constraint edges on the mesh.  With BSpline patches the mesher is free
    to triangulate based purely on size-field control.

    For closed (full-circle) surfaces two half-patches are created
    (0 → π and π → 2π) and OCC-fragmented so the two seam edges share
    topology and the resulting mesh is watertight.

    points: (n_phi, n_length+1, 3)
    closed: True for full-circle (quadrants == 1234)
    Returns list of (dim=2, tag) surface dimtags.
    """
    n_phi, n_len, _ = points.shape
    deg_v = min(3, max(1, n_len - 1))

    def _make_patch(col_indices: List[int]) -> int:
        """Create a single BSpline surface from the given phi-column indices."""
        n_u = len(col_indices)
        deg_u = min(3, max(1, n_u - 1))
        pt_tags: List[int] = []
        for j in range(n_len):
            for ci in col_indices:
                pt_tags.append(gmsh.model.occ.addPoint(
                    float(points[ci, j, 0]),
                    float(points[ci, j, 1]),
                    float(points[ci, j, 2]),
                ))
        return gmsh.model.occ.addBSplineSurface(
            pt_tags, n_u, degreeU=deg_u, degreeV=deg_v,
        )

    if not closed:
        tag = _make_patch(list(range(n_phi)))
        return [(2, tag)]

    # Closed: two half-patches with shared seam columns at φ=0 and φ=π.
    half = n_phi // 2
    tag1 = _make_patch(list(range(0, half + 1)))
    tag2 = _make_patch(list(range(half, n_phi)) + [0])

    # OCC fragment merges the coincident seam edges so the mesh is watertight.
    result, _ = gmsh.model.occ.fragment([(2, tag1), (2, tag2)], [])
    return [(d, t) for d, t in result if d == 2]


def _build_throat_disc(r0: float) -> List[Tuple[int, int]]:
    """Build a flat circular throat disc (legacy helper)."""
    disk_tag = gmsh.model.occ.addDisk(0.0, 0.0, 0.0, r0, r0)
    return [(2, disk_tag)]


def _build_throat_disc_from_ring(
    throat_ring_points: np.ndarray,
    closed: bool,
) -> List[Tuple[int, int]]:
    """Build the source/piston disc from the exact throat ring wire."""
    wire, curves, eps = _make_wire(throat_ring_points, closed=closed)
    if closed:
        loop = gmsh.model.occ.addCurveLoop([int(c) for c in curves])
    else:
        line_tag = gmsh.model.occ.addLine(eps[1], eps[0])
        loop = gmsh.model.occ.addCurveLoop([int(c) for c in curves] + [int(line_tag)])
    fill = gmsh.model.occ.addPlaneSurface([loop])
    return [(2, int(fill))]


def _build_throat_disc_from_inner_boundary(
    inner_dimtags: List[Tuple[int, int]],
    closed: bool,
) -> List[Tuple[int, int]]:
    """Build throat disc from the inner-surface throat boundary curves.

    This keeps the source disc topologically attached to the inner wall by
    reusing the same OCC boundary curves instead of creating an independent
    wire from control points.
    """
    if not inner_dimtags:
        return []

    boundary = gmsh.model.getBoundary(inner_dimtags, oriented=False, combined=False)
    curve_tags: List[int] = []
    seen: Set[int] = set()
    for dim, tag in boundary:
        if int(dim) != 1:
            continue
        ctag = int(tag)
        if ctag in seen:
            continue
        seen.add(ctag)
        curve_tags.append(ctag)

    if not curve_tags:
        return []

    z_bounds: Dict[int, Tuple[float, float]] = {}
    z_min = float("inf")
    z_max = float("-inf")
    for ctag in curve_tags:
        _, _, z0, _, _, z1 = gmsh.model.getBoundingBox(1, ctag)
        lo = float(min(z0, z1))
        hi = float(max(z0, z1))
        z_bounds[ctag] = (lo, hi)
        z_min = min(z_min, lo)
        z_max = max(z_max, hi)

    if not math.isfinite(z_min):
        return []

    eps = max(1e-6, abs(z_max - z_min) * 1e-3)
    throat_curves = [
        ctag
        for ctag in curve_tags
        if abs(z_bounds[ctag][0] - z_min) <= eps and abs(z_bounds[ctag][1] - z_min) <= eps
    ]
    if not throat_curves:
        return []

    if closed:
        loop = gmsh.model.occ.addCurveLoop(throat_curves)
    else:
        # For partial symmetry (e.g. half-disc), find the endpoints of the throat arc
        # and connect them with a straight chord.
        try:
            boundary_pts = gmsh.model.getBoundary([(1, t) for t in throat_curves], oriented=False, combined=True)
            pt_tags = [int(abs(t)) for d, t in boundary_pts if d == 0]
            if len(pt_tags) >= 2:
                line_tag = gmsh.model.occ.addLine(pt_tags[0], pt_tags[1])
                loop = gmsh.model.occ.addCurveLoop(throat_curves + [int(line_tag)])
            else:
                loop = gmsh.model.occ.addCurveLoop(throat_curves)
        except Exception:
            loop = gmsh.model.occ.addCurveLoop(throat_curves)

    try:
        fill = gmsh.model.occ.addPlaneSurface([loop])
    except Exception:
        # Planar filling failed? fallback to general filling.
        fill = gmsh.model.occ.addSurfaceFilling(loop)
    return [(2, int(fill))]


def _boundary_curves_at_z_extreme(
    dimtags: List[Tuple[int, int]], want_min_z: bool
) -> List[int]:
    """Return boundary curve tags lying at the min or max z-extent of dimtags.

    Requires a prior synchronize() so getBoundary and getBoundingBox are valid.
    Curves shared by two surfaces (internal seam edges) are excluded — only
    exterior boundary curves are returned (combined=True).

    A curve is considered to lie at the z-extreme if its z midpoint is within
    a tolerance of the global z-extreme.  Uses midpoint to handle BSpline surfaces
    whose throat/mouth boundary curves have small numerical noise (±1e-7 mm).
    """
    boundary = gmsh.model.getBoundary(dimtags, oriented=False, combined=True)
    curve_tags: List[int] = [int(abs(tag)) for dim, tag in boundary if int(dim) == 1]
    if not curve_tags:
        return []

    z_mid_map: Dict[int, float] = {}
    z_extreme = float("inf") if want_min_z else float("-inf")
    for ctag in curve_tags:
        _, _, z0, _, _, z1 = gmsh.model.getBoundingBox(1, ctag)
        z_mid = 0.5 * (float(z0) + float(z1))
        z_mid_map[ctag] = z_mid
        if want_min_z:
            z_extreme = min(z_extreme, z_mid)
        else:
            z_extreme = max(z_extreme, z_mid)

    if not math.isfinite(z_extreme):
        return []

    # Use a tolerance proportional to the overall z-span of all curves.
    z_mids = list(z_mid_map.values())
    z_span = max(abs(max(z_mids) - min(z_mids)), 1e-6)
    eps = 0.01 * z_span  # 1% of z-span
    return [ctag for ctag in curve_tags if abs(z_mid_map[ctag] - z_extreme) <= eps]


def _build_rear_wall(
    inner_points: np.ndarray, outer_points: np.ndarray, closed: bool
) -> List[Tuple[int, int]]:
    """Annular surface connecting inner and outer surfaces at the throat (z≈0)."""
    w_inner, _, _ = _make_wire(inner_points[:, 0, :], closed=closed)
    w_outer, _, _ = _make_wire(outer_points[:, 0, :], closed=closed)
    return gmsh.model.occ.addThruSections([int(w_inner), int(w_outer)], makeSolid=False, makeRuled=True)


def _build_annular_surface_from_boundaries(
    inner_dimtags: List[Tuple[int, int]],
    outer_dimtags: List[Tuple[int, int]],
    want_min_z: bool,
) -> List[Tuple[int, int]]:
    """Build a ruled annular surface using actual OCC boundary curves at min or max z.

    Connects the z-extreme boundary curves of inner_dimtags to those of outer_dimtags.
    Reuses the existing OCC curve entities so the resulting surface shares topology with
    both surfaces without a fragment call.
    Requires a prior synchronize() so getBoundary and getBoundingBox are valid.
    Returns empty list if boundary curves cannot be resolved.
    """
    inner_curves = _boundary_curves_at_z_extreme(inner_dimtags, want_min_z=want_min_z)
    outer_curves = _boundary_curves_at_z_extreme(outer_dimtags, want_min_z=want_min_z)
    if not inner_curves or not outer_curves:
        return []
    try:
        iw = gmsh.model.occ.addCurveLoop(inner_curves, reorient=True)
    except TypeError:
        iw = gmsh.model.occ.addCurveLoop(inner_curves)
    try:
        ow = gmsh.model.occ.addCurveLoop(outer_curves, reorient=True)
    except TypeError:
        ow = gmsh.model.occ.addCurveLoop(outer_curves)
    result = gmsh.model.occ.addThruSections([iw, ow], makeSolid=False, makeRuled=True)
    return list(result)


def _build_mouth_rim_from_boundaries(
    inner_dimtags: List[Tuple[int, int]],
    outer_dimtags: List[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    """Build mouth rim annular surface using actual OCC boundary curves at the mouth (max z).

    Requires a prior synchronize().  Returns empty list if boundary curves cannot be resolved.
    """
    return _build_annular_surface_from_boundaries(inner_dimtags, outer_dimtags, want_min_z=False)


def _build_mouth_rim(
    inner_points: np.ndarray, outer_points: np.ndarray, closed: bool
) -> List[Tuple[int, int]]:
    """Annular surface connecting inner and outer surfaces at the mouth end."""
    j_mouth = inner_points.shape[1] - 1
    w_inner, _, _ = _make_wire(inner_points[:, j_mouth, :], closed=closed)
    w_outer, _, _ = _make_wire(outer_points[:, j_mouth, :], closed=closed)
    return gmsh.model.occ.addThruSections([int(w_inner), int(w_outer)], makeSolid=False, makeRuled=True)


def _build_rear_disc_assembly(
    outer_points: np.ndarray,
    wall_thickness: float,
    closed: bool,
    *,
    outer_dimtags: Optional[List[Tuple[int, int]]] = None,
) -> List[Tuple[int, int]]:
    """Build the rear closure of the outer wall shell: axial step face + flat disc.

    outer_points[:, 0, :] is the outer throat ring at z_throat (same z as inner throat,
    purely radially offset by wall_thickness).  The rear disc sits at z_rear = z_throat
    - wall_thickness.

    Two surfaces, matching JS freestandingWall.js geometry:
      1. Ruled axial step face: outer throat ring → disc edge ring (same XY, z moved to z_rear).
         Corresponds to the JS outer-shell row-0→row-1 strip at the throat.
      2. Flat disc at z_rear: filled with addPlaneSurface (fast, exact for planar rings).
         Corresponds to JS addRearThroatDisc fan.

    Using addPlaneSurface instead of addSurfaceFilling avoids the slow OCC curved-surface
    solver for what is always a flat planar region.
    """
    # Preferred path: reuse actual outer-surface throat boundary curves so the rear
    # closure is topologically attached to the outer wall without OCC boolean fragment.
    # This avoids loop failures from fragment-induced tiny self-intersections.
    if outer_dimtags:
        throat_curves = _boundary_curves_at_z_extreme(outer_dimtags, want_min_z=True)
        if throat_curves:
            front_loop = _add_curve_loop_from_curves(throat_curves)
            copied_dimtags = gmsh.model.occ.copy([(1, int(curve)) for curve in throat_curves])
            copied_curves = [int(tag) for dim, tag in copied_dimtags if int(dim) == 1]
            if copied_curves:
                gmsh.model.occ.translate(
                    [(1, int(curve)) for curve in copied_curves],
                    0.0,
                    0.0,
                    -float(wall_thickness),
                )
                rear_loop = _add_curve_loop_from_curves(copied_curves)
                annular_dimtags = gmsh.model.occ.addThruSections(
                    [int(front_loop), int(rear_loop)],
                    makeSolid=False,
                    makeRuled=True,
                )
                disc_fill = gmsh.model.occ.addPlaneSurface([int(rear_loop)])
                return list(annular_dimtags) + [(2, int(disc_fill))]

    # Fallback path: build rear closure from control-point wires.
    throat_ring = outer_points[:, 0, :].copy()  # (n_phi, 3) at z_throat
    z_rear = float(np.mean(throat_ring[:, 2])) - wall_thickness

    disc_ring = throat_ring.copy()
    disc_ring[:, 2] = z_rear

    # Surface 1: ruled axial step face (outer throat ring → disc ring)
    w_front, _, _ = _make_wire(throat_ring, closed=closed)
    w_rear, _, _ = _make_wire(disc_ring, closed=closed)
    annular_dimtags = gmsh.model.occ.addThruSections(
        [int(w_front), int(w_rear)], makeSolid=False, makeRuled=True
    )

    # Surface 2: flat disc at z_rear using addPlaneSurface (fast for planar rings)
    pt_tags = []
    n = disc_ring.shape[0]
    for k in range(n):
        pt_tags.append(gmsh.model.occ.addPoint(
            float(disc_ring[k, 0]), float(disc_ring[k, 1]), float(disc_ring[k, 2])
        ))
    if closed:
        pt_tags.append(pt_tags[0])
    spline = gmsh.model.occ.addBSpline(pt_tags)
    cl = gmsh.model.occ.addCurveLoop([spline])
    disc_fill = gmsh.model.occ.addPlaneSurface([cl])

    return list(annular_dimtags) + [(2, disc_fill)]


# ---------------------------------------------------------------------------
# Enclosure helpers — extracted to waveguide_enclosure.py
# ---------------------------------------------------------------------------
from .waveguide_enclosure import (  # noqa: E402
    _add_curve_loop_from_curves,
    _add_ruled_section,
    _axial_interpolated_size,
    _build_enclosure_box,
    _classify_enclosure_surfaces,
    _collect_boundary_curves,
    _enclosure_resolution_formula,
    _generate_enclosure_points_from_angles,
    _intersect_ray_with_rounded_box,
    _panel_bilinear_resolution_formula,
    _panel_corner_points_by_quadrant,
    _parse_quadrant_resolutions,
    _rear_resolution_active,
    _ring_points_from_xy_plan,
)


# ---------------------------------------------------------------------------
# Mesh size configuration
# ---------------------------------------------------------------------------

def _configure_mesh_size(
    inner_points: np.ndarray,
    surface_groups: Dict[str, List[int]],
    throat_res: float,
    mouth_res: float,
    rear_res: float,
    *,
    enc_front_resolution: Optional[str] = None,
    enc_back_resolution: Optional[str] = None,
    enclosure_bounds: Optional[Dict[str, float]] = None,
) -> None:
    """Set per-surface mesh resolution using Gmsh Restrict fields.

    Inner horn surface: interpolated throat_res → mouth_res by axial distance.
    Outer wall shell (free-standing only): constant rear_res.
    Mouth rim: interpolated throat_res → mouth_res by axial distance.
    Throat disc: constant throat_res.
    Rear wall (free-standing only): constant rear_res.
    Enclosure: continuous front/back bilinear corner interpolation over x/y/z.
    Enclosure edges (roundovers/chamfers):
      - Front edge strips follow front-panel corner interpolation.
      - Back edge strips follow back-panel corner interpolation.
      - Any remaining connector edges use the continuous front↔back formula.
    """
    fields: List[int] = []
    z_throat = float(np.mean(inner_points[:, 0, 2]))
    z_mouth = float(np.mean(inner_points[:, -1, 2]))
    z_span = max(abs(z_mouth - z_throat), 1e-6)

    curve_groups: Dict[str, List[int]] = {}
    for group_name, group_surfaces in surface_groups.items():
        if group_surfaces:
            curve_groups[group_name] = _collect_boundary_curves(group_surfaces)

    def add_restricted_matheval(
        formula: str,
        surface_tags: List[int],
        curve_tags: List[int],
    ) -> Optional[int]:
        if len(surface_tags) == 0 and len(curve_tags) == 0:
            return None
        f_base = gmsh.model.mesh.field.add("MathEval")
        gmsh.model.mesh.field.setString(f_base, "F", formula)
        f_restrict = gmsh.model.mesh.field.add("Restrict")
        gmsh.model.mesh.field.setNumber(f_restrict, "InField", f_base)
        gmsh.model.mesh.field.setNumber(f_restrict, "IncludeBoundary", 0)
        if surface_tags:
            gmsh.model.mesh.field.setNumbers(f_restrict, "SurfacesList", surface_tags)
        if curve_tags:
            gmsh.model.mesh.field.setNumbers(f_restrict, "CurvesList", curve_tags)
        return f_restrict

    def add_restricted_threshold_from_points(
        point_tags: List[int],
        surface_tags: List[int],
        curve_tags: List[int],
        size_min: float,
        size_max: float,
        dist_max: float,
    ) -> Optional[int]:
        if len(point_tags) == 0 or (len(surface_tags) == 0 and len(curve_tags) == 0):
            return None
        f_dist = gmsh.model.mesh.field.add("Distance")
        gmsh.model.mesh.field.setNumbers(f_dist, "PointsList", point_tags)
        f_threshold = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(f_threshold, "InField", f_dist)
        gmsh.model.mesh.field.setNumber(f_threshold, "SizeMin", float(size_min))
        gmsh.model.mesh.field.setNumber(f_threshold, "SizeMax", float(size_max))
        gmsh.model.mesh.field.setNumber(f_threshold, "DistMin", 0.0)
        gmsh.model.mesh.field.setNumber(f_threshold, "DistMax", max(float(dist_max), 1e-6))
        f_restrict = gmsh.model.mesh.field.add("Restrict")
        gmsh.model.mesh.field.setNumber(f_restrict, "InField", f_threshold)
        gmsh.model.mesh.field.setNumber(f_restrict, "IncludeBoundary", 0)
        if surface_tags:
            gmsh.model.mesh.field.setNumbers(f_restrict, "SurfacesList", surface_tags)
        if curve_tags:
            gmsh.model.mesh.field.setNumbers(f_restrict, "CurvesList", curve_tags)
        return f_restrict

    # Horn surfaces (throat -> mouth interpolation by axial coordinate z)
    slope = (mouth_res - throat_res) / z_span
    intercept = throat_res - slope * z_throat
    axial_formula = f"{intercept:.6g} + ({slope:.6g}) * z"
    for group_key in ("inner", "mouth"):
        field = add_restricted_matheval(
            axial_formula,
            surface_groups.get(group_key, []),
            curve_groups.get(group_key, []),
        )
        if field:
            fields.append(field)

    # In free-standing thickened mode, the outer shell is part of the rear-domain
    # resolution policy and should not inherit the inner horn axial interpolation.
    free_standing_wall_mode = bool(surface_groups.get("outer")) and not bool(
        surface_groups.get("enclosure")
    )
    outer_formula = f"{rear_res:.6g}" if free_standing_wall_mode else axial_formula
    outer_field = add_restricted_matheval(
        outer_formula,
        surface_groups.get("outer", []),
        curve_groups.get("outer", []),
    )
    if outer_field:
        fields.append(outer_field)

    # Source disc fixed to throat resolution
    throat_field = add_restricted_matheval(
        f"{throat_res:.6g}",
        surface_groups.get("throat_disc", []),
        curve_groups.get("throat_disc", []),
    )
    if throat_field:
        fields.append(throat_field)

    # Free-standing rear wall fixed to rear resolution
    rear_field = add_restricted_matheval(
        f"{rear_res:.6g}",
        surface_groups.get("rear", []),
        curve_groups.get("rear", []),
    )
    if rear_field:
        fields.append(rear_field)

    enclosure_resolution_values: List[float] = []

    # Enclosure uses a continuous interpolation between front/back corner resolutions.
    # Front/back roundover surfaces follow their nearby panel resolution;
    # side walls use z-interpolated formula between front and back.
    if enclosure_bounds:
        bx0 = float(enclosure_bounds["bx0"])
        bx1 = float(enclosure_bounds["bx1"])
        by0 = float(enclosure_bounds["by0"])
        by1 = float(enclosure_bounds["by1"])
        z_front = float(enclosure_bounds["z_front"])
        z_back = float(enclosure_bounds["z_back"])

        front_q = _parse_quadrant_resolutions(enc_front_resolution, mouth_res)
        back_q = _parse_quadrant_resolutions(enc_back_resolution, mouth_res)
        enclosure_resolution_values.extend(front_q)
        enclosure_resolution_values.extend(back_q)

        front_panel_formula = _panel_bilinear_resolution_formula(
            front_q, bx0=bx0, bx1=bx1, by0=by0, by1=by1,
        )
        back_panel_formula = _panel_bilinear_resolution_formula(
            back_q, bx0=bx0, bx1=bx1, by0=by0, by1=by1,
        )
        enclosure_formula = _enclosure_resolution_formula(
            front_q, back_q,
            bx0=bx0, bx1=bx1, by0=by0, by1=by1,
            z_front=z_front, z_back=z_back,
        )

        # Side walls + any non-edge enclosure surfaces: z-interpolated resolution
        enclosure_field = add_restricted_matheval(
            enclosure_formula,
            surface_groups.get("enclosure", []),
            curve_groups.get("enclosure", []),
        )
        if enclosure_field:
            fields.append(enclosure_field)

        # Front roundover surfaces follow front panel resolution
        front_edge_field = add_restricted_matheval(
            front_panel_formula,
            surface_groups.get("enclosure_edges_front", []),
            curve_groups.get("enclosure_edges_front", []),
        )
        if front_edge_field:
            fields.append(front_edge_field)

        # Back roundover surfaces follow back panel resolution
        back_edge_field = add_restricted_matheval(
            back_panel_formula,
            surface_groups.get("enclosure_edges_back", []),
            curve_groups.get("enclosure_edges_back", []),
        )
        if back_edge_field:
            fields.append(back_edge_field)
    else:
        # Fallback for partial/no-enclosure metadata in reduced-domain modes.
        fallback_formula = f"{mouth_res:.6g}"
        for group_key in ("enclosure_sides", "enclosure_edges_front", "enclosure_edges_back", "enclosure_edges"):
            f = add_restricted_matheval(
                fallback_formula,
                surface_groups.get(group_key, []),
                curve_groups.get(group_key, []),
            )
            if f:
                fields.append(f)

    if fields:
        f_min = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(f_min, "FieldsList", fields)
        gmsh.model.mesh.field.setAsBackgroundMesh(f_min)

    mesh_sizes = [float(throat_res), float(mouth_res), float(rear_res)]
    mesh_sizes.extend(
        float(v) for v in enclosure_resolution_values if math.isfinite(v) and float(v) > 0.0
    )
    mesh_sizes = [v for v in mesh_sizes if math.isfinite(v) and v > 0.0]
    if not mesh_sizes:
        mesh_sizes = [1.0]
    gmsh.option.setNumber("Mesh.MeshSizeMin", min(mesh_sizes) * 0.5)
    gmsh.option.setNumber("Mesh.MeshSizeMax", max(mesh_sizes) * 1.5)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)


# ---------------------------------------------------------------------------
# Physical group assignment (ABEC convention)
# ---------------------------------------------------------------------------

def _apply_symmetry_cut_yz(surface_groups: Dict[str, List[int]]) -> None:
    """Cut all model surfaces at the YZ plane (X=0), keeping X >= 0.

    Uses OCC BooleanFragment to split surfaces at the cutting plane,
    then removes entities whose centre-of-mass is at X < 0.  The
    ``surface_groups`` dict is updated in-place so that downstream
    physical-group assignment references the correct post-cut tags.

    This implements the *tessellation-last* principle: geometry is
    modified at the B-Rep level BEFORE Gmsh generates the mesh.
    """
    all_surface_dimtags = gmsh.model.getEntities(2)
    if not all_surface_dimtags:
        return

    # Build a large planar surface at X=0 (in the YZ plane) to use as
    # the cutting tool.  We construct it from 4 points because
    # addRectangle always creates in the XY plane.
    bb = gmsh.model.getBoundingBox(-1, -1)  # xmin,ymin,zmin,xmax,ymax,zmax
    pad = max(abs(bb[3] - bb[0]), abs(bb[4] - bb[1]), abs(bb[5] - bb[2])) * 2
    y_lo, y_hi = bb[1] - pad, bb[4] + pad
    z_lo, z_hi = bb[2] - pad, bb[5] + pad
    p1 = gmsh.model.occ.addPoint(0, y_lo, z_lo)
    p2 = gmsh.model.occ.addPoint(0, y_hi, z_lo)
    p3 = gmsh.model.occ.addPoint(0, y_hi, z_hi)
    p4 = gmsh.model.occ.addPoint(0, y_lo, z_hi)
    l1 = gmsh.model.occ.addLine(p1, p2)
    l2 = gmsh.model.occ.addLine(p2, p3)
    l3 = gmsh.model.occ.addLine(p3, p4)
    l4 = gmsh.model.occ.addLine(p4, p1)
    cl = gmsh.model.occ.addCurveLoop([l1, l2, l3, l4])
    cut_rect = gmsh.model.occ.addPlaneSurface([cl])

    # Synchronize the OCC model before fragmenting. The surfaces may not be
    # registered in the OCC kernel yet, which can cause "Unknown surface" errors.
    gmsh.model.occ.synchronize()

    # Fragment all existing surfaces with the cutting rectangle.
    # This splits any surface that crosses X=0 into two pieces.
    object_dimtags = list(all_surface_dimtags)
    tool_dimtags = [(2, cut_rect)]
    try:
        out_dimtags, out_map = gmsh.model.occ.fragment(
            object_dimtags, tool_dimtags
        )
    except Exception as exc:
        logger.warning(
            "[MWG] Symmetry cut fragment failed: %s — skipping cut.", exc
        )
        # Clean up the rectangle and bail
        try:
            gmsh.model.occ.remove(tool_dimtags, recursive=True)
        except Exception:
            pass
        gmsh.model.occ.synchronize()
        return

    gmsh.model.occ.synchronize()

    # Build a mapping from old surface tags → new (post-fragment) tags.
    # out_map[i] lists the output entities that correspond to object_dimtags[i].
    # out_map has len(object_dimtags) + len(tool_dimtags) entries.
    old_to_new: Dict[int, List[int]] = {}
    for i, obj_dt in enumerate(object_dimtags):
        old_tag = obj_dt[1]
        new_tags = [t for d, t in out_map[i] if d == 2]
        old_to_new[old_tag] = new_tags

    # Identify surfaces that came from the cutting tool (rectangle).
    # These are in out_map[len(object_dimtags):] and must always be removed.
    tool_surface_tags: set[int] = set()
    for i in range(len(object_dimtags), len(out_map)):
        for d, t in out_map[i]:
            if d == 2:
                tool_surface_tags.add(t)
    # Tool fragments that also appear as object fragments are shared topology
    # (e.g. where the rectangle overlaps an existing surface).  Only remove
    # tags that are EXCLUSIVELY from the tool.
    object_surface_tags: set[int] = set()
    for i in range(len(object_dimtags)):
        for d, t in out_map[i]:
            if d == 2:
                object_surface_tags.add(t)
    pure_tool_tags = tool_surface_tags - object_surface_tags

    # Classify post-fragment surfaces: remove X < 0 AND pure tool surfaces.
    all_post_surfaces = gmsh.model.getEntities(2)
    to_remove = []
    for dim, tag in all_post_surfaces:
        if tag in pure_tool_tags:
            to_remove.append((dim, tag))
            continue
        try:
            com = gmsh.model.occ.getCenterOfMass(dim, tag)
            if com[0] < -1e-8:
                to_remove.append((dim, tag))
        except Exception:
            pass  # Entity may have been consumed by fragment

    if to_remove:
        gmsh.model.occ.remove(to_remove, recursive=True)
        gmsh.model.occ.synchronize()

    removed_tags = {t for _, t in to_remove}

    # Update surface_groups in-place: replace old tags with surviving new tags.
    surviving = {t for _, t in gmsh.model.getEntities(2)}
    for group_name in list(surface_groups.keys()):
        old_tags = surface_groups[group_name]
        new_tags = []
        for ot in old_tags:
            if ot in old_to_new:
                # This tag was fragmented → use its children that survived
                new_tags.extend(t for t in old_to_new[ot] if t in surviving)
            elif ot in surviving:
                # Tag wasn't involved in fragment but still exists
                new_tags.append(ot)
            # else: tag no longer exists (removed or consumed)
        surface_groups[group_name] = new_tags

    logger.info(
        "[MWG] Symmetry cut: removed %d surfaces (%d X<0, %d tool), %d surviving.",
        len(to_remove), len(to_remove) - len(pure_tool_tags & removed_tags),
        len(pure_tool_tags & removed_tags), len(surviving),
    )


def _assign_physical_groups(
    surface_groups: Dict[str, List[int]],
) -> None:
    """Assign ABEC-compatible physical group names to meshed surfaces.

    Tag contract (matching reference mesh convention):
        SD1G0     (1) - all rigid wall surfaces: inner horn + enclosure/outer shell +
                        rear disc + mouth rim (everything except source disc)
        SD1D1001  (2) - throat source disc (driving element)

    The outer wall shell, rear disc, and mouth rim are part of SD1G0 in
    free-standing wall mode — same as the reference ABEC/ATH mesh convention.
    Enclosure surfaces also share SD1G0 so all rigid boundaries use one wall tag.
    """
    # All rigid wall surfaces share tag 1 (SD1G0): inner horn + enclosure/outer shell +
    # rear disc + mouth rim.
    wall_tags = (
        surface_groups.get("inner", [])
        + surface_groups.get("enclosure", [])
        + surface_groups.get("outer", [])
        + surface_groups.get("rear", [])
        + surface_groups.get("mouth", [])
    )
    if wall_tags:
        gmsh.model.addPhysicalGroup(2, wall_tags, tag=1)
        gmsh.model.setPhysicalName(2, 1, "SD1G0")

    disc_tags = surface_groups.get("throat_disc", [])
    if disc_tags:
        gmsh.model.addPhysicalGroup(2, disc_tags, tag=2)
        gmsh.model.setPhysicalName(2, 2, "SD1D1001")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_waveguide_mesh(
    params: dict,
    *,
    include_canonical: bool = False,
    cancellation_callback: Optional[Callable[[], None]] = None,
    symmetry_cut: Optional[str] = None,
) -> dict:
    """Build a .msh from ATH parameters using Gmsh OCC Python API.

    Accepts both R-OSSE and OSSE formula types. See WaveguideParamsRequest
    in server/app.py for the full parameter schema.

    Args:
        params: Waveguide parameter dictionary.
        include_canonical: If True, extract canonical mesh arrays.
        cancellation_callback: Optional cancellation check.
        symmetry_cut: If set, cut the B-Rep geometry at a symmetry plane
            BEFORE tessellation (tessellation-last principle).  Values:
            ``"yz"`` — cut at X=0, keep X >= 0 (half model).
            ``None`` — no cut (default).

    Returns:
        {
            "msh_text": str,    Gmsh .msh file content
            "stats": {nodeCount, elementCount},
            "canonical_mesh": {vertices, indices, surfaceTags} (optional)
        }

    Raises:
        GmshMeshingError on any Gmsh failure.
        ValueError on invalid parameter values.
        RuntimeError if gmsh Python API is not available.
    """
    if not GMSH_AVAILABLE:
        raise RuntimeError(
            "Gmsh Python API is not available. Install gmsh: pip install 'gmsh>=4.11,<5.0'"
        )

    formula_type = params.get("formula_type", "R-OSSE")
    if formula_type not in ("R-OSSE", "OSSE"):
        raise ValueError(
            f"Formula type '{formula_type}' is not supported. Use 'R-OSSE' or 'OSSE'."
        )

    enc_depth = float(params.get("enc_depth", 0) or 0)
    msh_version = str(params.get("msh_version", "2.2"))
    vertical_offset = float(params.get("vertical_offset", 0) or 0)
    # sim_type is passed through to ABEC project files but does not affect geometry
    quadrants = int(params.get("quadrants", 1234))
    closed = (quadrants == 1234)

    _run_cancellation_callback(cancellation_callback)

    # Compute 3D point grids (includes morph).
    # outer_points is non-None only for the wall-shell case (enc_depth==0 + wall_thickness>0).
    inner_points, outer_points = _compute_point_grids(params)

    throat_res = float(params.get("throat_res", 5.0))
    mouth_res = float(params.get("mouth_res", 8.0))
    rear_res = float(params.get("rear_res", 25.0))
    enc_front_resolution = params.get("enc_front_resolution")
    enc_back_resolution = params.get("enc_back_resolution")

    with gmsh_lock:
        initialized_here = False
        try:
            if not gmsh.isInitialized():
                gmsh.initialize()
                initialized_here = True

            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("Geometry.Tolerance", 1e-8)
            gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-8)
            gmsh.clear()
            gmsh.model.add("WaveguideOCC")

            # --- Build geometry ---
            enc_data: Dict[str, Any] = {
                "dimtags": [],
                "front": [],
                "back": [],
                "sides": [],
                "edges": [],
                "front_edges": [],
                "back_edges": [],
                "bounds": None,
                "opening_curves": [],
                "opening_ring_points": None,
            }
            inner_dimtags = _build_surface_from_points(inner_points, closed=closed)

            # Synchronize so we can query boundary curves from inner surfaces.
            gmsh.model.occ.synchronize()

            throat_disc_dimtags = _build_throat_disc_from_inner_boundary(inner_dimtags, closed=closed)
            if not throat_disc_dimtags and closed:
                # Fallback to the legacy ring-based source disc if boundary extraction fails.
                throat_disc_dimtags = _build_throat_disc_from_ring(inner_points[:, 0, :], closed=closed)

            if enc_depth > 0:
                enc_data = _build_enclosure_box(
                    inner_points,
                    params,
                    closed=closed,
                    inner_dimtags=inner_dimtags,
                )

            outer_dimtags = []
            mouth_dimtags = []
            rear_dimtags: List[Tuple[int, int]] = []
            rear_cap_dimtags: List[Tuple[int, int]] = []
            throat_return_dimtags: List[Tuple[int, int]] = []
            if enc_depth == 0 and outer_points is not None:
                wall_thickness = float(params.get("wall_thickness", 6.0))

                # Outer shell BSpline surface (two half-patches, internally fragmented).
                outer_dimtags = _build_surface_from_points(outer_points, closed=closed)

                # Synchronize so boundary curves of both inner and outer surfaces are
                # queryable — needed to build a mouth rim that is topologically shared
                # with both inner and outer surfaces.
                gmsh.model.occ.synchronize()

                # Build mouth rim using actual OCC boundary curves from inner + outer horn.
                # This guarantees that the mouth rim shares the same curve entities as both
                # surfaces, so the resulting mesh is topologically connected at the mouth.
                mouth_dimtags = _build_mouth_rim_from_boundaries(inner_dimtags, outer_dimtags)
                if not mouth_dimtags:
                    # Fallback: build from control points (will rely on removeDuplicateNodes).
                    mouth_dimtags = _build_mouth_rim(inner_points, outer_points, closed=closed)

                # Rear disc assembly: axial step face + flat disc at z_rear.
                # Mirrors JS freestandingWall.js (outer shell throat strip + addRearThroatDisc).
                # Important: we intentionally omit any inner-throat -> outer-throat rear
                # annular connector here. This keeps a hollow throat cavity in thickened
                # wall mode so the source disc remains connected only to the inner horn,
                # not directly to shell/rear-closure surfaces.
                rear_shell_dimtags = _build_rear_disc_assembly(
                    outer_points,
                    wall_thickness,
                    closed=closed,
                    outer_dimtags=outer_dimtags,
                )

                # Keep wall surfaces as-authored. OCC boolean fragment on this
                # assembly can introduce tiny self-intersections in thickened OSSE
                # builds, which then fails meshing with:
                # "The 1D mesh seems not to be forming a closed loop."
                # Mouth rims are already built from actual boundary curves when
                # possible; any remaining coincident seams are merged by
                # mesh.removeDuplicateNodes() after surface meshing.
                rear_dimtags = [dt for dt in rear_shell_dimtags if dt[0] == 2]
                if rear_dimtags:
                    throat_return_dimtags = rear_dimtags[:-1]
                    rear_cap_dimtags = rear_dimtags[-1:]

            # Final synchronize flushes all fragmented entities.
            # Safe to call after enclosure's internal synchronize — it is idempotent.
            gmsh.model.occ.synchronize()

            # --- Surface groups ---
            surface_groups: Dict[str, List[int]] = {
                "inner": [tag for _, tag in inner_dimtags],
            }
            if throat_disc_dimtags:
                surface_groups["throat_disc"] = [tag for _, tag in throat_disc_dimtags]
            if enc_depth > 0:
                surface_groups["enclosure"] = [tag for _, tag in enc_data.get("dimtags", [])]
                surface_groups["enclosure_front"] = list(enc_data.get("front", []))
                surface_groups["enclosure_back"] = list(enc_data.get("back", []))
                surface_groups["enclosure_sides"] = list(enc_data.get("sides", []))
                surface_groups["enclosure_edges"] = list(enc_data.get("edges", []))
                surface_groups["enclosure_edges_front"] = list(enc_data.get("front_edges", []))
                surface_groups["enclosure_edges_back"] = list(enc_data.get("back_edges", []))
            elif outer_points is not None:
                # After fragment, per-group lists track their own post-fragment tags.
                surface_groups["outer"] = [tag for dim, tag in outer_dimtags if dim == 2]
                surface_groups["rear"] = [tag for dim, tag in rear_dimtags if dim == 2]
                surface_groups["throat_return"] = [
                    tag for dim, tag in throat_return_dimtags if dim == 2
                ]
                surface_groups["rear_cap"] = [tag for dim, tag in rear_cap_dimtags if dim == 2]
                surface_groups["mouth"] = [tag for dim, tag in mouth_dimtags if dim == 2]

            # --- Validate surface tags survived synchronize ---
            all_model_surfaces = {tag for _, tag in gmsh.model.getEntities(2)}
            for group_name, tags in surface_groups.items():
                missing = [t for t in tags if t not in all_model_surfaces]
                if missing:
                    logger.warning(
                        "[MWG] surface_groups['%s'] has invalid tags %s after occ.synchronize()",
                        group_name, missing,
                    )

            # --- Symmetry cut (tessellation-last: cut B-Rep BEFORE meshing) ---
            # Must run BEFORE _configure_mesh_size because fragment() replaces
            # surface/curve entity tags. Mesh size Restrict fields referencing
            # pre-cut tags would cause "Unknown surface" errors during meshing.
            if symmetry_cut == "yz":
                _apply_symmetry_cut_yz(surface_groups)

            # --- Mesh size fields ---
            _run_cancellation_callback(cancellation_callback)
            _configure_mesh_size(
                inner_points,
                surface_groups,
                throat_res,
                mouth_res,
                rear_res,
                enc_front_resolution=enc_front_resolution,
                enc_back_resolution=enc_back_resolution,
                enclosure_bounds=enc_data.get("bounds"),
            )

            # --- Physical groups (ABEC-compatible) ---
            _assign_physical_groups(surface_groups)

            # --- Mesh algorithm (MeshAdapt handles complex curved surfaces well) ---
            gmsh.option.setNumber("Mesh.Algorithm", 1)
            gmsh.option.setNumber("Mesh.AngleToleranceFacetOverlap", 0.5)
            gmsh.option.setNumber("Mesh.MshFileVersion", float(msh_version))

            # --- Generate mesh ---
            _run_cancellation_callback(cancellation_callback)
            gmsh.model.mesh.generate(2)
            gmsh.model.mesh.removeDuplicateNodes()
            surface_identity_by_entity = _build_surface_identity_by_entity(
                surface_groups,
                has_enclosure=enc_depth > 0,
            )
            canonical_mesh = (
                _extract_canonical_mesh_from_model(
                    surface_identity_by_entity=surface_identity_by_entity,
                )
                if include_canonical
                else None
            )

            if canonical_mesh is not None:
                canonical_mesh = _orient_and_validate_canonical_mesh(
                    canonical_mesh,
                    require_watertight=closed and not symmetry_cut,
                    require_single_boundary_loop=False,
                )
                flipped_tags = canonical_mesh.get("flippedElementTags", [])
                if flipped_tags:
                    gmsh.model.mesh.reverseElements(flipped_tags)
                canonical_mesh["metadata"] = {
                    "identityTriangleCounts": _count_triangle_identities(
                        list(canonical_mesh.get("triangleIdentities", []))
                    ),
                    "verticalOffset": vertical_offset,
                }
                canonical_mesh.pop("triangleIdentities", None)

            # Trust Gmsh to produce valid, consistently-oriented mesh
            if canonical_mesh is not None:
                tri_count = len(canonical_mesh["indices"]) // 3
                if len(canonical_mesh["surfaceTags"]) != tri_count:
                    raise GmshMeshingError(
                        "Canonical extraction surface tag count does not match triangle count."
                    )
                if tri_count > 0 and not any(tag == 2 for tag in canonical_mesh["surfaceTags"]):
                    raise GmshMeshingError("Canonical extraction produced no source-tagged triangles.")

            # --- Write outputs ---
            _run_cancellation_callback(cancellation_callback)
            with tempfile.TemporaryDirectory(prefix="mwg-occ-") as tmp_dir:
                tmp = Path(tmp_dir)
                msh_path = tmp / "output.msh"
                stl_path = tmp / "output.stl"

                gmsh.write(str(msh_path))
                gmsh.write(str(stl_path))

                if not msh_path.exists():
                    raise GmshMeshingError("Gmsh OCC builder did not produce a .msh file.")

                msh_text = msh_path.read_text(encoding="utf-8", errors="replace")
                stl_text = stl_path.read_text(encoding="utf-8", errors="replace") if stl_path.exists() else None

            stats = parse_msh_stats(msh_text)
            result = {
                "msh_text": msh_text,
                "stl_text": stl_text,
                "stats": stats,
            }
            if canonical_mesh is not None:
                result["canonical_mesh"] = canonical_mesh
            return result

        except (GmshMeshingError, ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise GmshMeshingError(f"Gmsh OCC build failed: {exc}") from exc
        finally:
            if initialized_here and gmsh.isInitialized():
                gmsh.finalize()

"""
Enclosure geometry helpers for the waveguide mesh builder.

Extracted from waveguide_builder.py — builds the viewport-equivalent rectangular
enclosure box (front baffle, side walls, rear cap, edge roundovers/chamfers) and
provides resolution-formula helpers for mesh sizing.

All functions preserve their original names and signatures.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from .deps import gmsh

logger = logging.getLogger(__name__)


def _parse_number_list(text) -> List[float]:
    """Parse comma-separated number list (duplicated from waveguide_builder)."""
    if not text or not str(text).strip():
        return []
    try:
        return [float(p.strip()) for p in str(text).split(",") if p.strip()]
    except ValueError:
        return []


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _parse_quadrant_resolutions(value: Optional[str], fallback: float) -> List[float]:
    """Parse per-quadrant resolution list q1..q4 with scalar broadcast support."""
    fallback = float(fallback)
    if value is None:
        return [fallback, fallback, fallback, fallback]

    text = str(value).strip()
    if not text:
        return [fallback, fallback, fallback, fallback]

    try:
        scalar = float(text)
    except ValueError:
        scalar = float("nan")
    if math.isfinite(scalar) and scalar > 0:
        return [scalar, scalar, scalar, scalar]

    parts = _parse_number_list(text)
    if not parts:
        return [fallback, fallback, fallback, fallback]

    out: List[float] = []
    for i in range(4):
        if i < len(parts) and math.isfinite(parts[i]) and parts[i] > 0:
            out.append(float(parts[i]))
        else:
            out.append(fallback)
    return out


def _axial_interpolated_size(
    z: float, z_throat: float, z_mouth: float, throat_res: float, mouth_res: float
) -> float:
    span = max(abs(z_mouth - z_throat), 1e-6)
    t = (z - z_throat) / span
    t = max(0.0, min(1.0, t))
    return float(throat_res + (mouth_res - throat_res) * t)


def _rear_resolution_active(enc_depth: float, wall_thickness: float) -> bool:
    return float(enc_depth) <= 0.0 and float(wall_thickness) > 0.0


def _panel_corner_points_by_quadrant(
    bx0: float, bx1: float, by0: float, by1: float, z_plane: float
) -> List[Tuple[float, float, float]]:
    # Quadrant order: Q1(+x,+y), Q2(-x,+y), Q3(-x,-y), Q4(+x,-y)
    return [
        (bx1, by1, z_plane),
        (bx0, by1, z_plane),
        (bx0, by0, z_plane),
        (bx1, by0, z_plane),
    ]


def _panel_bilinear_resolution_formula(
    q_values: List[float],
    *,
    bx0: float,
    bx1: float,
    by0: float,
    by1: float,
) -> str:
    """Return MathEval formula for bilinear corner interpolation over panel x/y."""
    dx = max(abs(bx1 - bx0), 1e-6)
    dy = max(abs(by1 - by0), 1e-6)
    u = f"((x - ({bx0:.12g})) / ({dx:.12g}))"
    v = f"((y - ({by0:.12g})) / ({dy:.12g}))"

    q1 = float(q_values[0])  # (+x,+y)
    q2 = float(q_values[1])  # (-x,+y)
    q3 = float(q_values[2])  # (-x,-y)
    q4 = float(q_values[3])  # (+x,-y)

    return (
        f"({q3:.12g})*(1-({u}))*(1-({v})) + "
        f"({q4:.12g})*({u})*(1-({v})) + "
        f"({q2:.12g})*(1-({u}))*({v}) + "
        f"({q1:.12g})*({u})*({v})"
    )


def _enclosure_resolution_formula(
    front_q: List[float],
    back_q: List[float],
    *,
    bx0: float,
    bx1: float,
    by0: float,
    by1: float,
    z_front: float,
    z_back: float,
) -> str:
    """Return MathEval formula for continuous enclosure front/back interpolation."""
    dz = max(abs(z_front - z_back), 1e-6)
    t = f"(({z_front:.12g}) - z) / ({dz:.12g})"
    front_expr = _panel_bilinear_resolution_formula(
        front_q, bx0=bx0, bx1=bx1, by0=by0, by1=by1
    )
    back_expr = _panel_bilinear_resolution_formula(
        back_q, bx0=bx0, bx1=bx1, by0=by0, by1=by1
    )
    return f"(({front_expr})*(1-({t})) + ({back_expr})*({t}))"


# ---------------------------------------------------------------------------
# Surface / curve classification helpers
# ---------------------------------------------------------------------------

def _classify_enclosure_surfaces(
    dimtags: List[Tuple[int, int]], z_front: float, z_back: float
) -> Dict[str, List[int]]:
    """Split enclosure surfaces into front/back/side groups by z-plane bounding boxes."""
    front: List[int] = []
    back: List[int] = []
    sides: List[int] = []
    eps = max(1e-6, abs(z_front - z_back) * 1e-3)

    for dim, tag in dimtags:
        if dim != 2:
            continue
        _, _, z0, _, _, z1 = gmsh.model.getBoundingBox(dim, tag)
        if abs(z0 - z_front) <= eps and abs(z1 - z_front) <= eps:
            front.append(tag)
        elif abs(z0 - z_back) <= eps and abs(z1 - z_back) <= eps:
            back.append(tag)
        else:
            sides.append(tag)

    return {
        "front": front,
        "back": back,
        "sides": sides,
    }


def _collect_boundary_curves(surface_tags: List[int]) -> List[int]:
    """Return unique boundary curve tags for the given surface tags."""
    if len(surface_tags) == 0:
        return []

    ordered: List[int] = []
    seen: Set[int] = set()
    for surface_tag in surface_tags:
        boundary_dimtags = gmsh.model.getBoundary(
            [(2, int(surface_tag))], oriented=False, combined=False
        )
        for dim, curve_tag in boundary_dimtags:
            if dim != 1:
                continue
            curve_tag_i = int(curve_tag)
            if curve_tag_i not in seen:
                seen.add(curve_tag_i)
                ordered.append(curve_tag_i)
    return ordered


# ---------------------------------------------------------------------------
# Ray / rounded-box intersection
# ---------------------------------------------------------------------------

def _intersect_ray_with_rounded_box(
    *,
    angle: float,
    cx: float,
    cy: float,
    bx0: float,
    bx1: float,
    by0: float,
    by1: float,
    corner_radius: float,
    edge_type: int,
) -> Tuple[float, float, float, float]:
    """Viewport-equivalent ray intersection against rounded/chamfered rectangle."""
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    eps = 1e-12

    best_t = float("inf")
    hit_x = cx + cos_a
    hit_y = cy + sin_a
    hit_nx = cos_a
    hit_ny = sin_a

    def try_segment(x1: float, y1: float, x2: float, y2: float, nx: float, ny: float) -> None:
        nonlocal best_t, hit_x, hit_y, hit_nx, hit_ny
        ex = x2 - x1
        ey = y2 - y1
        det = cos_a * (-ey) - sin_a * (-ex)
        if abs(det) <= eps:
            return

        rhs_x = x1 - cx
        rhs_y = y1 - cy
        t = (rhs_x * (-ey) - rhs_y * (-ex)) / det
        u = (cos_a * rhs_y - sin_a * rhs_x) / det
        if t > eps and -eps <= u <= 1.0 + eps and t < best_t:
            best_t = t
            hit_x = cx + cos_a * t
            hit_y = cy + sin_a * t
            hit_nx = nx
            hit_ny = ny

    def try_arc(acx: float, acy: float, r: float, start_angle: float, end_angle: float) -> None:
        nonlocal best_t, hit_x, hit_y, hit_nx, hit_ny
        ox = cx - acx
        oy = cy - acy
        b = 2.0 * (ox * cos_a + oy * sin_a)
        c = ox * ox + oy * oy - r * r
        disc = b * b - 4.0 * c
        if disc < 0.0:
            return
        sqrt_disc = math.sqrt(disc)
        for t in ((-b - sqrt_disc) / 2.0, (-b + sqrt_disc) / 2.0):
            if t <= eps or t >= best_t:
                continue
            px = cx + cos_a * t
            py = cy + sin_a * t
            pa = math.atan2(py - acy, px - acx)
            swept = end_angle - start_angle
            rel = pa - start_angle
            while rel < -eps:
                rel += 2.0 * math.pi
            while rel > 2.0 * math.pi + eps:
                rel -= 2.0 * math.pi
            if rel <= swept + eps:
                best_t = t
                hit_x = px
                hit_y = py
                dx = px - acx
                dy = py - acy
                nlen = math.hypot(dx, dy)
                if nlen > 0.0:
                    hit_nx = dx / nlen
                    hit_ny = dy / nlen

    def try_chamfer(acx: float, acy: float, r: float, start_angle: float, end_angle: float) -> None:
        x1 = acx + r * math.cos(start_angle)
        y1 = acy + r * math.sin(start_angle)
        x2 = acx + r * math.cos(end_angle)
        y2 = acy + r * math.sin(end_angle)
        mid = 0.5 * (start_angle + end_angle)
        try_segment(x1, y1, x2, y2, math.cos(mid), math.sin(mid))

    half_w = 0.5 * (bx1 - bx0)
    half_h = 0.5 * (by1 - by0)
    box_cx = 0.5 * (bx1 + bx0)
    box_cy = 0.5 * (by1 + by0)
    r = max(0.0, min(float(corner_radius), half_w - 0.1, half_h - 0.1))
    use_corners = r > 1e-3

    try_segment(
        bx1,
        box_cy - half_h + (r if use_corners else 0.0),
        bx1,
        box_cy + half_h - (r if use_corners else 0.0),
        1.0,
        0.0,
    )
    try_segment(
        box_cx + half_w - (r if use_corners else 0.0),
        by1,
        box_cx - half_w + (r if use_corners else 0.0),
        by1,
        0.0,
        1.0,
    )
    try_segment(
        bx0,
        box_cy + half_h - (r if use_corners else 0.0),
        bx0,
        box_cy - half_h + (r if use_corners else 0.0),
        -1.0,
        0.0,
    )
    try_segment(
        box_cx - half_w + (r if use_corners else 0.0),
        by0,
        box_cx + half_w - (r if use_corners else 0.0),
        by0,
        0.0,
        -1.0,
    )

    if use_corners:
        corners = [
            (box_cx + half_w - r, box_cy - half_h + r, -math.pi / 2.0, 0.0),
            (box_cx + half_w - r, box_cy + half_h - r, 0.0, math.pi / 2.0),
            (box_cx - half_w + r, box_cy + half_h - r, math.pi / 2.0, math.pi),
            (box_cx - half_w + r, box_cy - half_h + r, math.pi, 1.5 * math.pi),
        ]
        for acx, acy, start_angle, end_angle in corners:
            if int(edge_type) == 2:
                try_chamfer(acx, acy, r, start_angle, end_angle)
            else:
                try_arc(acx, acy, r, start_angle, end_angle)

    return hit_x, hit_y, hit_nx, hit_ny


# ---------------------------------------------------------------------------
# Enclosure point generation
# ---------------------------------------------------------------------------

def _generate_enclosure_points_from_angles(
    *,
    angles: List[float],
    cx: float,
    cy: float,
    bx0: float,
    bx1: float,
    by0: float,
    by1: float,
    edge_radius: float,
    edge_type: int,
) -> Tuple[List[Tuple[float, float, float, float]], List[Tuple[float, float, float, float]], float]:
    """Generate outer/inset enclosure loops aligned to horn mouth angular sampling."""
    half_w = 0.5 * (bx1 - bx0)
    half_h = 0.5 * (by1 - by0)
    clamped_edge = max(0.0, min(float(edge_radius), half_w - 0.1, half_h - 0.1))

    outer_pts: List[Tuple[float, float, float, float]] = []
    inset_pts: List[Tuple[float, float, float, float]] = []
    for angle in angles:
        hx, hy, nx, ny = _intersect_ray_with_rounded_box(
            angle=float(angle),
            cx=float(cx),
            cy=float(cy),
            bx0=float(bx0),
            bx1=float(bx1),
            by0=float(by0),
            by1=float(by1),
            corner_radius=float(clamped_edge),
            edge_type=int(edge_type),
        )
        outer_pts.append((hx, hy, nx, ny))
        inset_pts.append(
            (
                hx - nx * clamped_edge,
                hy - ny * clamped_edge,
                nx,
                ny,
            )
        )
    return outer_pts, inset_pts, clamped_edge

# This is a part of the front baffle build process for closed (full-circle) horns.  The "enclosure box" is a viewport-aligned rectangular box that fully contains the horn mouth and extends back along the z-axis by enc_depth.
def _ring_points_from_xy_plan(
    plan_pts: List[Tuple[float, float, float, float]],
    *,
    z: float,
) -> np.ndarray:
    out = np.empty((len(plan_pts), 3), dtype=float)
    for i, (x, y, _, _) in enumerate(plan_pts):
        out[i, 0] = float(x)
        out[i, 1] = float(y)
        out[i, 2] = float(z)
    return out


def _add_curve_loop_from_curves(curve_tags: List[int]) -> int:
    try:
        return int(gmsh.model.occ.addCurveLoop([int(c) for c in curve_tags], reorient=True))
    except TypeError:
        return int(gmsh.model.occ.addCurveLoop([int(c) for c in curve_tags]))


def _add_ruled_section(loop_a: int, loop_b: int) -> List[Tuple[int, int]]:
    return list(
        gmsh.model.occ.addThruSections(
            [int(loop_a), int(loop_b)],
            makeSolid=False,
            makeRuled=True,
        )
    )


# ---------------------------------------------------------------------------
# Main enclosure builder
# ---------------------------------------------------------------------------

def _build_enclosure_box(
    inner_points: np.ndarray,
    params: dict,
    closed: bool,
    *,
    inner_dimtags: Optional[List[Tuple[int, int]]] = None,
) -> Dict[str, Any]:
    """Build viewport-equivalent enclosure surfaces and classify front/back/sides."""
    # Deferred import to avoid circular dependency with waveguide_builder.
    from .waveguide_builder import _boundary_curves_at_z_extreme, _make_wire

    empty = {
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
    if not inner_dimtags:
        return empty

    enc_depth = float(params.get("enc_depth", 0) or 0)
    if enc_depth <= 0:
        return empty

    enc_space_l = float(params.get("enc_space_l", 25.0) or 25.0)
    enc_space_t = float(params.get("enc_space_t", 25.0) or 25.0)
    enc_space_r = float(params.get("enc_space_r", 25.0) or 25.0)
    enc_space_b = float(params.get("enc_space_b", 25.0) or 25.0)

    mouth_pts = inner_points[:, -1, :]  # (n_phi, 3)
    x_min = float(mouth_pts[:, 0].min())
    x_max = float(mouth_pts[:, 0].max())
    y_min = float(mouth_pts[:, 1].min())
    y_max = float(mouth_pts[:, 1].max())
    z_front = float(mouth_pts[:, 2].max())

    # Clamp enc_depth so the back wall never intersects the inner horn tube.
    # The horn extends from throat (inner_points[:, 0, :]) to mouth (z_front).
    # If enc_depth < horn_length, the back wall cuts through the horn surface,
    # creating self-intersecting geometry that produces wrong BEM results.
    z_throat = float(np.min(inner_points[:, 0, 2]))
    horn_length = z_front - z_throat
    clearance_mm = 1.0
    min_enc_depth = horn_length + clearance_mm
    if enc_depth < min_enc_depth:
        logger.warning(
            "[MWG] enc_depth (%.1f mm) < horn length (%.1f mm); "
            "clamping to %.1f mm to prevent back wall / horn intersection.",
            enc_depth, horn_length, min_enc_depth,
        )
        enc_depth = min_enc_depth

    z_back = z_front - enc_depth

    bx0 = x_min - enc_space_l
    bx1 = x_max + enc_space_r
    by0 = y_min - enc_space_b
    by1 = y_max + enc_space_t

    bounds = {
        "bx0": bx0,
        "bx1": bx1,
        "by0": by0,
        "by1": by1,
        "z_front": z_front,
        "z_back": z_back,
        "cx": 0.5 * (bx0 + bx1),
        "cy": 0.5 * (by0 + by1),
    }

    enc_edge = float(params.get("enc_edge", 0) or 0)
    enc_edge_type = int(params.get("enc_edge_type", 1) or 1)
    corner_segments = int(params.get("corner_segments", 4) or 4)
    axial_segs = max(4, corner_segments) if enc_edge > 0 else 1

    mouth_curves = _boundary_curves_at_z_extreme(inner_dimtags, want_min_z=False)
    if not mouth_curves:
        return empty

    cx = float(np.mean(mouth_pts[:, 0]))
    cy = float(np.mean(mouth_pts[:, 1]))
    angles = [
        float(math.atan2(float(mouth_pts[i, 1]) - cy, float(mouth_pts[i, 0]) - cx))
        for i in range(mouth_pts.shape[0])
    ]

    outer_pts, inset_pts, clamped_edge = _generate_enclosure_points_from_angles(
        angles=angles,
        cx=cx,
        cy=cy,
        bx0=bx0,
        bx1=bx1,
        by0=by0,
        by1=by1,
        edge_radius=enc_edge,
        edge_type=enc_edge_type,
    )
    if not outer_pts or not inset_pts:
        return empty

    generated_dimtags: List[Tuple[int, int]] = []
    edge_dimtags: List[Tuple[int, int]] = []
    front_edge_dimtags_all: List[Tuple[int, int]] = []
    back_edge_dimtags_all: List[Tuple[int, int]] = []
    edge_depth = min(clamped_edge, max(0.0, enc_depth * 0.49))

    if closed:
        mouth_loop = _add_curve_loop_from_curves(mouth_curves)
    else:
        # Cut the front baffle hole: adding the straight symmetry line to close the loop.
        # Use existing boundary points from the mouth_curves chain to ensure topological connectivity.
        try:
            boundary_pts = gmsh.model.getBoundary([(1, t) for t in mouth_curves], oriented=False, combined=True)
            pt_tags = [int(abs(t)) for d, t in boundary_pts if d == 0]
            if len(pt_tags) >= 2:
                # Chain has two end points.
                line_tag = gmsh.model.occ.addLine(pt_tags[0], pt_tags[1])
                mouth_loop = _add_curve_loop_from_curves(mouth_curves + [int(line_tag)])
            else:
                mouth_loop = _add_curve_loop_from_curves(mouth_curves)
        except Exception:
            mouth_loop = _add_curve_loop_from_curves(mouth_curves)

    current_profile, mouth_curves_list, profile_pts = _make_wire(mouth_pts, closed=closed)

    merge_eps = 1e-6
    reuse_mouth_as_ring0 = True
    for i in range(mouth_pts.shape[0]):
        if math.hypot(
            inset_pts[i][0] - float(mouth_pts[i, 0]),
            inset_pts[i][1] - float(mouth_pts[i, 1]),
        ) > merge_eps:
            reuse_mouth_as_ring0 = False
            break

    ring0_wire: Optional[int] = None
    ring0_loop: Optional[int] = None

    def _close_wire_for_surface(orig_curves: List[int], first_pt: int, last_pt: int) -> int:
        if closed:
            return int(gmsh.model.occ.addCurveLoop([int(c) for c in orig_curves]))
        # Explicitly connect the last point back to the first point with a straight line
        # using the existing topological points used by the bspline
        line_tag = gmsh.model.occ.addLine(last_pt, first_pt)
        return int(gmsh.model.occ.addCurveLoop([int(c) for c in orig_curves] + [int(line_tag)]))

    if not reuse_mouth_as_ring0:
        # Build front baffle with inset ring (when enc_edge > 0)
        ring0_pts = _ring_points_from_xy_plan(inset_pts, z=z_front)
        ring0_wire, ring0_curves, ring0_eps = _make_wire(ring0_pts, closed=closed)
        ring0_loop = _close_wire_for_surface(ring0_curves, ring0_eps[0], ring0_eps[1])
        try:
            front_tag = int(gmsh.model.occ.addPlaneSurface([int(ring0_loop), int(mouth_loop)]))
            generated_dimtags.append((2, front_tag))
        except Exception:
            # Fallback for non-planar tolerance issues: ruled patch.
            generated_dimtags.extend(_add_ruled_section(current_profile, ring0_wire))
        current_profile = ring0_wire
    else:
        # Build front baffle without inset ring (when enc_edge == 0, mouth coincides with inset).
        # The front panel must be annular: outer enclosure boundary MINUS the horn mouth opening.
        # Using addPlaneSurface([outer_loop, mouth_loop]) cuts the mouth hole out of the front
        # panel, matching the enc_edge>0 path and ensuring the horn opening is not sealed.
        front_pts = _ring_points_from_xy_plan(outer_pts, z=z_front)
        front_wire, front_curves, front_eps = _make_wire(front_pts, closed=closed)
        front_loop = _close_wire_for_surface(front_curves, front_eps[0], front_eps[1])
        try:
            front_tag = int(gmsh.model.occ.addPlaneSurface([int(front_loop), int(mouth_loop)]))
            generated_dimtags.append((2, front_tag))
        except Exception:
            # Fallback: ruled surface from mouth to front
            generated_dimtags.extend(_add_ruled_section(current_profile, front_wire))
        # CRITICAL: advance current_profile to the outer front boundary so that the
        # side walls are built from the outer enclosure perimeter at z_front to the
        # outer enclosure perimeter at z_back — NOT from the horn mouth to the back.
        # Without this, the side wall was a cone from the small horn mouth to the
        # large outer back, leaving the true enclosure sides open and causing
        # incorrect BEM radiation results.
        current_profile = front_wire

    edge_slices = max(1, axial_segs) if edge_depth > 0.0 else 0
    for j in range(1, edge_slices + 1):
        t = float(j) / float(edge_slices)
        if enc_edge_type == 1:
            angle = t * (math.pi / 2.0)
            axial_t = 1.0 - math.cos(angle)
            radial_t = math.sin(angle)
        else:
            axial_t = t
            radial_t = t
        z_ring = z_front - axial_t * edge_depth
        ring_plan: List[Tuple[float, float, float, float]] = []
        for i in range(len(outer_pts)):
            ix, iy, nx, ny = inset_pts[i]
            ox, oy, _, _ = outer_pts[i]
            ring_plan.append(
                (
                    ix + (ox - ix) * radial_t,
                    iy + (oy - iy) * radial_t,
                    nx,
                    ny,
                )
            )
        ring_pts = _ring_points_from_xy_plan(ring_plan, z=z_ring)
        ring_wire, _, _ = _make_wire(ring_pts, closed=closed)
        front_edge_step_dimtags = _add_ruled_section(current_profile, ring_wire)
        generated_dimtags.extend(front_edge_step_dimtags)
        edge_dimtags.extend(front_edge_step_dimtags)
        front_edge_dimtags_all.extend(front_edge_step_dimtags)
        current_profile = ring_wire

    z_outer_back = z_back + edge_depth if edge_depth > 0.0 else z_back
    back_outer_pts = _ring_points_from_xy_plan(outer_pts, z=z_outer_back)
    back_outer_wire, back_outer_curves, back_outer_eps = _make_wire(back_outer_pts, closed=closed)
    generated_dimtags.extend(_add_ruled_section(current_profile, back_outer_wire))
    current_profile = back_outer_wire
    current_curves = back_outer_curves
    profile_pts = back_outer_eps

    for j in range(1, edge_slices + 1):
        t = float(j) / float(edge_slices)
        if enc_edge_type == 1:
            angle = t * (math.pi / 2.0)
            # Rear fillet: convex roll from side wall tangent to back panel tangent.
            axial_t = math.sin(angle)
            radial_t = math.cos(angle)
        else:
            axial_t = t
            radial_t = 1.0 - t
        z_ring = z_back + (1.0 - axial_t) * edge_depth
        ring_plan: List[Tuple[float, float, float, float]] = []
        for i in range(len(outer_pts)):
            ix, iy, nx, ny = inset_pts[i]
            ox, oy, _, _ = outer_pts[i]
            ring_plan.append(
                (
                    ix + (ox - ix) * radial_t,
                    iy + (oy - iy) * radial_t,
                    nx,
                    ny,
                )
            )
        ring_pts = _ring_points_from_xy_plan(ring_plan, z=z_ring)
        ring_wire, ring_curves, ring_eps = _make_wire(ring_pts, closed=closed)
        back_edge_step_dimtags = _add_ruled_section(current_profile, ring_wire)
        generated_dimtags.extend(back_edge_step_dimtags)
        edge_dimtags.extend(back_edge_step_dimtags)
        back_edge_dimtags_all.extend(back_edge_step_dimtags)
        current_profile = ring_wire
        current_curves = ring_curves
        profile_pts = ring_eps

    back_cap_loop = _close_wire_for_surface(current_curves, profile_pts[0], profile_pts[1])
    try:
        back_cap = gmsh.model.occ.addPlaneSurface([int(back_cap_loop)])
    except Exception:
        back_cap = gmsh.model.occ.addSurfaceFilling(current_profile)
    generated_dimtags.append((2, int(back_cap)))

    gmsh.model.occ.synchronize()
    dimtags = [(2, int(tag)) for dim, tag in generated_dimtags if int(dim) == 2]
    edge_tags = {int(tag) for dim, tag in edge_dimtags if int(dim) == 2}
    front_edge_tags = {int(tag) for dim, tag in front_edge_dimtags_all if int(dim) == 2}
    back_edge_tags = {int(tag) for dim, tag in back_edge_dimtags_all if int(dim) == 2}

    split = _classify_enclosure_surfaces(dimtags, z_front, z_back)
    edge_surfaces = [tag for tag in split["sides"] if int(tag) in edge_tags]
    front_edge_surfaces = [tag for tag in split["sides"] if int(tag) in front_edge_tags]
    back_edge_surfaces = [tag for tag in split["sides"] if int(tag) in back_edge_tags]
    side_surfaces = [tag for tag in split["sides"] if int(tag) not in edge_tags]
    return {
        "dimtags": dimtags,
        "front": split["front"],
        "back": split["back"],
        "sides": side_surfaces,
        "edges": edge_surfaces,
        "front_edges": front_edge_surfaces,
        "back_edges": back_edge_surfaces,
        "bounds": bounds,
        "opening_curves": [],
        "opening_ring_points": None,
    }

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


def _refine_enclosure_corners(
    outer_pts: List[Tuple[float, float, float, float]],
    inset_pts: List[Tuple[float, float, float, float]],
    *,
    cx: float,
    cy: float,
    bx0: float,
    bx1: float,
    by0: float,
    by1: float,
    clamped_edge: float,
    edge_type: int,
) -> Tuple[List[Tuple[float, float, float, float]], List[Tuple[float, float, float, float]]]:
    """Insert midpoint samples into XY-plane corner arcs for better definition.

    Each 90-degree corner arc gets a midpoint sample (at 45 degrees into the arc),
    matching the front/back roundover approach which uses a mid-arc profile.
    """
    if clamped_edge <= 1e-3 or len(outer_pts) < 3:
        return outer_pts, inset_pts

    # Corner midpoint angles: center of each 90-degree arc
    mid_angles = [
        -math.pi / 4.0,       # Q4 corner (bottom-right)
        math.pi / 4.0,        # Q1 corner (top-right)
        3.0 * math.pi / 4.0,  # Q2 corner (top-left)
        -3.0 * math.pi / 4.0, # Q3 corner (bottom-left)
    ]

    existing_angles = [math.atan2(p[1] - cy, p[0] - cx) for p in outer_pts]

    new_points: List[Tuple[float, Tuple[float, float, float, float], Tuple[float, float, float, float]]] = []
    for mid_a in mid_angles:
        min_gap = min(
            abs(((a - mid_a + math.pi) % (2.0 * math.pi)) - math.pi)
            for a in existing_angles
        )
        if min_gap < math.radians(5.0):
            continue

        hx, hy, nx, ny = _intersect_ray_with_rounded_box(
            angle=mid_a, cx=cx, cy=cy,
            bx0=bx0, bx1=bx1, by0=by0, by1=by1,
            corner_radius=clamped_edge, edge_type=edge_type,
        )
        new_points.append((
            mid_a,
            (hx, hy, nx, ny),
            (hx - nx * clamped_edge, hy - ny * clamped_edge, nx, ny),
        ))

    if not new_points:
        return outer_pts, inset_pts

    # Merge existing and new points, sorted by angle.
    # Use the first existing point as reference to handle wrap-around.
    combined: List[Tuple[float, Tuple[float, float, float, float], Tuple[float, float, float, float]]] = []
    for i, (op, ip) in enumerate(zip(outer_pts, inset_pts)):
        combined.append((existing_angles[i], op, ip))
    combined.extend(new_points)

    ref_angle = existing_angles[0]
    combined.sort(key=lambda item: (item[0] - ref_angle) % (2.0 * math.pi))

    return (
        [c[1] for c in combined],
        [c[2] for c in combined],
    )


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


def _interpolate_ring_plan(
    outer_pts: List[Tuple[float, float, float, float]],
    inset_pts: List[Tuple[float, float, float, float]],
    radial_t: float,
) -> List[Tuple[float, float, float, float]]:
    """Blend inset/outer xy-coordinates by radial_t (0=inset, 1=outer)."""
    plan: List[Tuple[float, float, float, float]] = []
    for i in range(len(outer_pts)):
        ix, iy, nx, ny = inset_pts[i]
        ox, oy, _, _ = outer_pts[i]
        plan.append((
            ix + (ox - ix) * radial_t,
            iy + (oy - iy) * radial_t,
            nx,
            ny,
        ))
    return plan


def _sample_rounded_rect(
    *, bx0: float, bx1: float, by0: float, by1: float,
    corner_radius: float, edge_type: int, z: float,
    n_per_edge: int = 3, n_per_corner: int = 4,
) -> np.ndarray:
    """Sample points on a rounded rectangle for BSpline wire construction.

    Returns an (N, 3) array of CCW-ordered points.  Uses few points — enough
    to define the shape accurately but without the excessive BSpline parametric
    density that forces Gmsh to ignore the size field on roundover surfaces.
    """
    half_w = 0.5 * (bx1 - bx0)
    half_h = 0.5 * (by1 - by0)
    R = max(0.0, min(float(corner_radius), half_w - 0.1, half_h - 0.1))
    has_corners = R > 1e-3

    pts: List[Tuple[float, float]] = []

    def add_edge(x0: float, y0: float, x1: float, y1: float) -> None:
        """Add interior points along a straight edge (endpoints added by corners)."""
        for i in range(1, n_per_edge + 1):
            t = i / (n_per_edge + 1)
            pts.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))

    def add_corner(acx: float, acy: float, start_a: float, end_a: float) -> None:
        """Add points along a corner arc (or chamfer line)."""
        for i in range(n_per_corner):
            t = i / n_per_corner
            a = start_a + t * (end_a - start_a)
            if edge_type == 2:
                # Chamfer: straight line between arc start and end.
                sx = acx + R * math.cos(start_a)
                sy = acy + R * math.sin(start_a)
                ex = acx + R * math.cos(end_a)
                ey = acy + R * math.sin(end_a)
                pts.append((sx + t * (ex - sx), sy + t * (ey - sy)))
            else:
                pts.append((acx + R * math.cos(a), acy + R * math.sin(a)))

    if has_corners:
        # CCW from bottom-right corner, starting at angle -π/2
        # Q4 corner (bottom-right)
        add_corner(bx1 - R, by0 + R, -math.pi / 2, 0.0)
        # Right edge
        add_edge(bx1, by0 + R, bx1, by1 - R)
        # Q1 corner (top-right)
        add_corner(bx1 - R, by1 - R, 0.0, math.pi / 2)
        # Top edge
        add_edge(bx1 - R, by1, bx0 + R, by1)
        # Q2 corner (top-left)
        add_corner(bx0 + R, by1 - R, math.pi / 2, math.pi)
        # Left edge
        add_edge(bx0, by1 - R, bx0, by0 + R)
        # Q3 corner (bottom-left)
        add_corner(bx0 + R, by0 + R, math.pi, 1.5 * math.pi)
        # Bottom edge
        add_edge(bx0 + R, by0, bx1 - R, by0)
    else:
        # Simple rectangle: corners only
        corners = [(bx1, by0), (bx1, by1), (bx0, by1), (bx0, by0)]
        for i in range(4):
            x0, y0 = corners[i]
            x1, y1 = corners[(i + 1) % 4]
            pts.append((x0, y0))
            add_edge(x0, y0, x1, y1)

    out = np.empty((len(pts), 3), dtype=float)
    for i, (x, y) in enumerate(pts):
        out[i, 0] = x
        out[i, 1] = y
        out[i, 2] = z
    return out


def _build_roundover_surface(
    current_wire: int,
    ring_wire_builder,
    edge_depth: float,
    z_start: float,
    direction: int,
    enc_edge_type: int,
    closed: bool,
) -> Tuple[List[Tuple[int, int]], int, List[int], Tuple[int, int]]:
    """Build a single roundover surface between the current wire and the fillet end.

    For rounded mode (enc_edge_type=1): ruled thru-sections with 3 profiles.
    For chamfer mode (enc_edge_type=2): single ruled surface with 2 profiles.

    ring_wire_builder: callable(z, radial_t) -> (wire, curves, endpoints)
        Builds a ring wire at the given z and radial blend position
        (0 = inset, 1 = outer).

    direction: +1 for front (inset->outer, z decreasing), -1 for back (outer->inset, z decreasing).
    Returns (dimtags, last_wire, last_curves, last_eps).
    """
    last_curves: List[int] = []
    last_eps: Tuple[int, int] = (0, 0)

    if enc_edge_type == 1:
        # Rounded fillet: 3 profiles (start, mid-arc at t=0.5, end) with ruled
        # sections between consecutive pairs.
        prev_wire = current_wire
        dimtags = []
        for t in (0.5, 1.0):
            angle = t * (math.pi / 2.0)
            if direction == 1:
                axial_t = 1.0 - math.cos(angle)
                radial_t = math.sin(angle)
            else:
                axial_t = math.sin(angle)
                radial_t = math.cos(angle)

            z_ring = z_start - axial_t * edge_depth if direction == 1 else z_start + (1.0 - axial_t) * edge_depth
            ring_wire, last_curves, last_eps = ring_wire_builder(z_ring, radial_t)
            dimtags.extend(_add_ruled_section(prev_wire, ring_wire))
            prev_wire = ring_wire

        last_wire = prev_wire
    else:
        # Chamfer: single ruled surface between start and end.
        z_end = z_start - edge_depth if direction == 1 else z_start
        radial_t = 1.0 if direction == 1 else 0.0
        ring_wire, last_curves, last_eps = ring_wire_builder(z_end, radial_t)
        dimtags = _add_ruled_section(current_wire, ring_wire)
        last_wire = ring_wire

    return dimtags, last_wire, last_curves, last_eps


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
    mouth_curves = _boundary_curves_at_z_extreme(inner_dimtags, want_min_z=False)
    if not mouth_curves:
        return empty

    cx = float(np.mean(mouth_pts[:, 0]))
    cy = float(np.mean(mouth_pts[:, 1]))

    half_w = 0.5 * (bx1 - bx0)
    half_h = 0.5 * (by1 - by0)
    clamped_edge = max(0.0, min(float(enc_edge), half_w - 0.1, half_h - 0.1))

    # For non-closed (partial-domain) geometries, we still need point arrays
    # for BSpline wire construction.  For closed geometries, enclosure wires
    # are built from line/arc primitives (_make_rect_wire) — no point arrays
    # needed, fully decoupled from n_angular.
    outer_pts: List[Tuple[float, float, float, float]] = []
    inset_pts: List[Tuple[float, float, float, float]] = []
    if not closed:
        mouth_angles = sorted([
            float(math.atan2(float(mouth_pts[i, 1]) - cy, float(mouth_pts[i, 0]) - cx))
            for i in range(mouth_pts.shape[0])
        ])
        n_enc = min(28, len(mouth_angles))
        angles = np.linspace(mouth_angles[0], mouth_angles[-1], n_enc).tolist()
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
    front_edge_dimtags_all: List[Tuple[int, int]] = []
    back_edge_dimtags_all: List[Tuple[int, int]] = []
    # Limit edge_depth to half of enc_depth so front + back fillets don't overlap.
    edge_depth = min(clamped_edge, max(0.0, enc_depth * 0.5))

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

    ring0_wire: Optional[int] = None
    ring0_loop: Optional[int] = None

    def _close_wire_for_surface(orig_curves: List[int], first_pt: int, last_pt: int) -> int:
        if closed:
            return int(gmsh.model.occ.addCurveLoop([int(c) for c in orig_curves]))
        line_tag = gmsh.model.occ.addLine(last_pt, first_pt)
        return int(gmsh.model.occ.addCurveLoop([int(c) for c in orig_curves] + [int(line_tag)]))

    # --- Build ring0 (inset ring at z_front) and front baffle ---
    if closed:
        # Sample rounded rectangle geometry at few points → BSpline wire.
        # Fully decoupled from n_angular; keeps single-curve wire topology.
        ring0_pts = _sample_rounded_rect(
            bx0=bx0 + clamped_edge, bx1=bx1 - clamped_edge,
            by0=by0 + clamped_edge, by1=by1 - clamped_edge,
            corner_radius=0.0, edge_type=enc_edge_type, z=z_front,
        )
        ring0_wire, ring0_curves, ring0_eps = _make_wire(ring0_pts, closed=True)
    else:
        ring0_pts = _ring_points_from_xy_plan(inset_pts, z=z_front)
        ring0_wire, ring0_curves, ring0_eps = _make_wire(ring0_pts, closed=closed)

    ring0_loop = _close_wire_for_surface(ring0_curves, ring0_eps[0], ring0_eps[1])
    try:
        front_tag = int(gmsh.model.occ.addPlaneSurface([int(ring0_loop), int(mouth_loop)]))
        generated_dimtags.append((2, front_tag))
    except Exception:
        generated_dimtags.extend(_add_ruled_section(current_profile, ring0_wire))
    current_profile = ring0_wire

    # --- Wire builder for roundover intermediate rings ---
    if closed:
        def _make_ring(z: float, radial_t: float):
            d = clamped_edge * (1.0 - radial_t)
            r = clamped_edge * radial_t
            pts = _sample_rounded_rect(
                bx0=bx0 + d, bx1=bx1 - d,
                by0=by0 + d, by1=by1 - d,
                corner_radius=r, edge_type=enc_edge_type, z=z,
            )
            return _make_wire(pts, closed=True)
    else:
        def _make_ring(z: float, radial_t: float):
            ring_plan = _interpolate_ring_plan(outer_pts, inset_pts, radial_t)
            ring_pts = _ring_points_from_xy_plan(ring_plan, z=z)
            return _make_wire(ring_pts, closed=closed)

    # --- Front roundover ---
    if edge_depth > 0.0:
        front_dt, current_profile, _, _ = _build_roundover_surface(
            current_profile, _make_ring,
            edge_depth, z_front, direction=1,
            enc_edge_type=enc_edge_type, closed=closed,
        )
        generated_dimtags.extend(front_dt)
        front_edge_dimtags_all.extend(front_dt)

    # --- Side walls (straight ruled surface from front outer to back outer) ---
    z_outer_back = z_back + edge_depth if edge_depth > 0.0 else z_back
    if closed:
        back_outer_pts = _sample_rounded_rect(
            bx0=bx0, bx1=bx1, by0=by0, by1=by1,
            corner_radius=clamped_edge, edge_type=enc_edge_type, z=z_outer_back,
        )
        back_outer_wire, back_outer_curves, back_outer_eps = _make_wire(back_outer_pts, closed=True)
    else:
        back_outer_pts = _ring_points_from_xy_plan(outer_pts, z=z_outer_back)
        back_outer_wire, back_outer_curves, back_outer_eps = _make_wire(back_outer_pts, closed=closed)
    generated_dimtags.extend(_add_ruled_section(current_profile, back_outer_wire))
    current_profile = back_outer_wire
    current_curves = back_outer_curves
    profile_pts = back_outer_eps

    # --- Back roundover ---
    if edge_depth > 0.0:
        back_dt, current_profile, current_curves, profile_pts = _build_roundover_surface(
            current_profile, _make_ring,
            edge_depth, z_back, direction=-1,
            enc_edge_type=enc_edge_type, closed=closed,
        )
        generated_dimtags.extend(back_dt)
        back_edge_dimtags_all.extend(back_dt)

    back_cap_loop = _close_wire_for_surface(current_curves, profile_pts[0], profile_pts[1])
    try:
        back_cap = gmsh.model.occ.addPlaneSurface([int(back_cap_loop)])
    except Exception:
        back_cap = gmsh.model.occ.addSurfaceFilling(current_profile)
    generated_dimtags.append((2, int(back_cap)))

    # For primitive (line+arc) wires, adjacent ruled surfaces don't share edge
    # topology automatically.  OCC fragment merges coincident edges so the
    # mesh is watertight.  Only needed for the closed/primitive path.
    if closed and len(generated_dimtags) > 1:
        pre_tags = {int(tag) for _, tag in generated_dimtags}
        pre_front_edge = {int(tag) for _, tag in front_edge_dimtags_all}
        pre_back_edge = {int(tag) for _, tag in back_edge_dimtags_all}
        try:
            out, out_map = gmsh.model.occ.fragment(
                [(d, t) for d, t in generated_dimtags], [],
            )
            # Rebuild dimtags and edge tag sets from fragment output.
            generated_dimtags = [(d, t) for d, t in out if d == 2]
            # Map old edge tags to new (post-fragment) tags.
            new_front_edge: Set[int] = set()
            new_back_edge: Set[int] = set()
            for i, (_, old_tag) in enumerate([(d, t) for d, t in zip(
                [d for d, _ in [(2, t) for t in pre_tags]], list(pre_tags)
            )]):
                pass  # fallback below
            # Use position-based reclassification instead of tracking through fragment.
            front_edge_dimtags_all = []
            back_edge_dimtags_all = []
        except Exception:
            pass  # Fall through to synchronize + position-based classification.

    gmsh.model.occ.synchronize()
    dimtags = [(2, int(tag)) for dim, tag in generated_dimtags if int(dim) == 2]
    front_edge_tags = {int(tag) for dim, tag in front_edge_dimtags_all if int(dim) == 2}
    back_edge_tags = {int(tag) for dim, tag in back_edge_dimtags_all if int(dim) == 2}
    all_edge_tags = front_edge_tags | back_edge_tags

    split = _classify_enclosure_surfaces(dimtags, z_front, z_back)
    front_edge_surfaces = [tag for tag in split["sides"] if int(tag) in front_edge_tags]
    back_edge_surfaces = [tag for tag in split["sides"] if int(tag) in back_edge_tags]
    side_surfaces = [tag for tag in split["sides"] if int(tag) not in all_edge_tags]
    return {
        "dimtags": dimtags,
        "front": split["front"],
        "back": split["back"],
        "sides": side_surfaces,
        "edges": list(all_edge_tags),
        "front_edges": front_edge_surfaces,
        "back_edges": back_edge_surfaces,
        "bounds": bounds,
        "opening_curves": [],
        "opening_ring_points": None,
    }

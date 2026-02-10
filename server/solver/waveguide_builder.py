"""
Waveguide mesh builder using Gmsh OCC (OpenCASCADE) Python API.

Generates .geo and .msh files from ATH-format parameters directly inside
Gmsh using parametric BSpline curves and ThruSections surfaces.

This is the architecturally correct approach per ATH section 3.3.1:
  "for each slice a smooth spline curve is created (controlled by the grid
   points), surface stripes between each adjacent pair of slices are created,
   each stripe is meshed independently."

Contrast with the legacy path (gmshGeoBuilder.js) which passes flat
triangulated surfaces to Gmsh — those have no curvature information.

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

import math
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .deps import GMSH_AVAILABLE, gmsh
from .gmsh_geo_mesher import gmsh_lock, parse_msh_stats, GmshMeshingError


# ---------------------------------------------------------------------------
# ATH expression evaluation
# ---------------------------------------------------------------------------

def _expression_to_callable(expr_str: str):
    """Convert an ATH math expression (may contain parameter 'p') to a Python callable.

    ATH syntax uses ^ for power. 'p' represents the angle phi around the
    waveguide axis in [0, 2*pi).

    Returns a callable(p: float) -> float.
    """
    expr = expr_str.strip().replace("^", "**")
    ns = {
        "abs": abs, "cos": math.cos, "sin": math.sin, "tan": math.tan,
        "acos": math.acos, "asin": math.asin, "atan": math.atan,
        "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
        "exp": math.exp, "pi": math.pi, "cosh": math.cosh,
        "sinh": math.sinh, "tanh": math.tanh, "floor": math.floor,
        "ceil": math.ceil, "fabs": math.fabs,
    }

    def fn(p: float) -> float:
        local_ns = dict(ns)
        local_ns["p"] = p
        return float(eval(expr, {"__builtins__": {}}, local_ns))  # noqa: S307

    try:
        fn(0.0)
    except Exception as exc:
        raise ValueError(f"Failed to evaluate ATH expression '{expr_str}': {exc}") from exc
    return fn


def _make_callable(value, default: float = 0.0):
    """Return a callable(p) from a numeric value, ATH expression string, or None.

    If value is None or empty string, returns a callable that returns default.
    """
    if value is None or value == "":
        return lambda p, _d=default: _d
    if callable(value):
        return value
    if isinstance(value, (int, float)):
        v = float(value)
        return lambda p, _v=v: _v
    text = str(value).strip()
    if not text:
        return lambda p, _d=default: _d
    # Check for 'p' as a variable token (not inside 'exp', 'pi', etc.)
    if re.search(r"(?<![a-zA-Z])p(?![a-zA-Z])", text):
        return _expression_to_callable(text)
    try:
        v = float(text)
        return lambda p, _v=v: _v
    except ValueError:
        return _expression_to_callable(text)


def _parse_number_list(text) -> List[float]:
    """Parse comma-separated number list, returns [] if empty or invalid."""
    if not text or not str(text).strip():
        return []
    parts = [p.strip() for p in str(text).split(",")]
    try:
        return [float(p) for p in parts if p]
    except ValueError:
        return []


# ---------------------------------------------------------------------------
# R-OSSE profile computation
# ---------------------------------------------------------------------------

def _compute_rosse_profile(
    t_array: np.ndarray,
    R: float,
    a_deg: float,
    r0: float,
    a0_deg: float,
    k: float,
    r_param: float,
    b_param: float,
    m_param: float,
    q_param: float,
    tmax: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """Compute R-OSSE profile (x_axial, y_radial) for t ∈ [0, 1].

    t_array is normalized [0,1]. tmax is the R-OSSE truncation factor.
    All distances in mm, angles in degrees.
    Returns (x_arr, y_arr, length_mm).
    """
    t_actual = t_array * tmax
    a0_rad = math.radians(a0_deg)
    a_rad = math.radians(a_deg)

    c1 = (k * r0) ** 2
    c2 = 2.0 * k * r0 * math.tan(a0_rad)
    c3 = math.tan(a_rad) ** 2

    target = R + r0 * (k - 1.0)
    discriminant = c2 ** 2 - 4.0 * c3 * (c1 - target ** 2)
    if discriminant < 0:
        raise ValueError(
            f"Negative discriminant in R-OSSE profile: R={R:.2f}, a={a_deg:.2f}, "
            f"r0={r0}, a0={a0_deg}, k={k}. Check formula parameters."
        )
    L = (math.sqrt(discriminant) - c2) / (2.0 * c3)

    r, m, b, q = r_param, m_param, b_param, q_param
    sqrt_r2_m2 = math.sqrt(r ** 2 + m ** 2)
    x_scale2 = b * L * (math.sqrt(r ** 2 + (1.0 - m) ** 2) - sqrt_r2_m2)

    x_arr = np.empty_like(t_actual)
    y_arr = np.empty_like(t_actual)

    for i, t in enumerate(t_actual):
        x_val = L * (sqrt_r2_m2 - math.sqrt(r ** 2 + (t - m) ** 2)) + x_scale2 * t ** 2
        tq = t ** q
        y_os = math.sqrt(c1 + c2 * L * t + c3 * L ** 2 * t ** 2) + r0 * (1.0 - k)
        y_term = R + L * (1.0 - math.sqrt(1.0 + c3 * (t - 1.0) ** 2))
        y_arr[i] = (1.0 - tq) * y_os + tq * y_term
        x_arr[i] = x_val

    return x_arr, y_arr, L


# ---------------------------------------------------------------------------
# OSSE profile computation
# ---------------------------------------------------------------------------

def _compute_osse_base_radius(z_main: float, r0_main: float, k: float,
                               a0_rad: float, a_cov_rad: float) -> float:
    """OSSE oblate-spheroid (OS) base radius at axial position z_main."""
    t1 = (k * r0_main) ** 2
    t2 = 2.0 * k * r0_main * z_main * math.tan(a0_rad)
    t3 = (z_main ** 2) * (math.tan(a_cov_rad) ** 2)
    return math.sqrt(max(0.0, t1 + t2 + t3)) + r0_main * (1.0 - k)


def _compute_osse_term_radius(z_main: float, L: float, s: float, n: float, q: float) -> float:
    """OSSE super-exponential (SE) termination radius contribution at z_main."""
    if z_main <= 0 or n <= 0 or q <= 0 or L <= 0 or s == 0:
        return 0.0
    z_norm = q * z_main / L
    if z_norm > 1.0:
        return s * L / q
    return (s * L / q) * (1.0 - (1.0 - z_norm ** n) ** (1.0 / n))


def _compute_osse_radius_at(z_main: float, a_cov_deg: float, a0_deg: float,
                             r0_main: float, k: float,
                             s: float, n: float, q: float, L: float) -> float:
    """Full OSSE radius (OS base + SE termination) at z_main."""
    a0_rad = math.radians(a0_deg)
    a_rad = math.radians(a_cov_deg)
    base = _compute_osse_base_radius(z_main, r0_main, k, a0_rad, a_rad)
    term = _compute_osse_term_radius(z_main, L, s, n, q)
    return base + term


def _invert_osse_coverage_angle(
    target_r: float, z_main: float, r0_main: float, a0_deg: float,
    k: float, s: float, n: float, q: float, L: float,
) -> float:
    """Binary search for coverage angle [deg] that gives target_r at z_main.

    Used by guiding curve to invert the OSSE radius function.
    24 iterations yield ~10^-7 degree precision.
    """
    low, high = 0.5, 89.0
    for _ in range(24):
        mid = (low + high) / 2.0
        r_mid = _compute_osse_radius_at(z_main, mid, a0_deg, r0_main, k, s, n, q, L)
        if not math.isfinite(r_mid):
            break
        if r_mid < target_r:
            low = mid
        else:
            high = mid
    return max(0.5, min(89.0, (low + high) / 2.0))


def _evaluate_circular_arc(z_main: float, r0_main: float, mouth_r: float,
                            circ_arc_radius: float, circ_arc_term_angle_deg: float,
                            L: float) -> float:
    """Evaluate circular arc profile (Throat.Profile=3) at z_main.

    Two options:
      1. Explicit circ_arc_radius: find arc center equidistant from both endpoints.
      2. circ_arc_term_angle_deg: find arc tangent at throat with given angle.
    """
    p1 = (0.0, r0_main)
    p2 = (L, mouth_r)

    center = None
    arc_radius = circ_arc_radius

    if arc_radius > 0:
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        d = math.hypot(dx, dy)
        if d > 0 and arc_radius >= d / 2.0:
            mid_x = (p1[0] + p2[0]) / 2.0
            mid_y = (p1[1] + p2[1]) / 2.0
            h = math.sqrt(max(0.0, arc_radius ** 2 - (d / 2.0) ** 2))
            nx, ny = -dy / d, dx / d
            c1 = (mid_x + nx * h, mid_y + ny * h)
            c2 = (mid_x - nx * h, mid_y - ny * h)
            center = c1 if c1[1] >= c2[1] else c2

    if center is None:
        term_rad = math.radians(max(1e-6, circ_arc_term_angle_deg))
        t_dir = (math.cos(term_rad), math.sin(term_rad))
        n_dir = (-t_dir[1], t_dir[0])
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        d_dot_n = dx * n_dir[0] + dy * n_dir[1]
        if abs(d_dot_n) > 1e-6:
            arc_radius = -((dx ** 2 + dy ** 2) / (2.0 * d_dot_n))
            center = (p2[0] + n_dir[0] * arc_radius, p2[1] + n_dir[1] * arc_radius)

    if center is None or not math.isfinite(arc_radius) or arc_radius == 0:
        return mouth_r

    dx_c = z_main - center[0]
    under = arc_radius ** 2 - dx_c ** 2
    if under < 0:
        return mouth_r

    sign = math.copysign(1.0, mouth_r - center[1]) or 1.0
    return center[1] + sign * math.sqrt(under)


# ---------------------------------------------------------------------------
# Guiding curve computation
# ---------------------------------------------------------------------------

def _parse_superformula_params(params: dict) -> dict:
    """Parse superformula parameters from gcurve_sf string or individual fields."""
    a = float(params.get("gcurve_sf_a") or 1)
    b = float(params.get("gcurve_sf_b") or 1)
    m1 = float(params.get("gcurve_sf_m1") or 0)
    m2 = float(params.get("gcurve_sf_m2") or 0)
    n1 = float(params.get("gcurve_sf_n1") or 1)
    n2 = float(params.get("gcurve_sf_n2") or 1)
    n3 = float(params.get("gcurve_sf_n3") or 1)

    lst = _parse_number_list(params.get("gcurve_sf", ""))
    if len(lst) >= 6:
        a, b, m1, n1, n2, n3 = lst[:6]
        m2 = m1

    return {"a": a, "b": b, "m1": m1, "m2": m2, "n1": n1, "n2": n2, "n3": n3}


def _compute_guiding_curve_radius(phi: float, params: dict) -> Optional[float]:
    """Guiding curve radius at azimuthal angle phi. Returns None if disabled."""
    gcurve_type = int(float(params.get("gcurve_type", 0) or 0))
    if gcurve_type == 0:
        return None

    width = float(params.get("gcurve_width", 0) or 0)
    if width <= 0:
        return None

    aspect = float(params.get("gcurve_aspect_ratio", 1) or 1)
    rot = math.radians(float(params.get("gcurve_rot", 0) or 0))
    pr = phi - rot
    cos_pr = math.cos(pr)
    sin_pr = math.sin(pr)

    if gcurve_type == 1:  # superellipse
        n = max(2.0, float(params.get("gcurve_se_n", 3) or 3))
        a = width / 2.0
        b_val = a * aspect
        if a <= 0 or b_val <= 0:
            return None
        term = (abs(cos_pr / a) ** n + abs(sin_pr / b_val) ** n)
        if term <= 0:
            return None
        return term ** (-1.0 / n)

    if gcurve_type == 2:  # superformula
        sf = _parse_superformula_params(params)
        try:
            t1 = abs(math.cos(sf["m1"] * pr / 4.0) / sf["a"]) ** sf["n2"]
            t2 = abs(math.sin(sf["m2"] * pr / 4.0) / sf["b"]) ** sf["n3"]
            r_norm = (t1 + t2) ** (-1.0 / sf["n1"])
        except (ZeroDivisionError, ValueError, OverflowError):
            return None
        if not math.isfinite(r_norm):
            return None
        sx = width / 2.0
        sy = sx * aspect
        return math.hypot(r_norm * cos_pr * sx, r_norm * sin_pr * sy)

    return None


def _compute_coverage_from_guiding_curve(
    phi: float, params: dict,
    r0_main: float, a0_deg: float, k: float,
    s: float, n: float, q: float, L: float,
    ext_len: float, slot_len: float, total_length: float,
) -> Optional[float]:
    """Coverage angle [deg] derived from guiding curve via OSSE radius inversion."""
    target_r = _compute_guiding_curve_radius(phi, params)
    if target_r is None:
        return None

    dist_param = _make_callable(params.get("gcurve_dist", 0.5), default=0.5)(phi)
    dist_raw = total_length * dist_param if dist_param <= 1.0 else dist_param
    dist = max(0.0, min(dist_raw, total_length))
    if dist <= 0:
        return None

    z_main = max(0.0, dist - ext_len - slot_len)
    return _invert_osse_coverage_angle(target_r, z_main, r0_main, a0_deg, k, s, n, q, L)


def _compute_osse_profile_arrays(
    t_values: np.ndarray, phi: float, params: dict
) -> Tuple[np.ndarray, np.ndarray, float]:
    """Compute OSSE profile (x_axial, y_radial) for t ∈ [0, 1].

    Returns (x_arr, y_arr, total_length).
    """
    L_fn = _make_callable(params.get("L", 120), default=120.0)
    L = L_fn(phi)
    ext_len = max(0.0, _make_callable(params.get("throat_ext_length", 0))(phi))
    slot_len = max(0.0, _make_callable(params.get("slot_length", 0))(phi))
    total_length = L + ext_len + slot_len
    ext_angle_rad = math.radians(_make_callable(params.get("throat_ext_angle", 0))(phi))

    r0_base = _make_callable(params.get("r0", 12.7), default=12.7)(phi)
    a0_deg = _make_callable(params.get("a0", 15.5), default=15.5)(phi)
    r0_main = r0_base + ext_len * math.tan(ext_angle_rad)

    k = _make_callable(params.get("k", 1.0), default=1.0)(phi)
    s_val = _make_callable(params.get("s", 0.0))(phi)
    n_val = _make_callable(params.get("n", 4.0), default=4.0)(phi)
    q_val = _make_callable(params.get("q", 0.995), default=0.995)(phi)
    h_val = _make_callable(params.get("h", 0.0))(phi)

    # Coverage angle — from guiding curve or direct expression
    gcurve_type = int(float(params.get("gcurve_type", 0) or 0))
    if gcurve_type != 0:
        cov_from_gcurve = _compute_coverage_from_guiding_curve(
            phi, params, r0_main, a0_deg, k, s_val, n_val, q_val, L,
            ext_len, slot_len, total_length,
        )
        a_fn = _make_callable(params.get("a", "60"), default=60.0)
        coverage_angle = cov_from_gcurve if cov_from_gcurve is not None else a_fn(phi)
    else:
        coverage_angle = _make_callable(params.get("a", "60"), default=60.0)(phi)

    a_cov_rad = math.radians(coverage_angle)

    throat_profile = int(float(params.get("throat_profile", 1) or 1))
    circ_arc_radius = float(params.get("circ_arc_radius", 0) or 0)
    circ_arc_term_angle = float(params.get("circ_arc_term_angle", 1) or 1)
    mouth_r_for_arc = r0_main + L * math.tan(a_cov_rad)

    rot_deg = _make_callable(params.get("rot", 0))(phi)
    do_rot = abs(rot_deg) > 1e-9

    x_arr = np.empty_like(t_values)
    y_arr = np.empty_like(t_values)

    for i, t in enumerate(t_values):
        z = t * total_length
        if z <= ext_len:
            x_val = z
            y_val = r0_base + z * math.tan(ext_angle_rad)
        elif z <= ext_len + slot_len:
            x_val = z
            y_val = r0_main
        else:
            z_main = z - ext_len - slot_len
            x_val = z
            if throat_profile == 3:
                y_val = _evaluate_circular_arc(
                    z_main, r0_main, mouth_r_for_arc,
                    circ_arc_radius, circ_arc_term_angle, L
                )
            else:
                y_val = _compute_osse_radius_at(
                    z_main, coverage_angle, a0_deg, r0_main, k, s_val, n_val, q_val, L
                )

        # h adjustment (extra sinusoidal bulge along the profile)
        if h_val > 0:
            y_val += h_val * math.sin(t * math.pi)

        # Profile rotation around point [0, r0_base]
        if do_rot:
            rot_rad = math.radians(rot_deg)
            dx = x_val
            dy = y_val - r0_base
            x_val = dx * math.cos(rot_rad) - dy * math.sin(rot_rad)
            y_val = r0_base + dx * math.sin(rot_rad) + dy * math.cos(rot_rad)

        x_arr[i] = x_val
        y_arr[i] = y_val

    return x_arr, y_arr, total_length


# ---------------------------------------------------------------------------
# Morph feature
# ---------------------------------------------------------------------------

def _get_rounded_rect_radius(phi: float, half_w: float, half_h: float, corner_r: float) -> float:
    """Radius of a rounded rectangle outline at angle phi (port of getRoundedRectRadius)."""
    abs_cos = abs(math.cos(phi))
    abs_sin = abs(math.sin(phi))

    if abs_cos < 1e-9:
        return half_h
    if abs_sin < 1e-9:
        return half_w

    r = max(0.0, min(corner_r, min(half_w, half_h)))
    if r <= 1e-9:
        return min(half_w / abs_cos, half_h / abs_sin)

    # Check which region the ray hits
    y_at_x = (half_w * abs_sin) / abs_cos
    if y_at_x <= half_h - r + 1e-9:
        return half_w / abs_cos

    x_at_y = (half_h * abs_cos) / abs_sin
    if x_at_y <= half_w - r + 1e-9:
        return half_h / abs_sin

    # Corner circle region — solve quadratic
    cx = half_w - r
    cy = half_h - r
    A = abs_cos ** 2 + abs_sin ** 2
    B = -2.0 * (abs_cos * cx + abs_sin * cy)
    C = cx ** 2 + cy ** 2 - r ** 2
    disc = max(0.0, B ** 2 - 4.0 * A * C)
    return (-B + math.sqrt(disc)) / (2.0 * A)


def _get_morph_target_radius(phi: float, target_shape: int,
                              half_w: float, half_h: float, corner_r: float) -> float:
    """Target radius at angle phi for the requested morph shape."""
    if target_shape == 2:  # circle
        return math.sqrt(max(0.0, half_w * half_h))
    # target_shape == 1: rectangle (with optional corner rounding)
    return _get_rounded_rect_radius(phi, half_w, half_h, corner_r)


def _apply_morph(current_r: float, t: float, phi: float, params: dict,
                 morph_target_info: Optional[dict] = None) -> float:
    """Apply morph transformation to radius at position (t, phi).

    morph_target_info: {'half_w': float, 'half_h': float} precomputed from
    raw slice extents. Only needed when morph_width/morph_height are not set.
    """
    target_shape = int(float(params.get("morph_target", 0) or 0))
    if target_shape == 0:
        return current_r

    morph_fixed = float(params.get("morph_fixed", 0) or 0)
    if t <= morph_fixed:
        return current_r

    rate = float(params.get("morph_rate", 3) or 3)
    morph_factor = ((t - morph_fixed) / max(1e-9, 1.0 - morph_fixed)) ** rate

    morph_width = float(params.get("morph_width", 0) or 0)
    morph_height = float(params.get("morph_height", 0) or 0)
    has_explicit = (morph_width > 0) or (morph_height > 0)

    half_w = morph_width / 2.0 if morph_width > 0 \
        else (morph_target_info["half_w"] if morph_target_info else current_r)
    half_h = morph_height / 2.0 if morph_height > 0 \
        else (morph_target_info["half_h"] if morph_target_info else current_r)

    if not has_explicit and not morph_target_info:
        return current_r

    corner_r = float(params.get("morph_corner", 0) or 0)
    target_r = _get_morph_target_radius(phi, target_shape, half_w, half_h, corner_r)
    allow_shrinkage = bool(int(float(params.get("morph_allow_shrinkage", 0) or 0)))
    safe_target = target_r if allow_shrinkage else max(current_r, target_r)
    return current_r + morph_factor * (safe_target - current_r)


def _compute_morph_target_info(
    raw_y: np.ndarray, phi_values: np.ndarray, params: dict, morph_target: int
) -> Optional[List[dict]]:
    """Precompute per-slice morph target extents from raw (pre-morph) radii.

    Only needed when morph is enabled AND explicit morph_width/morph_height
    are not both provided. Returns list of {'half_w', 'half_h'} per slice.
    """
    if morph_target == 0:
        return None

    morph_width = float(params.get("morph_width", 0) or 0)
    morph_height = float(params.get("morph_height", 0) or 0)
    if morph_width > 0 and morph_height > 0:
        return None  # Explicit dimensions — no precomputation needed

    n_slices = raw_y.shape[1]
    morph_info = []
    for j in range(n_slices):
        half_w = float(np.max(np.abs(raw_y[:, j] * np.cos(phi_values))))
        half_h = float(np.max(np.abs(raw_y[:, j] * np.sin(phi_values))))
        morph_info.append({"half_w": half_w, "half_h": half_h})
    return morph_info


# ---------------------------------------------------------------------------
# Angular (phi) values
# ---------------------------------------------------------------------------

def _compute_phi_values(n_angular: int, quadrants: int) -> np.ndarray:
    """Compute angular sample positions respecting the ATH quadrant convention.

    quadrants=1234: full circle [0, 2π)
    quadrants=1:    Q1 [0, π/2]
    quadrants=12:   Q1+Q2 [0, π]
    quadrants=14:   Q1+Q4 [−π/2, π/2]
    """
    if quadrants == 1234:
        return np.linspace(0.0, 2.0 * math.pi, n_angular, endpoint=False)
    if quadrants == 1:
        n = n_angular // 4 + 1
        return np.linspace(0.0, math.pi / 2.0, n)
    if quadrants == 12:
        n = n_angular // 2 + 1
        return np.linspace(0.0, math.pi, n)
    if quadrants == 14:
        n = n_angular // 2 + 1
        return np.linspace(-math.pi / 2.0, math.pi / 2.0, n)
    raise ValueError(f"Unsupported Mesh.Quadrants value: {quadrants}. Use 1, 12, 14, or 1234.")


# ---------------------------------------------------------------------------
# Point grid computation
# ---------------------------------------------------------------------------

def _compute_point_grids(params: dict) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """Compute 3D inner (and optionally outer) surface point grids.

    First pass: compute raw (pre-morph) profiles for all phi values.
    Second pass: apply morph and project to 3D.

    Returns:
        inner_points: (n_phi, n_length+1, 3)
        outer_points: (n_phi, n_length+1, 3) or None if sim_type==1
    """
    formula_type = params.get("formula_type", "R-OSSE")
    n_angular = int(params.get("n_angular", 100))
    n_length = int(params.get("n_length", 20))
    quadrants = int(params.get("quadrants", 1234))
    sim_type = int(params.get("sim_type", 2))
    wall_thickness = float(params.get("wall_thickness", 6.0))
    morph_target = int(float(params.get("morph_target", 0) or 0))

    phi_values = _compute_phi_values(n_angular, quadrants)
    t_values = np.linspace(0.0, 1.0, n_length + 1)
    n_phi = len(phi_values)

    raw_x = np.zeros((n_phi, n_length + 1))
    raw_y = np.zeros((n_phi, n_length + 1))

    if formula_type == "R-OSSE":
        r0 = float(params.get("r0", 12.7))
        a0 = float(params.get("a0", 15.5))
        k = float(params.get("k", 2.0))
        r_p = float(params.get("r", 0.4))
        b_p = float(params.get("b", 0.2))
        m_p = float(params.get("m", 0.85))
        q_p = float(params.get("q", 3.4))

        R_func = _make_callable(params["R"])
        a_func = _make_callable(params["a"])
        tmax_fn = _make_callable(params.get("tmax", 1.0), default=1.0)

        for i, phi in enumerate(phi_values):
            x_arr, y_arr, _ = _compute_rosse_profile(
                t_values, R_func(phi), a_func(phi),
                r0, a0, k, r_p, b_p, m_p, q_p,
                tmax=tmax_fn(phi),
            )
            raw_x[i] = x_arr
            raw_y[i] = y_arr

    elif formula_type == "OSSE":
        for i, phi in enumerate(phi_values):
            x_arr, y_arr, _ = _compute_osse_profile_arrays(t_values, phi, params)
            raw_x[i] = x_arr
            raw_y[i] = y_arr

    else:
        raise ValueError(
            f"Unsupported formula_type '{formula_type}'. Use 'R-OSSE' or 'OSSE'."
        )

    # Precompute per-slice morph target extents (two-pass approach matching JS engine)
    morph_info = _compute_morph_target_info(raw_y, phi_values, params, morph_target)

    # Build 3D inner points with morph applied to the radial (y) coordinate
    inner_points = np.zeros((n_phi, n_length + 1, 3))
    for i, phi in enumerate(phi_values):
        for j, t in enumerate(t_values):
            y_m = _apply_morph(
                raw_y[i, j], t, phi, params,
                morph_info[j] if morph_info is not None else None,
            )
            inner_points[i, j, 0] = y_m * math.cos(phi)
            inner_points[i, j, 1] = y_m * math.sin(phi)
            inner_points[i, j, 2] = raw_x[i, j]

    outer_points = None
    if sim_type == 2:
        outer_points = _compute_outer_points(inner_points, wall_thickness, phi_values)

    return inner_points, outer_points


def _compute_outer_points(
    inner_points: np.ndarray,
    wall_thickness: float,
    phi_values: np.ndarray,
) -> np.ndarray:
    """Offset inner surface by wall_thickness in the 2D profile plane per slice."""
    outer_points = np.zeros_like(inner_points)
    for i, phi in enumerate(phi_values):
        cos_phi = math.cos(phi)
        sin_phi = math.sin(phi)
        x_axial = inner_points[i, :, 2]
        y_radial = np.sqrt(inner_points[i, :, 0] ** 2 + inner_points[i, :, 1] ** 2)
        dx = np.gradient(x_axial)
        dy = np.gradient(y_radial)
        normals_x, normals_y = dy.copy(), -dx.copy()
        norms = np.maximum(np.sqrt(normals_x ** 2 + normals_y ** 2), 1e-12)
        normals_x /= norms
        normals_y /= norms
        for j in range(len(x_axial)):
            if normals_y[j] < 0:
                normals_x[j] = -normals_x[j]
                normals_y[j] = -normals_y[j]
        x_outer = x_axial + wall_thickness * normals_x
        y_outer = y_radial + wall_thickness * normals_y
        outer_points[i, :, 0] = y_outer * cos_phi
        outer_points[i, :, 1] = y_outer * sin_phi
        outer_points[i, :, 2] = x_outer
    return outer_points


# ---------------------------------------------------------------------------
# Gmsh geometry construction (OCC kernel)
# ---------------------------------------------------------------------------

def _make_wire(points_2d: np.ndarray, closed: bool = True) -> int:
    """Create a BSpline wire from an (n, 3) array of 3D points.

    closed=True creates a closed loop (full 360° profiles).
    Returns Gmsh wire tag.
    """
    n = points_2d.shape[0]
    pt_tags = []
    for i in range(n):
        x, y, z = float(points_2d[i, 0]), float(points_2d[i, 1]), float(points_2d[i, 2])
        pt_tags.append(gmsh.model.occ.addPoint(x, y, z))
    if closed:
        pt_tags.append(pt_tags[0])
    spline = gmsh.model.occ.addBSpline(pt_tags)
    wire = gmsh.model.occ.addWire([spline])
    return wire


def _build_surface_from_points(
    points: np.ndarray, closed: bool = True
) -> List[Tuple[int, int]]:
    """Build a surface through cross-sectional slices using pairwise ruled strips.

    Per ATH section 3.3.1: each pair of adjacent slices creates one strip,
    meshed independently.

    points: (n_angular, n_length+1, 3)
    Returns list of (dim=2, tag) surface dimtags.
    """
    n_len = points.shape[1]
    wires = []
    for j in range(n_len):
        wires.append(_make_wire(points[:, j, :], closed=closed))

    all_dimtags = []
    for j in range(n_len - 1):
        strip = gmsh.model.occ.addThruSections(
            [wires[j], wires[j + 1]], makeSolid=False, makeRuled=True
        )
        all_dimtags.extend(strip)
    return all_dimtags


def _build_throat_disc(r0: float) -> List[Tuple[int, int]]:
    """Build a flat disc surface closing the throat at z=0 (the source/piston)."""
    disk_tag = gmsh.model.occ.addDisk(0.0, 0.0, 0.0, r0, r0)
    return [(2, disk_tag)]


def _build_rear_wall(
    inner_points: np.ndarray, outer_points: np.ndarray, closed: bool
) -> List[Tuple[int, int]]:
    """Annular surface connecting inner and outer surfaces at the throat (z≈0)."""
    w_inner = _make_wire(inner_points[:, 0, :], closed=closed)
    w_outer = _make_wire(outer_points[:, 0, :], closed=closed)
    return gmsh.model.occ.addThruSections([w_inner, w_outer], makeSolid=False, makeRuled=True)


def _build_mouth_rim(
    inner_points: np.ndarray, outer_points: np.ndarray, closed: bool
) -> List[Tuple[int, int]]:
    """Annular surface connecting inner and outer surfaces at the mouth end."""
    j_mouth = inner_points.shape[1] - 1
    w_inner = _make_wire(inner_points[:, j_mouth, :], closed=closed)
    w_outer = _make_wire(outer_points[:, j_mouth, :], closed=closed)
    return gmsh.model.occ.addThruSections([w_inner, w_outer], makeSolid=False, makeRuled=True)


# ---------------------------------------------------------------------------
# Mesh size configuration
# ---------------------------------------------------------------------------

def _configure_mesh_size(
    inner_points: np.ndarray,
    surface_groups: Dict[str, List[int]],
    throat_res: float,
    mouth_res: float,
    rear_res: float,
) -> None:
    """Set per-surface mesh resolution using Gmsh Restrict fields.

    Inner surface: interpolated throat_res → mouth_res by radial distance.
    Throat disc: constant throat_res.
    Outer/rear/mouth surfaces: constant rear_res.
    """
    fields: List[int] = []

    r_throat = float(np.sqrt(inner_points[:, 0, 0] ** 2 + inner_points[:, 0, 1] ** 2).max())
    r_mouth = float(np.sqrt(inner_points[:, -1, 0] ** 2 + inner_points[:, -1, 1] ** 2).max())
    r_range = max(r_mouth - r_throat, 1e-6)
    slope = (mouth_res - throat_res) / r_range
    intercept = throat_res - slope * r_throat

    def add_restricted_matheval(formula: str, surface_tags: List[int]) -> int:
        f_base = gmsh.model.mesh.field.add("MathEval")
        gmsh.model.mesh.field.setString(f_base, "F", formula)
        f_restrict = gmsh.model.mesh.field.add("Restrict")
        gmsh.model.mesh.field.setNumber(f_restrict, "InField", f_base)
        gmsh.model.mesh.field.setNumbers(f_restrict, "SurfacesList", surface_tags)
        return f_restrict

    if surface_groups.get("inner"):
        formula = f"{intercept:.6g} + {slope:.6g} * Sqrt(x*x + y*y)"
        fields.append(add_restricted_matheval(formula, surface_groups["inner"]))

    for group_key in ("throat_disc",):
        if surface_groups.get(group_key):
            fields.append(add_restricted_matheval(f"{throat_res:.6g}", surface_groups[group_key]))

    for group_key in ("outer", "rear", "mouth"):
        if surface_groups.get(group_key):
            fields.append(add_restricted_matheval(f"{rear_res:.6g}", surface_groups[group_key]))

    if fields:
        f_min = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(f_min, "FieldsList", fields)
        gmsh.model.mesh.field.setAsBackgroundMesh(f_min)

    gmsh.option.setNumber("Mesh.MeshSizeMin", min(throat_res, mouth_res) * 0.5)
    gmsh.option.setNumber("Mesh.MeshSizeMax", rear_res * 1.5)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)


# ---------------------------------------------------------------------------
# Physical group assignment (ABEC convention)
# ---------------------------------------------------------------------------

def _assign_physical_groups(
    surface_groups: Dict[str, List[int]],
    sim_type: int,
) -> None:
    """Assign ABEC-compatible physical group names to meshed surfaces.

    Tag contract:
        SD1G0     (1) - inner horn wall
        SD1D1001  (2) - throat source disc (driving element)
        SD2G0     (3) - outer/rear/mouth (free-standing, sim_type==2)
    """
    inner_tags = surface_groups.get("inner", [])
    if inner_tags:
        gmsh.model.addPhysicalGroup(2, inner_tags, tag=1)
        gmsh.model.setPhysicalName(2, 1, "SD1G0")

    disc_tags = surface_groups.get("throat_disc", [])
    if disc_tags:
        gmsh.model.addPhysicalGroup(2, disc_tags, tag=2)
        gmsh.model.setPhysicalName(2, 2, "SD1D1001")

    if sim_type == 2:
        exterior_tags = (
            surface_groups.get("outer", [])
            + surface_groups.get("rear", [])
            + surface_groups.get("mouth", [])
        )
        if exterior_tags:
            gmsh.model.addPhysicalGroup(2, exterior_tags, tag=3)
            gmsh.model.setPhysicalName(2, 3, "SD2G0")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_waveguide_mesh(params: dict) -> dict:
    """Build a .msh from ATH parameters using Gmsh OCC Python API.

    Accepts both R-OSSE and OSSE formula types. See WaveguideParamsRequest
    in server/app.py for the full parameter schema.

    Returns:
        {
            "msh_text": str,    Gmsh .msh file content
            "stats": {nodeCount, elementCount}
        }

    Raises:
        GmshMeshingError on any Gmsh failure.
        ValueError on invalid parameter values.
        RuntimeError if gmsh Python API is not available.
    """
    if not GMSH_AVAILABLE:
        raise RuntimeError(
            "Gmsh Python API is not available. Install gmsh: pip install gmsh>=4.10.0"
        )

    formula_type = params.get("formula_type", "R-OSSE")
    if formula_type not in ("R-OSSE", "OSSE"):
        raise ValueError(
            f"Formula type '{formula_type}' is not supported. Use 'R-OSSE' or 'OSSE'."
        )

    sim_type = int(params.get("sim_type", 2))
    msh_version = str(params.get("msh_version", "2.2"))
    quadrants = int(params.get("quadrants", 1234))
    closed = (quadrants == 1234)

    # Compute 3D point grids (includes morph)
    inner_points, outer_points = _compute_point_grids(params)

    throat_res = float(params.get("throat_res", 5.0))
    mouth_res = float(params.get("mouth_res", 8.0))
    rear_res = float(params.get("rear_res", 25.0))

    # Use mean throat radius across all phi for the throat disc
    r0_throat = float(np.sqrt(inner_points[:, 0, 0] ** 2 + inner_points[:, 0, 1] ** 2).mean())

    with gmsh_lock:
        initialized_here = False
        try:
            if not gmsh.isInitialized():
                gmsh.initialize()
                initialized_here = True

            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.clear()
            gmsh.model.add("WaveguideOCC")

            # --- Build geometry ---
            inner_dimtags = _build_surface_from_points(inner_points, closed=closed)
            throat_disc_dimtags = _build_throat_disc(r0_throat) if closed else []

            outer_dimtags = []
            rear_dimtags = []
            mouth_dimtags = []
            if sim_type == 2 and outer_points is not None:
                outer_dimtags = _build_surface_from_points(outer_points, closed=closed)
                rear_dimtags = _build_rear_wall(inner_points, outer_points, closed=closed)
                mouth_dimtags = _build_mouth_rim(inner_points, outer_points, closed=closed)

            gmsh.model.occ.synchronize()

            # --- Surface groups ---
            surface_groups: Dict[str, List[int]] = {
                "inner": [tag for _, tag in inner_dimtags],
            }
            if closed:
                surface_groups["throat_disc"] = [tag for _, tag in throat_disc_dimtags]
            if sim_type == 2:
                surface_groups["outer"] = [tag for _, tag in outer_dimtags]
                surface_groups["rear"] = [tag for _, tag in rear_dimtags]
                surface_groups["mouth"] = [tag for _, tag in mouth_dimtags]

            # --- Mesh size fields ---
            _configure_mesh_size(inner_points, surface_groups, throat_res, mouth_res, rear_res)

            # --- Physical groups (ABEC-compatible) ---
            _assign_physical_groups(surface_groups, sim_type)

            # --- Mesh algorithm (MeshAdapt handles complex curved surfaces well) ---
            gmsh.option.setNumber("Mesh.Algorithm", 1)
            gmsh.option.setNumber("Mesh.AngleToleranceFacetOverlap", 0.5)
            gmsh.option.setNumber("Mesh.MshFileVersion", float(msh_version))

            # --- Generate mesh ---
            gmsh.model.mesh.generate(2)

            # --- Write outputs ---
            with tempfile.TemporaryDirectory(prefix="mwg-occ-") as tmp_dir:
                tmp = Path(tmp_dir)
                msh_path = tmp / "output.msh"

                gmsh.write(str(msh_path))

                if not msh_path.exists():
                    raise GmshMeshingError("Gmsh OCC builder did not produce a .msh file.")

                msh_text = msh_path.read_text(encoding="utf-8", errors="replace")

            stats = parse_msh_stats(msh_text)
            return {
                "msh_text": msh_text,
                "stats": stats,
            }

        except (GmshMeshingError, ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise GmshMeshingError(f"Gmsh OCC build failed: {exc}") from exc
        finally:
            if initialized_here and gmsh.isInitialized():
                gmsh.finalize()

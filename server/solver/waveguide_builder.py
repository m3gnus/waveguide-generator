"""
Waveguide mesh builder using Gmsh OCC (OpenCASCADE) Python API.

Generates Gmsh-authored .msh (and optional STL) from ATH-format parameters
directly inside Gmsh using parametric BSpline curves and ThruSections surfaces.

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
from typing import Any, Dict, List, Optional, Set, Tuple

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

    Outer points are computed only for the wall-shell case (enc_depth == 0 and
    wall_thickness > 0). Enclosure box geometry is built from inner_points only.

    Returns:
        inner_points: (n_phi, n_length+1, 3)
        outer_points: (n_phi, n_length+1, 3) or None
    """
    formula_type = params.get("formula_type", "R-OSSE")
    n_angular = int(params.get("n_angular", 100))
    n_length = int(params.get("n_length", 20))
    quadrants = int(params.get("quadrants", 1234))
    enc_depth = float(params.get("enc_depth", 0) or 0)
    wall_thickness = float(params.get("wall_thickness", 6.0))
    morph_target = int(float(params.get("morph_target", 0) or 0))

    phi_values = _compute_phi_values(n_angular, quadrants)
    t_values = np.linspace(0.0, 1.0, n_length + 1)
    n_phi = len(phi_values)

    raw_x = np.zeros((n_phi, n_length + 1))
    raw_y = np.zeros((n_phi, n_length + 1))

    if formula_type == "R-OSSE":
        r0_fn = _make_callable(params.get("r0", 12.7), default=12.7)
        a0_fn = _make_callable(params.get("a0", 15.5), default=15.5)
        k_fn = _make_callable(params.get("k", 2.0), default=2.0)
        r_fn = _make_callable(params.get("r", 0.4), default=0.4)
        m_fn = _make_callable(params.get("m", 0.85), default=0.85)
        q_fn = _make_callable(params.get("q", 3.4), default=3.4)
        R_func = _make_callable(params["R"])
        a_func = _make_callable(params["a"])
        b_func = _make_callable(params.get("b", 0.2), default=0.2)
        tmax_fn = _make_callable(params.get("tmax", 1.0), default=1.0)

        for i, phi in enumerate(phi_values):
            x_arr, y_arr, _ = _compute_rosse_profile(
                t_values, R_func(phi), a_func(phi),
                r0_fn(phi), a0_fn(phi), k_fn(phi),
                r_fn(phi), b_func(phi), m_fn(phi), q_fn(phi),
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

    # Outer wall shell: only when wall_thickness > 0 AND no enclosure box is used.
    # When enc_depth > 0, the enclosure box is built from inner_points directly.
    outer_points = None
    if enc_depth == 0 and wall_thickness > 0:
        outer_points = _compute_outer_points(inner_points, wall_thickness, phi_values)

    return inner_points, outer_points


def _compute_outer_points(
    inner_points: np.ndarray,
    wall_thickness: float,
    phi_values: np.ndarray,
) -> np.ndarray:
    """Offset inner surface by wall_thickness in the 2D profile plane per slice.

    Throat row (j=0): offset is purely radial (XY only), axial z unchanged.
    This matches the JS fix in freestandingWall.js: the outer throat ring sits
    exactly one wall_thickness radially outward from the inner throat ring at the
    same z.  The axial step to z_rear is a separate surface in _build_rear_disc_assembly,
    keeping the outer BSpline surface well-conditioned (no steep throat kink).

    All other rows: normal offset in the full 2D profile plane (axial + radial).
    """
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
        # Single per-slice sign: majority vote of normals_y against the radial
        # outward direction (+y in the 2D profile plane). Mirrors JS
        # resolveOffsetSign() in freestandingWall.js. Per-point flipping
        # causes inconsistent offset directions at the mouth edge due to
        # np.gradient() endpoint artefacts.
        offset_sign = 1.0 if np.sum(normals_y) >= 0.0 else -1.0
        normals_x *= offset_sign
        normals_y *= offset_sign
        x_outer = x_axial + wall_thickness * normals_x
        y_outer = y_radial + wall_thickness * normals_y
        outer_points[i, :, 0] = y_outer * cos_phi
        outer_points[i, :, 1] = y_outer * sin_phi
        outer_points[i, :, 2] = x_outer

        # Throat row (j=0): snap to purely radial offset, same axial z as inner throat.
        # The JS throat row uses only the XZ (radial) component of the surface normal
        # so the outer throat ring is exactly one wall_thickness radially outward from
        # the inner throat ring.  The axial step back to z_rear is a separate surface
        # (built in _build_rear_disc_assembly), keeping the outer BSpline well-conditioned.
        z_throat_i = float(x_axial[0])
        r_throat_i = float(y_radial[0])
        if r_throat_i > 1e-12:
            outer_points[i, 0, 0] = (r_throat_i + wall_thickness) * cos_phi
            outer_points[i, 0, 1] = (r_throat_i + wall_thickness) * sin_phi
        outer_points[i, 0, 2] = z_throat_i  # same z as inner throat, not z_rear

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
    wire = _make_wire(throat_ring_points, closed=closed)
    fill = gmsh.model.occ.addSurfaceFilling(wire)
    return [(2, fill)]


def _build_throat_disc_from_inner_boundary(
    inner_dimtags: List[Tuple[int, int]],
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

    try:
        loop = gmsh.model.occ.addCurveLoop(throat_curves, reorient=True)
    except TypeError:
        loop = gmsh.model.occ.addCurveLoop(throat_curves)
    fill = gmsh.model.occ.addSurfaceFilling(loop)
    return [(2, fill)]


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
    w_inner = _make_wire(inner_points[:, 0, :], closed=closed)
    w_outer = _make_wire(outer_points[:, 0, :], closed=closed)
    return gmsh.model.occ.addThruSections([w_inner, w_outer], makeSolid=False, makeRuled=True)


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
    w_inner = _make_wire(inner_points[:, j_mouth, :], closed=closed)
    w_outer = _make_wire(outer_points[:, j_mouth, :], closed=closed)
    return gmsh.model.occ.addThruSections([w_inner, w_outer], makeSolid=False, makeRuled=True)


def _build_rear_disc_assembly(
    outer_points: np.ndarray, wall_thickness: float, closed: bool
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
    throat_ring = outer_points[:, 0, :].copy()  # (n_phi, 3) at z_throat
    z_rear = float(np.mean(throat_ring[:, 2])) - wall_thickness

    disc_ring = throat_ring.copy()
    disc_ring[:, 2] = z_rear

    # Surface 1: ruled axial step face (outer throat ring → disc ring)
    w_front = _make_wire(throat_ring, closed=closed)
    w_rear = _make_wire(disc_ring, closed=closed)
    annular_dimtags = gmsh.model.occ.addThruSections(
        [w_front, w_rear], makeSolid=False, makeRuled=True
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


def _build_enclosure_box(
    inner_points: np.ndarray, params: dict, closed: bool
) -> Dict[str, Any]:
    """Build enclosure surfaces and classify front/back/sides for mesh-field control."""
    empty = {
        "dimtags": [],
        "front": [],
        "back": [],
        "sides": [],
        "bounds": None,
    }
    if not closed:
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

    # --- Build enclosure box solid ---
    box_tag = gmsh.model.occ.addBox(bx0, by0, z_back, bx1 - bx0, by1 - by0, enc_depth)

    # --- Build mouth-opening prism to cut through the front face ---
    # Project mouth ring to two z planes bracketing z_front so the prism
    # slices cleanly through the box's front face.
    eps = max(enc_depth * 0.01, 0.1)
    mouth_lo = mouth_pts.copy()
    mouth_lo[:, 2] = z_front - eps
    mouth_hi = mouth_pts.copy()
    mouth_hi[:, 2] = z_front + eps

    w_lo = _make_wire(mouth_lo, closed=True)
    w_hi = _make_wire(mouth_hi, closed=True)

    mouth_prism = gmsh.model.occ.addThruSections(
        [w_lo, w_hi], makeSolid=True, makeRuled=True
    )
    mouth_solid_tags = [tag for dim, tag in mouth_prism if dim == 3]

    if not mouth_solid_tags:
        # Fallback: return box surfaces without hole
        gmsh.model.occ.synchronize()
        box_surfs = gmsh.model.getBoundary(
            [(3, box_tag)], oriented=False, combined=False
        )
        dimtags = [(2, tag) for dim, tag in box_surfs if dim == 2]
        split = _classify_enclosure_surfaces(dimtags, z_front, z_back)
        return {
            "dimtags": dimtags,
            "front": split["front"],
            "back": split["back"],
            "sides": split["sides"],
            "bounds": bounds,
        }

    # Boolean difference: box minus mouth prism → box with hole in front face
    result_dimtags, _ = gmsh.model.occ.cut(
        [(3, box_tag)], [(3, mouth_solid_tags[0])]
    )

    # --- Apply fillet or chamfer to mouth-opening edges on the front face ---
    enc_edge = float(params.get("enc_edge", 0) or 0)
    enc_edge_type = int(params.get("enc_edge_type", 1) or 1)
    solid_tags = [tag for dim, tag in result_dimtags if dim == 3]

    if enc_edge > 0 and solid_tags:
        # Synchronize so we can query edge geometry
        gmsh.model.occ.synchronize()

        # Find edges at the mouth opening: those lying on the z_front plane
        # (created by the boolean cut, not the box's own straight edges).
        all_edges = gmsh.model.getBoundary(
            result_dimtags, oriented=False, combined=False, recursive=True
        )
        mouth_edge_tags = []
        front_eps = max(1e-3, abs(z_front) * 1e-4)
        for dim, tag in all_edges:
            if dim != 1:
                continue
            try:
                ex0, ey0, ez0, ex1, ey1, ez1 = gmsh.model.getBoundingBox(dim, tag)
            except Exception:
                continue
            # Edge must lie on the front z-plane
            if abs(ez0 - z_front) > front_eps or abs(ez1 - z_front) > front_eps:
                continue
            # Exclude straight box edges (aligned with a single axis)
            dx = abs(ex1 - ex0)
            dy = abs(ey1 - ey0)
            is_straight = (dx < front_eps or dy < front_eps)
            if is_straight:
                continue
            mouth_edge_tags.append(tag)

        if mouth_edge_tags:
            try:
                if enc_edge_type == 2:
                    # Chamfer: find a front-face surface adjacent to each edge
                    # for the chamfer reference direction.
                    ref_surfs = []
                    for etag in mouth_edge_tags:
                        edge_surfs = gmsh.model.getAdjacencies(1, etag)[1]
                        ref = edge_surfs[0] if len(edge_surfs) > 0 else mouth_edge_tags[0]
                        ref_surfs.append(int(ref))
                    out, _ = gmsh.model.occ.chamfer(
                        solid_tags,
                        mouth_edge_tags,
                        ref_surfs,
                        [enc_edge] * len(mouth_edge_tags),
                    )
                else:
                    # Fillet (rounded)
                    out, _ = gmsh.model.occ.fillet(
                        solid_tags,
                        mouth_edge_tags,
                        [enc_edge],
                    )
                # Update result_dimtags with the new solid(s) from fillet/chamfer
                new_solids = [(d, t) for d, t in out if d == 3]
                if new_solids:
                    result_dimtags = new_solids
            except Exception:
                pass  # If fillet/chamfer fails, proceed with un-filleted geometry

    gmsh.model.occ.synchronize()

    enc_surfaces = gmsh.model.getBoundary(
        result_dimtags, oriented=False, combined=False
    )
    dimtags = [(2, tag) for dim, tag in enc_surfaces if dim == 2]
    split = _classify_enclosure_surfaces(dimtags, z_front, z_back)
    return {
        "dimtags": dimtags,
        "front": split["front"],
        "back": split["back"],
        "sides": split["sides"],
        "bounds": bounds,
    }


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
    Enclosure sides: constant mouth_res.
    Enclosure front/back panels: per-quadrant corner refinement fields.
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

    # Enclosure side walls baseline mouth resolution
    side_field = add_restricted_matheval(
        f"{mouth_res:.6g}",
        surface_groups.get("enclosure_sides", []),
        curve_groups.get("enclosure_sides", []),
    )
    if side_field:
        fields.append(side_field)

    # Enclosure front/back with per-quadrant corner controls
    if enclosure_bounds:
        bx0 = float(enclosure_bounds["bx0"])
        bx1 = float(enclosure_bounds["bx1"])
        by0 = float(enclosure_bounds["by0"])
        by1 = float(enclosure_bounds["by1"])
        z_front = float(enclosure_bounds["z_front"])
        z_back = float(enclosure_bounds["z_back"])
        panel_diag = math.hypot(bx1 - bx0, by1 - by0)
        corner_dist = max(1.0, panel_diag * 0.35)

        front_q = _parse_quadrant_resolutions(enc_front_resolution, mouth_res)
        back_q = _parse_quadrant_resolutions(enc_back_resolution, mouth_res)
        front_base = max(mouth_res, max(front_q))
        back_base = max(mouth_res, max(back_q))

        front_tags = surface_groups.get("enclosure_front", [])
        back_tags = surface_groups.get("enclosure_back", [])
        front_curves = curve_groups.get("enclosure_front", [])
        back_curves = curve_groups.get("enclosure_back", [])

        front_base_field = add_restricted_matheval(
            f"{front_base:.6g}", front_tags, front_curves
        )
        if front_base_field:
            fields.append(front_base_field)

        back_base_field = add_restricted_matheval(
            f"{back_base:.6g}", back_tags, back_curves
        )
        if back_base_field:
            fields.append(back_base_field)

        front_corners = _panel_corner_points_by_quadrant(bx0, bx1, by0, by1, z_front)
        back_corners = _panel_corner_points_by_quadrant(bx0, bx1, by0, by1, z_back)

        corner_points: List[int] = []
        for x, y, z in front_corners + back_corners:
            corner_points.append(gmsh.model.occ.addPoint(float(x), float(y), float(z)))
        if corner_points:
            gmsh.model.occ.synchronize()

        for q_idx in range(4):
            front_point_tag = corner_points[q_idx]
            back_point_tag = corner_points[4 + q_idx]
            front_corner_field = add_restricted_threshold_from_points(
                [front_point_tag],
                front_tags,
                front_curves,
                front_q[q_idx],
                front_base,
                corner_dist,
            )
            if front_corner_field:
                fields.append(front_corner_field)

            back_corner_field = add_restricted_threshold_from_points(
                [back_point_tag],
                back_tags,
                back_curves,
                back_q[q_idx],
                back_base,
                corner_dist,
            )
            if back_corner_field:
                fields.append(back_corner_field)

    if fields:
        f_min = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(f_min, "FieldsList", fields)
        gmsh.model.mesh.field.setAsBackgroundMesh(f_min)

    gmsh.option.setNumber("Mesh.MeshSizeMin", min(throat_res, mouth_res, rear_res) * 0.5)
    gmsh.option.setNumber("Mesh.MeshSizeMax", max(throat_res, mouth_res, rear_res) * 1.5)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)


# ---------------------------------------------------------------------------
# Physical group assignment (ABEC convention)
# ---------------------------------------------------------------------------

def _assign_physical_groups(
    surface_groups: Dict[str, List[int]],
) -> None:
    """Assign ABEC-compatible physical group names to meshed surfaces.

    Tag contract (matching reference mesh convention):
        SD1G0     (1) - all rigid wall surfaces: inner horn + outer shell +
                        rear disc + mouth rim (everything except source disc)
        SD1D1001  (2) - throat source disc (driving element)
        SD2G0     (3) - enclosure box exterior surfaces only (enc_depth > 0 path)

    The outer wall shell, rear disc, and mouth rim are part of SD1G0 in
    free-standing wall mode — same as the reference ABEC/ATH mesh convention.
    SD2G0 is only used for enclosure box exports (enc_depth > 0).
    """
    # All rigid wall surfaces share tag 1 (SD1G0): inner horn + outer shell +
    # rear disc + mouth rim.  This matches the reference .msh topology where
    # the entire connected shell is one physical group.
    wall_tags = (
        surface_groups.get("inner", [])
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

    enclosure_tags = surface_groups.get("enclosure", [])
    if enclosure_tags:
        gmsh.model.addPhysicalGroup(2, enclosure_tags, tag=3)
        gmsh.model.setPhysicalName(2, 3, "SD2G0")


def _extract_triangle_block(
    elem_types: List[int], elem_tags: List[List[int]], elem_nodes: List[List[int]]
) -> Tuple[List[int], List[int]]:
    for i, etype in enumerate(elem_types):
        if int(etype) == 2:
            return [int(tag) for tag in elem_tags[i]], [int(node) for node in elem_nodes[i]]
    return [], []


def _extract_canonical_mesh_from_model(default_surface_tag: int = 1) -> Dict[str, List[float]]:
    """Extract flat canonical mesh arrays (vertices, indices, surfaceTags) from current gmsh model."""
    node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
    if len(node_tags) == 0 or len(node_coords) == 0:
        return {"vertices": [], "indices": [], "surfaceTags": []}

    node_tags_i = [int(tag) for tag in node_tags]
    node_coords_f = [float(v) for v in node_coords]
    node_to_index = {tag: i for i, tag in enumerate(node_tags_i)}

    tri_elem_types_all, tri_elem_tags_all, tri_elem_nodes_all = gmsh.model.mesh.getElements(2)
    tri_tags, tri_nodes = _extract_triangle_block(tri_elem_types_all, tri_elem_tags_all, tri_elem_nodes_all)
    if not tri_tags:
        return {"vertices": node_coords_f, "indices": [], "surfaceTags": []}
    if len(tri_nodes) != len(tri_tags) * 3:
        raise GmshMeshingError("Unexpected triangle node buffer size while extracting canonical mesh.")

    element_surface_tag: Dict[int, int] = {}
    for _, physical_tag in gmsh.model.getPhysicalGroups(2):
        entities = gmsh.model.getEntitiesForPhysicalGroup(2, physical_tag)
        for entity in entities:
            etypes_e, etags_e, enodes_e = gmsh.model.mesh.getElements(2, entity)
            tri_tags_e, _ = _extract_triangle_block(etypes_e, etags_e, enodes_e)
            for elem_tag in tri_tags_e:
                element_surface_tag[int(elem_tag)] = int(physical_tag)

    indices: List[int] = []
    surface_tags: List[int] = []
    for tri_idx, elem_tag in enumerate(tri_tags):
        n0 = tri_nodes[tri_idx * 3]
        n1 = tri_nodes[tri_idx * 3 + 1]
        n2 = tri_nodes[tri_idx * 3 + 2]
        try:
            indices.extend([
                node_to_index[int(n0)],
                node_to_index[int(n1)],
                node_to_index[int(n2)],
            ])
        except KeyError as exc:
            raise GmshMeshingError(
                f"Missing node mapping for triangle element tag {elem_tag}."
            ) from exc
        surface_tags.append(int(element_surface_tag.get(int(elem_tag), default_surface_tag)))

    return {
        "vertices": node_coords_f,
        "indices": indices,
        "surfaceTags": surface_tags,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_waveguide_mesh(params: dict, *, include_canonical: bool = False) -> dict:
    """Build a .msh from ATH parameters using Gmsh OCC Python API.

    Accepts both R-OSSE and OSSE formula types. See WaveguideParamsRequest
    in server/app.py for the full parameter schema.

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
            "Gmsh Python API is not available. Install gmsh: pip install gmsh>=4.15.0"
        )

    formula_type = params.get("formula_type", "R-OSSE")
    if formula_type not in ("R-OSSE", "OSSE"):
        raise ValueError(
            f"Formula type '{formula_type}' is not supported. Use 'R-OSSE' or 'OSSE'."
        )

    enc_depth = float(params.get("enc_depth", 0) or 0)
    msh_version = str(params.get("msh_version", "2.2"))
    # sim_type is passed through to ABEC project files but does not affect geometry
    quadrants = int(params.get("quadrants", 1234))
    closed = (quadrants == 1234)

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
            # Enclosure box must be built and cut BEFORE inner horn surfaces are created
            # to avoid the OCC Boolean cut interfering with the horn surface entities.
            enc_data: Dict[str, Any] = {
                "dimtags": [],
                "front": [],
                "back": [],
                "sides": [],
                "bounds": None,
            }
            if enc_depth > 0:
                enc_data = _build_enclosure_box(inner_points, params, closed=closed)
                # _build_enclosure_box calls occ.synchronize() internally

            inner_dimtags = _build_surface_from_points(inner_points, closed=closed)

            # Synchronize so we can query boundary curves from inner surfaces.
            gmsh.model.occ.synchronize()
            throat_disc_dimtags = (
                _build_throat_disc_from_inner_boundary(inner_dimtags) if closed else []
            )
            if not throat_disc_dimtags and closed:
                # Fallback to the legacy ring-based source disc if boundary extraction fails.
                throat_disc_dimtags = _build_throat_disc_from_ring(inner_points[:, 0, :], closed=closed)

            outer_dimtags = []
            mouth_dimtags = []
            rear_dimtags: List[Tuple[int, int]] = []
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
                    outer_points, wall_thickness, closed=closed
                )

                # Fragment outer shell + mouth rim + rear shell together
                # so coincident boundary edges are merged into shared topology.
                # inner_dimtags is excluded — its BSpline seam periodicity conflicts
                # with re-fragmentation; source disc and inner horn remain the only
                # surfaces sharing the throat boundary.
                all_wall_dimtags = outer_dimtags + mouth_dimtags + rear_shell_dimtags
                n_outer = len(outer_dimtags)
                n_mouth = len(mouth_dimtags)
                n_rear_shell = len(rear_shell_dimtags)
                if len(all_wall_dimtags) > 1:
                    frag_result, frag_map = gmsh.model.occ.fragment(all_wall_dimtags, [])

                    # Rebuild per-group lists from fragment map.
                    # frag_map[i] = output dimtags from the i-th input entity.
                    def _collect_frag(start: int, count: int) -> List[Tuple[int, int]]:
                        out: List[Tuple[int, int]] = []
                        for i in range(start, start + count):
                            if i < len(frag_map):
                                out.extend(dt for dt in frag_map[i] if dt[0] == 2)
                        return out

                    outer_dimtags = _collect_frag(0, n_outer)
                    mouth_dimtags = _collect_frag(n_outer, n_mouth)
                    rear_shell_dimtags = _collect_frag(n_outer + n_mouth, n_rear_shell)
                    rear_dimtags = rear_shell_dimtags
                else:
                    rear_dimtags = rear_shell_dimtags

            # Final synchronize flushes all fragmented entities.
            # Safe to call after enclosure's internal synchronize — it is idempotent.
            gmsh.model.occ.synchronize()

            # Orient surface normals to match ABEC/ATH reference convention:
            #   - Inner horn: normals point INWARD (toward the horn axis / cavity).
            #     Gmsh OCC BSpline surfaces default to outward; setReverse flips them.
            #   - Rear disc: normals point rearward (−z direction).
            # setReverse must be called after synchronize() and before generate().
            if enc_depth == 0 and outer_points is not None:
                all_model_surfaces = {tag for _, tag in gmsh.model.getEntities(2)}
                for _, tag in inner_dimtags:
                    if tag in all_model_surfaces:
                        gmsh.model.mesh.setReverse(2, tag)
                for _, tag in rear_dimtags:
                    if tag in all_model_surfaces:
                        gmsh.model.mesh.setReverse(2, tag)

            # --- Surface groups ---
            surface_groups: Dict[str, List[int]] = {
                "inner": [tag for _, tag in inner_dimtags],
            }
            if closed:
                surface_groups["throat_disc"] = [tag for _, tag in throat_disc_dimtags]
            if enc_depth > 0:
                surface_groups["enclosure"] = [tag for _, tag in enc_data.get("dimtags", [])]
                surface_groups["enclosure_front"] = list(enc_data.get("front", []))
                surface_groups["enclosure_back"] = list(enc_data.get("back", []))
                surface_groups["enclosure_sides"] = list(enc_data.get("sides", []))
            elif outer_points is not None:
                # After fragment, per-group lists track their own post-fragment tags.
                surface_groups["outer"] = [tag for dim, tag in outer_dimtags if dim == 2]
                surface_groups["rear"] = [tag for dim, tag in rear_dimtags if dim == 2]
                surface_groups["mouth"] = [tag for dim, tag in mouth_dimtags if dim == 2]

            # --- Validate surface tags survived synchronize ---
            all_model_surfaces = {tag for _, tag in gmsh.model.getEntities(2)}
            for group_name, tags in surface_groups.items():
                missing = [t for t in tags if t not in all_model_surfaces]
                if missing:
                    print(f"[MWG] WARNING: surface_groups['{group_name}'] has "
                          f"invalid tags {missing} after occ.synchronize()")

            # --- Mesh size fields ---
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
            gmsh.model.mesh.generate(2)
            gmsh.model.mesh.removeDuplicateNodes()
            canonical_mesh = _extract_canonical_mesh_from_model() if include_canonical else None
            if canonical_mesh is not None:
                tri_count = len(canonical_mesh["indices"]) // 3
                if len(canonical_mesh["surfaceTags"]) != tri_count:
                    raise GmshMeshingError(
                        "Canonical extraction surface tag count does not match triangle count."
                    )
                if tri_count > 0 and not any(tag == 2 for tag in canonical_mesh["surfaceTags"]):
                    raise GmshMeshingError("Canonical extraction produced no source-tagged triangles.")

            # --- Write outputs ---
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

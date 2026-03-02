"""
Correct polar directivity computation using BEMPP far-field evaluation.

This module computes physically accurate directivity patterns by:
1. Evaluating BEM solution on a spherical far-field surface
2. Sampling at multiple angles in horizontal/vertical planes
3. Proper normalization and SPL calculation
4. No approximations - uses actual computed field

All observation points are batched into single operator calls for performance.
"""

import logging

import numpy as np
from typing import Dict, List, Optional, Tuple
from .deps import bempp_api

logger = logging.getLogger(__name__)
from .device_interface import is_opencl_buffer_error, potential_device_interface
from .observation import infer_observation_frame, point_from_polar


def evaluate_far_field_sphere(
    grid,
    p_total,
    u_total,
    space_p,
    space_u,
    k: float,
    omega: float,
    rho: float,
    radius_m: float = 2.0,
    theta_range: Tuple[float, float, int] = (0, 180, 37),
    phi_angles: Optional[List[float]] = None,
    device_interface: Optional[str] = None,
    observation_frame: Optional[Dict[str, np.ndarray]] = None,
) -> Dict[str, np.ndarray]:
    """
    Evaluate pressure field on spherical surface at multiple angles.

    All observation points are batched into a single bempp potential operator
    call for performance (2 operator constructions total instead of 2*N).

    Args:
        grid: BEMPP grid
        p_total: Surface pressure solution (GridFunction)
        u_total: Surface velocity solution (GridFunction)
        space_p: Pressure function space
        space_u: Velocity function space
        k: Wavenumber
        omega: Angular frequency
        rho: Air density
        radius_m: Sphere radius in meters (default: 2m for far-field)
        theta_range: (start_deg, end_deg, num_points) for polar angle
        phi_angles: List of azimuth angles in degrees (default: [0] for single plane)

    Returns:
        Dictionary:
        - 'theta_degrees': Array of theta angles
        - 'phi_degrees': Array of phi angles
        - 'pressure': 2D array (len(phi), len(theta)) of complex pressures
        - 'spl': 2D array of SPL in dB
        - 'observation_points': 3D array (len(phi), len(theta), 3) of XYZ coordinates
    """
    frame = observation_frame if isinstance(observation_frame, dict) else infer_observation_frame(grid)
    axis = frame["axis"]
    origin_center = frame["origin_center"]
    u = frame["u"]
    v = frame["v"]

    # Generate theta angles
    theta_start, theta_end, theta_points = theta_range
    theta_points = max(2, int(theta_points))
    theta_deg = np.linspace(theta_start, theta_end, theta_points)
    theta_rad = np.deg2rad(theta_deg)

    # Default phi angles (single plane cut)
    if phi_angles is None:
        phi_angles = [0.0]  # Horizontal cut in the inferred transverse basis

    phi_deg = np.array(phi_angles)
    phi_rad = np.deg2rad(phi_deg)

    n_phi = len(phi_deg)
    n_theta = len(theta_deg)
    n_total = n_phi * n_theta

    # Build all observation points at once: shape (3, N)
    obs_array = np.zeros((3, n_total))
    observation_points = np.zeros((n_phi, n_theta, 3))

    idx = 0
    for i_phi, phi in enumerate(phi_rad):
        for i_theta, theta in enumerate(theta_rad):
            obs_xyz = point_from_polar(
                origin_center=origin_center,
                axis=axis,
                u=u,
                v=v,
                radius_m=radius_m,
                theta_rad=theta,
                phi_rad=phi,
            )
            obs_array[:, idx] = obs_xyz
            observation_points[i_phi, i_theta, :] = obs_xyz
            idx += 1

    # Reference pressure for SPL calculation
    p_ref = 20e-6  # 20 µPa standard reference pressure

    selected_interface = str(device_interface or potential_device_interface()).strip().lower()
    interface_order = [selected_interface]

    pressures = None
    last_error = None
    for interface_name in interface_order:
        try:
            # TWO operator constructions for ALL points (instead of 2*N)
            dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(
                space_p, obs_array, k, device_interface=interface_name
            )
            slp_pot = bempp_api.operators.potential.helmholtz.single_layer(
                space_u, obs_array, k, device_interface=interface_name
            )

            # Evaluate far-field pressure at all points at once
            # P(x) = D*p_total - i*omega*rho_0*S*u_total
            pressure_flat = dlp_pot * p_total - 1j * omega * rho * slp_pot * u_total
            # pressure_flat shape: (1, n_total) — reshape to (n_phi, n_theta)
            pressures = pressure_flat[0, :].reshape(n_phi, n_theta)
            break
        except Exception as exc:
            last_error = exc
            if interface_name == "opencl" and is_opencl_buffer_error(exc):
                logger.warning("[Directivity] OpenCL potential operator failed; no numba fallback is enabled.")
                break
            if interface_name == "opencl":
                logger.warning("[Directivity] OpenCL directivity evaluation failed; no numba fallback is enabled.")
                break
            break

    if pressures is None:
        raise RuntimeError(f"Batched far-field evaluation failed: {last_error}")

    # Calculate SPL
    p_amplitude = np.abs(pressures)
    spl = np.zeros_like(p_amplitude)
    valid_mask = p_amplitude > 1e-15
    spl[valid_mask] = 20 * np.log10(p_amplitude[valid_mask] / p_ref)
    spl[~valid_mask] = -80.0  # Floor for very low pressure

    return {
        'theta_degrees': theta_deg,
        'phi_degrees': phi_deg,
        'pressure': pressures,
        'spl': spl,
        'observation_points': observation_points,
        'radius_m': radius_m
    }


def calculate_directivity_patterns_correct(
    grid,
    frequencies: np.ndarray,
    c: float,
    rho: float,
    p_solutions: List,  # List of (p_total, u_total, space_p, space_u) per frequency
    polar_config: Optional[Dict] = None,
    device_interface: Optional[str] = None,
    observation_frame: Optional[Dict[str, np.ndarray]] = None,
) -> Dict[str, List[List[float]]]:
    """
    Calculate correct directivity patterns using BEM far-field evaluation.

    Enabled polar cuts (horizontal, vertical, diagonal) are evaluated in a
    single batched operator call per frequency for performance.

    Args:
        grid: BEMPP grid
        frequencies: Array of frequencies
        c: Speed of sound
        rho: Air density
        p_solutions: List of solution tuples (p_total, u_total, space_p, space_u) for each frequency
        polar_config: Configuration dict with:
            - angle_range: [start, end, num_points] (default: [0, 180, 37])
            - norm_angle: Normalization angle in degrees (default: 5.0)
            - distance: Measurement distance in meters (default: 2.0)
            - inclination: Inclination angle for diagonal (default: 35.0)
            - enabled_axes: Requested cuts among horizontal|vertical|diagonal

    Returns:
        patterns dict with keys 'horizontal', 'vertical', 'diagonal'
        Each contains list of [[angle, dB], ...] per frequency
    """
    # Parse config
    enabled_axes = {"horizontal", "vertical", "diagonal"}
    if polar_config:
        angle_start, angle_end, angle_points = polar_config.get('angle_range', [0, 180, 37])
        angle_points = int(angle_points)  # Ensure integer for np.linspace
        norm_angle = polar_config.get('norm_angle', 5.0)
        distance_m = polar_config.get('distance', 2.0)
        inclination = polar_config.get('inclination', 35.0)
        raw_enabled_axes = polar_config.get('enabled_axes', ["horizontal", "vertical", "diagonal"])
        parsed_axes = {
            str(axis).strip().lower() for axis in (raw_enabled_axes or []) if str(axis).strip()
        }
        enabled_axes = parsed_axes.intersection({"horizontal", "vertical", "diagonal"}) or enabled_axes
    else:
        angle_start, angle_end, angle_points = 0, 180, 37
        norm_angle = 5.0
        distance_m = 2.0
        inclination = 35.0

    patterns = {
        "horizontal": [],
        "vertical": [],
        "diagonal": []
    }

    if len(p_solutions) == 0:
        logger.warning("[Directivity] No solutions provided, returning empty patterns")
        return patterns

    if len(p_solutions) != len(frequencies):
        logger.error(
            "[Directivity] Solution count (%d) != frequency count (%d)",
            len(p_solutions), len(frequencies),
        )
        return patterns

    axis_phi = {
        "horizontal": 0.0,
        "vertical": 90.0,
        "diagonal": inclination
    }
    active_axes = [axis for axis in ("horizontal", "vertical", "diagonal") if axis in enabled_axes]
    frame = observation_frame if isinstance(observation_frame, dict) else infer_observation_frame(grid)

    # Process each frequency — all requested phi cuts batched into one call
    for freq_idx, freq in enumerate(frequencies):
        k = 2 * np.pi * freq / c
        omega = k * c

        # Unpack solution
        p_total, u_total, space_p, space_u = p_solutions[freq_idx]

        try:
            # Evaluate requested polar cuts in ONE batched call
            result = evaluate_far_field_sphere(
                grid, p_total, u_total, space_p, space_u,
                k, omega, rho,
                radius_m=distance_m,
                theta_range=(angle_start, angle_end, angle_points),
                phi_angles=[axis_phi[axis] for axis in active_axes],
                device_interface=device_interface,
                observation_frame=frame,
            )

            theta = result['theta_degrees']
            for axis_idx, axis in enumerate(active_axes):
                spl_norm = normalize_polar(result['spl'][axis_idx, :], theta, norm_angle)
                axis_pattern = [[float(a), float(db)] for a, db in zip(theta, spl_norm)]
                patterns[axis].append(axis_pattern)

        except Exception as e:
            logger.error("[Directivity] Error computing directivity at %.0f Hz: %s", freq, e, exc_info=True)
            # Preserve shape for this frequency but mark values missing.
            angles = np.linspace(angle_start, angle_end, angle_points)
            placeholder = [[float(a), None] for a in angles]
            for axis in active_axes:
                patterns[axis].append(placeholder)

    return patterns


def normalize_polar(
    spl: np.ndarray,
    theta: np.ndarray,
    norm_angle: float = 5.0
) -> np.ndarray:
    """
    Normalize SPL polar to reference angle.

    Args:
        spl: SPL values at each angle
        theta: Angle values in degrees
        norm_angle: Reference angle for normalization (default: 5°)

    Returns:
        Normalized SPL (0 dB at norm_angle)
    """
    if spl.size == 0 or theta.size == 0:
        return spl

    finite_mask = np.isfinite(spl) & np.isfinite(theta)
    if not np.any(finite_mask):
        return spl

    valid_theta = theta[finite_mask]
    valid_spl = spl[finite_mask]
    order = np.argsort(valid_theta)
    valid_theta = valid_theta[order]
    valid_spl = valid_spl[order]

    # Interpolate reference level at requested normalization angle.
    ref_angle = float(np.clip(norm_angle, valid_theta[0], valid_theta[-1]))
    reference_spl = float(np.interp(ref_angle, valid_theta, valid_spl))
    return spl - reference_spl


def estimate_di_from_ka(
    grid, k: float, observation_frame: Optional[Dict[str, np.ndarray]] = None
) -> float:
    """
    Quick DI estimate from ka (no BEM potential operators needed).

    Uses the standard approximation DI = 10*log10(1 + (ka)^2) which is
    valid for circular pistons and provides a reasonable estimate for horns.

    Args:
        grid: BEMPP grid
        k: Wavenumber

    Returns:
        Approximate Directivity Index in dB
    """
    frame = observation_frame if isinstance(observation_frame, dict) else infer_observation_frame(grid)
    axis = frame["axis"]
    mouth_center = frame["mouth_center"]
    u = frame["u"]
    v = frame["v"]

    vertices = grid.vertices
    rel = vertices - mouth_center.reshape(3, 1)
    axial = axis @ rel
    max_axial = float(np.max(axial))
    min_axial = float(np.min(axial))
    tol = max(1e-6, 0.02 * max(abs(max_axial - min_axial), 1e-6))
    mouth_verts = vertices[:, axial >= (max_axial - tol)]
    if mouth_verts.shape[1] > 0:
        rel_mouth = mouth_verts - mouth_center.reshape(3, 1)
        mouth_radius = float(max(np.max(np.abs(u @ rel_mouth)), np.max(np.abs(v @ rel_mouth))))
        ka = k * mouth_radius
        return float(max(0.0, min(30.0, 10 * np.log10(1 + ka ** 2))))
    return 6.0


def calculate_directivity_index_correct(
    grid,
    k: float,
    c: float,
    rho: float,
    p_total,
    u_total,
    space_p,
    space_u,
    omega: float,
    spl_on_axis: float
) -> float:
    """
    Calculate Directivity Index using hemisphere integration.

    DI = SPL_on_axis - SPL_average
    where SPL_average is the spatial average over a hemisphere.

    Uses batched evaluation for all hemisphere points in a single operator call.

    Args:
        grid: BEMPP grid
        k: Wavenumber
        c: Speed of sound
        rho: Air density
        p_total: Surface pressure solution
        u_total: Surface velocity solution
        space_p: Pressure function space
        space_u: Velocity function space
        omega: Angular frequency
        spl_on_axis: On-axis SPL in dB

    Returns:
        Directivity Index in dB
    """
    # Sample hemisphere (front half)
    theta_points = 9  # 0-90° in ~10° steps
    phi_points = 12  # Around axis

    result = evaluate_far_field_sphere(
        grid, p_total, u_total, space_p, space_u,
        k, omega, rho,
        radius_m=1.0,  # 1m for DI calculation
        theta_range=(0, 90, theta_points),
        phi_angles=list(np.linspace(0, 360, phi_points, endpoint=False))
    )

    # Integrate intensity over hemisphere
    # Solid angle element: dOmega = sin(theta) dtheta dphi
    spl_field = result['spl']  # Shape: (n_phi, n_theta)
    theta_deg = result['theta_degrees']
    theta_rad = np.deg2rad(theta_deg)

    # Convert SPL to intensity
    p_ref = 20e-6
    pressures = p_ref * 10 ** (spl_field / 20)
    intensities = pressures ** 2  # I proportional to p^2

    # Integrate with solid angle weighting
    total_intensity = 0.0
    total_weight = 0.0

    for i_theta, theta in enumerate(theta_rad):
        sin_theta = np.sin(theta)
        # Weight by solid angle
        weight = sin_theta if sin_theta > 0.01 else 0.01

        # Average over phi at this theta
        avg_intensity_at_theta = np.mean(intensities[:, i_theta])

        total_intensity += avg_intensity_at_theta * weight
        total_weight += weight

    if total_weight > 0 and total_intensity > 0:
        avg_intensity = total_intensity / total_weight

        # On-axis intensity
        p_on_axis = p_ref * 10 ** (spl_on_axis / 20)
        i_on_axis = p_on_axis ** 2

        # DI = 10 * log10(I_on_axis / I_average)
        di = 10 * np.log10(i_on_axis / avg_intensity)

        # Clamp to reasonable range
        di = max(0.0, min(di, 30.0))
    else:
        # Fallback: use approximate DI based on ka
        di = estimate_di_from_ka(grid, k)

    return di

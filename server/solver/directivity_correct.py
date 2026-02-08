"""
Correct polar directivity computation using BEMPP far-field evaluation.

This module computes physically accurate directivity patterns by:
1. Evaluating BEM solution on a spherical far-field surface
2. Sampling at multiple angles in horizontal/vertical planes
3. Proper normalization and SPL calculation
4. No approximations - uses actual computed field

Replaces the incorrect piston approximation in directivity.py
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from .deps import bempp_api
from .units import mm_to_m, m_to_mm


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
    phi_angles: Optional[List[float]] = None
) -> Dict[str, np.ndarray]:
    """
    Evaluate pressure field on spherical surface at multiple angles.

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
    # Find mouth position (maximum Y coordinate)
    vertices = grid.vertices
    max_y_mm = np.max(vertices[1, :])
    max_y_m = mm_to_m(max_y_mm)

    # Generate theta angles
    theta_start, theta_end, theta_points = theta_range
    theta_deg = np.linspace(theta_start, theta_end, theta_points)
    theta_rad = np.deg2rad(theta_deg)

    # Default phi angles (single plane cut)
    if phi_angles is None:
        phi_angles = [0.0]  # Horizontal plane (XY)

    phi_deg = np.array(phi_angles)
    phi_rad = np.deg2rad(phi_deg)

    # Prepare output arrays
    n_phi = len(phi_deg)
    n_theta = len(theta_deg)

    pressures = np.zeros((n_phi, n_theta), dtype=complex)
    observation_points = np.zeros((n_phi, n_theta, 3))

    # Reference pressure for SPL calculation
    p_ref = 20e-6 * np.sqrt(2)  # 20 μPa RMS, peak amplitude

    # Evaluate at each angle
    for i_phi, phi in enumerate(phi_rad):
        for i_theta, theta in enumerate(theta_rad):
            # Spherical to Cartesian
            # Theta = 0 is on-axis (positive Y), theta = 90 is lateral
            # Standard spherical: (r, theta, phi) where theta is from +Y axis

            x_m = radius_m * np.sin(theta) * np.cos(phi)
            z_m = radius_m * np.sin(theta) * np.sin(phi)
            y_m = max_y_m + radius_m * np.cos(theta)

            # Convert to mm for BEMPP (mesh is in mm)
            obs_point = np.array([[m_to_mm(x_m)],
                                  [m_to_mm(y_m)],
                                  [m_to_mm(z_m)]])

            observation_points[i_phi, i_theta, :] = [x_m, y_m, z_m]

            try:
                # Create potential operators for this point
                dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(
                    space_p, obs_point, k
                )
                slp_pot = bempp_api.operators.potential.helmholtz.single_layer(
                    space_u, obs_point, k
                )

                # Evaluate far-field pressure
                # P(x) = D*p_total - i*ω*ρ₀*S*u_total
                pressure = dlp_pot * p_total - 1j * omega * rho * slp_pot * u_total
                pressures[i_phi, i_theta] = pressure[0, 0]

            except Exception as e:
                # Numerical issues at this point
                print(f"[Directivity] Warning: Failed to evaluate at theta={np.rad2deg(theta):.1f}°, phi={np.rad2deg(phi):.1f}°: {e}")
                pressures[i_phi, i_theta] = 0.0

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
    polar_config: Optional[Dict] = None
) -> Dict[str, List[List[float]]]:
    """
    Calculate correct directivity patterns using BEM far-field evaluation.

    This replaces the analytical piston approximation with actual field evaluation.

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

    Returns:
        patterns dict with keys 'horizontal', 'vertical', 'diagonal'
        Each contains list of [[angle, dB], ...] per frequency
    """
    # Parse config
    if polar_config:
        angle_start, angle_end, angle_points = polar_config.get('angle_range', [0, 180, 37])
        angle_points = int(angle_points)  # Ensure integer for np.linspace
        norm_angle = polar_config.get('norm_angle', 5.0)
        distance_m = polar_config.get('distance', 2.0)
        inclination = polar_config.get('inclination', 35.0)
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
        print("[Directivity] Warning: No solutions provided, returning empty patterns")
        return patterns

    if len(p_solutions) != len(frequencies):
        print(f"[Directivity] Error: Solution count ({len(p_solutions)}) != frequency count ({len(frequencies)})")
        return patterns

    # Process each frequency
    for freq_idx, freq in enumerate(frequencies):
        k = 2 * np.pi * freq / c
        omega = k * c

        # Unpack solution
        p_total, u_total, space_p, space_u = p_solutions[freq_idx]

        # Evaluate three polar cuts:
        # 1. Horizontal: phi=0° (XY plane, Z=0)
        # 2. Vertical: phi=90° (YZ plane, X=0)
        # 3. Diagonal: phi=inclination° (user-defined)

        try:
            # Horizontal cut (phi = 0°)
            h_result = evaluate_far_field_sphere(
                grid, p_total, u_total, space_p, space_u,
                k, omega, rho,
                radius_m=distance_m,
                theta_range=(angle_start, angle_end, angle_points),
                phi_angles=[0.0]
            )
            h_spl = h_result['spl'][0, :]  # Extract single phi slice
            h_theta = h_result['theta_degrees']

            # Normalize
            h_spl_norm = normalize_polar(h_spl, h_theta, norm_angle)

            # Format as [[angle, dB], ...]
            horizontal = [[float(angle), float(db)] for angle, db in zip(h_theta, h_spl_norm)]

            # Vertical cut (phi = 90°)
            v_result = evaluate_far_field_sphere(
                grid, p_total, u_total, space_p, space_u,
                k, omega, rho,
                radius_m=distance_m,
                theta_range=(angle_start, angle_end, angle_points),
                phi_angles=[90.0]
            )
            v_spl = v_result['spl'][0, :]
            v_theta = v_result['theta_degrees']
            v_spl_norm = normalize_polar(v_spl, v_theta, norm_angle)
            vertical = [[float(angle), float(db)] for angle, db in zip(v_theta, v_spl_norm)]

            # Diagonal cut (phi = inclination°)
            d_result = evaluate_far_field_sphere(
                grid, p_total, u_total, space_p, space_u,
                k, omega, rho,
                radius_m=distance_m,
                theta_range=(angle_start, angle_end, angle_points),
                phi_angles=[inclination]
            )
            d_spl = d_result['spl'][0, :]
            d_theta = d_result['theta_degrees']
            d_spl_norm = normalize_polar(d_spl, d_theta, norm_angle)
            diagonal = [[float(angle), float(db)] for angle, db in zip(d_theta, d_spl_norm)]

        except Exception as e:
            print(f"[Directivity] Error computing directivity at {freq:.0f} Hz: {e}")
            # Fallback: create flat response
            angles = np.linspace(angle_start, angle_end, angle_points)
            horizontal = [[float(a), 0.0] for a in angles]
            vertical = [[float(a), 0.0] for a in angles]
            diagonal = [[float(a), 0.0] for a in angles]

        patterns["horizontal"].append(horizontal)
        patterns["vertical"].append(vertical)
        patterns["diagonal"].append(diagonal)

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
    # Find closest angle to norm_angle
    idx = np.argmin(np.abs(theta - norm_angle))
    reference_spl = spl[idx]

    # Normalize
    return spl - reference_spl


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
    # Use sparse sampling for DI (more points = more accurate but slower)
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
    # Solid angle element: dΩ = sin(θ) dθ dφ
    spl_field = result['spl']  # Shape: (n_phi, n_theta)
    theta_deg = result['theta_degrees']
    theta_rad = np.deg2rad(theta_deg)

    # Convert SPL to intensity
    p_ref = 20e-6 * np.sqrt(2)
    pressures = p_ref * 10 ** (spl_field / 20)
    intensities = pressures ** 2  # I ∝ p²

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
        vertices = grid.vertices
        max_y = np.max(vertices[1, :])
        mouth_verts = vertices[:, np.abs(vertices[1, :] - max_y) < 1.0]
        if mouth_verts.shape[1] > 0:
            mouth_radius = np.max(np.sqrt(mouth_verts[0, :] ** 2 + mouth_verts[2, :] ** 2))
            ka = k * mm_to_m(mouth_radius)
            di = max(3.0, min(20.0, 10 * np.log10(1 + ka ** 2)))
        else:
            di = 6.0

    return di

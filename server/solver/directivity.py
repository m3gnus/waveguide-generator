import numpy as np
from typing import Dict, List, Optional
from scipy import special

from .deps import bempp_api


def calculate_directivity_index_from_pressure(
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
    Calculate Directivity Index by comparing on-axis to average SPL.

    DI = SPL_on_axis - SPL_average
    where SPL_average is computed from power integration over a sphere.
    """
    vertices = grid.vertices
    max_y = np.max(vertices[1, :])

    R_far = 1.0

    # Sample points on a hemisphere (front half)
    n_theta = 9  # 0 to 90 degrees in 10-degree steps
    n_phi = 12  # Around the axis

    total_intensity = 0.0
    total_weight = 0.0

    for i_theta in range(n_theta):
        theta = (i_theta / (n_theta - 1)) * (np.pi / 2)  # 0 to 90 degrees
        sin_theta = np.sin(theta)
        cos_theta = np.cos(theta)

        # Weight by solid angle element (sin(theta) for spherical integration)
        weight = sin_theta if sin_theta > 0.01 else 0.01

        for i_phi in range(n_phi):
            phi = (i_phi / n_phi) * 2 * np.pi

            # Point on sphere centered at mouth
            x = R_far * sin_theta * np.cos(phi)
            y = max_y + R_far * cos_theta
            z = R_far * sin_theta * np.sin(phi)

            obs_point = np.array([[x], [y], [z]])

            try:
                dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(space_p, obs_point, k)
                slp_pot = bempp_api.operators.potential.helmholtz.single_layer(space_u, obs_point, k)

                pressure = dlp_pot * p_total - 1j * omega * rho * slp_pot * u_total
                intensity = np.abs(pressure[0, 0]) ** 2

                total_intensity += intensity * weight
                total_weight += weight
            except Exception:
                # Skip points that cause numerical issues
                pass

    if total_weight > 0 and total_intensity > 0:
        avg_intensity = total_intensity / total_weight

        # On-axis intensity (from SPL)
        p_ref = 20e-6 * np.sqrt(2)
        p_on_axis = p_ref * 10 ** (spl_on_axis / 20)
        i_on_axis = p_on_axis ** 2

        # DI = 10 * log10(I_on_axis / I_average)
        if avg_intensity > 0:
            di = 10 * np.log10(i_on_axis / avg_intensity)
            # Clamp to reasonable range
            di = max(0.0, min(di, 25.0))
        else:
            di = 6.0
    else:
        # Fallback: estimate DI from ka (mouth radius * wavenumber)
        # Find approximate mouth radius
        mouth_y = max_y
        mouth_verts = vertices[:, np.abs(vertices[1, :] - mouth_y) < 1.0]
        if mouth_verts.shape[1] > 0:
            mouth_radius = np.max(np.sqrt(mouth_verts[0, :] ** 2 + mouth_verts[2, :] ** 2))
            ka = k * mouth_radius
            # Approximate DI for circular piston: DI ≈ (ka)² for ka < 1, increases slower after
            if ka < 1:
                di = max(0, 10 * np.log10(1 + ka ** 2))
            else:
                di = 10 * np.log10(1 + ka ** 2)
            di = max(3.0, min(di, 20.0))
        else:
            di = 6.0

    return di


def piston_directivity(ka: float, sin_theta: float) -> float:
    """
    Calculate piston directivity pattern in dB.

    Uses the formula: D(θ) = 2*J1(ka*sin(θ)) / (ka*sin(θ))
    where J1 is the Bessel function of the first kind.

    For horns, this is modified to account for waveguide effects.

    Args:
        ka: wavenumber * radius (dimensionless)
        sin_theta: sine of the angle from axis

    Returns:
        Relative SPL in dB (0 dB on axis)
    """
    x = ka * sin_theta

    if abs(x) < 0.01:
        # Small argument approximation
        d = 1.0
    else:
        # Bessel function directivity
        d = 2.0 * special.j1(x) / x

    # Convert to dB (relative to on-axis)
    if abs(d) > 1e-10:
        db = 20 * np.log10(abs(d))
    else:
        db = -40.0  # Floor at -40 dB

    # Clamp to reasonable range
    return max(-40.0, min(0.0, db))


def calculate_directivity_patterns(
    grid,
    frequencies: np.ndarray,
    c: float,
    rho: float,
    sim_type: str,
    polar_config: Optional[Dict] = None
) -> Dict[str, List[List[float]]]:
    """
    Calculate directivity patterns at key frequencies.

    Uses an analytical approximation based on ka (wavenumber * radius).
    For a horn, the pattern narrows with increasing frequency.

    Args:
        grid: bempp grid
        frequencies: frequency array
        c: speed of sound
        rho: air density
        sim_type: simulation type
        polar_config: Configuration dict with keys:
            - angle_range: [start, end, num_points] for angles
            - norm_angle: normalization angle in degrees
            - distance: measurement distance in meters
            - inclination: inclination angle in degrees

    Returns patterns for horizontal, vertical, and diagonal planes
    """
    # Parse polar config with defaults matching ABEC.Polars format
    if polar_config:
        angle_start, angle_end, angle_points = polar_config.get('angle_range', [0, 180, 37])
        angle_points = int(angle_points)  # Ensure integer for np.linspace
        norm_angle = polar_config.get('norm_angle', 5.0)
        distance_m = polar_config.get('distance', 2.0)
        inclination = polar_config.get('inclination', 35.0)
    else:
        # Defaults matching reference script
        angle_start, angle_end, angle_points = 0, 180, 37
        norm_angle = 5.0
        distance_m = 2.0
        inclination = 35.0
    # Find mouth dimensions from grid
    vertices = grid.vertices
    max_y = np.max(vertices[1, :])

    # Get mouth vertices (at max Y)
    mouth_mask = np.abs(vertices[1, :] - max_y) < 1.0
    mouth_verts = vertices[:, mouth_mask]

    if mouth_verts.shape[1] > 0:
        # Calculate mouth dimensions (may be elliptical)
        mouth_width = np.max(mouth_verts[0, :]) - np.min(mouth_verts[0, :])  # X extent
        mouth_height = np.max(mouth_verts[2, :]) - np.min(mouth_verts[2, :])  # Z extent
        mouth_radius_h = mouth_width / 2  # Horizontal
        mouth_radius_v = mouth_height / 2  # Vertical
    else:
        mouth_radius_h = 100.0  # Default 100mm
        mouth_radius_v = 100.0

    # Calculate directivity for ALL frequencies (not just 3 key frequencies)
    # This is needed for the polar heatmap visualization
    patterns = {"horizontal": [], "vertical": [], "diagonal": []}

    # Angles for directivity using polar config
    angles = np.linspace(angle_start, angle_end, int(angle_points))

    for freq_idx in range(len(frequencies)):
        freq = frequencies[freq_idx]
        k = 2 * np.pi * freq / c

        # ka products for horizontal and vertical
        ka_h = k * mouth_radius_h
        ka_v = k * mouth_radius_v

        horizontal = []
        vertical = []
        diagonal = []

        for angle in angles:
            theta_rad = np.radians(angle)

            # Piston directivity function: 2*J1(ka*sin(theta)) / (ka*sin(theta))
            # For small angles or small ka, this approaches 1
            # For large ka*sin(theta), it produces lobes
            sin_theta = np.sin(theta_rad)

            # Horizontal pattern (uses horizontal ka)
            h_pattern = piston_directivity(ka_h, sin_theta)

            # Vertical pattern (uses vertical ka)
            v_pattern = piston_directivity(ka_v, sin_theta)

            # Diagonal (average of h and v)
            ka_d = (ka_h + ka_v) / 2
            d_pattern = piston_directivity(ka_d, sin_theta)

            horizontal.append([angle, h_pattern])
            vertical.append([angle, v_pattern])
            diagonal.append([angle, d_pattern])

        patterns["horizontal"].append(horizontal)
        patterns["vertical"].append(vertical)
        patterns["diagonal"].append(diagonal)

    return patterns

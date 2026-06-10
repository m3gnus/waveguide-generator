"""Directivity Index computation from solved polar patterns."""

import numpy as np


def calculate_di_from_polar_patterns(directivity_patterns):
    """
    Calculate Directivity Index from actual polar directivity patterns.

    For each plane and frequency, computes DI by integrating the normalized
    pressure pattern over the full polar range using sin(θ) weighting:

        DI = 10 * log10(2 / ∫₀^π p²(θ) sin(θ) dθ)

    where p(θ) is the normalized linear pressure (on-axis = 1, i.e. 0 dB).
    The factor of 2 assumes axial symmetry (the φ integral gives 2π,
    and 4π / 2π = 2).

    Args:
        directivity_patterns: Dict mapping plane_id to list of patterns.
            Each pattern is a list of [angle_deg, normalized_dB] pairs.
            Example: {"horizontal": [[[0, 0.0], [5, -1.2], ...], ...]}

    Returns:
        Dict mapping plane_id to list of DI values (one per frequency).
        Example: {"horizontal": [3.2, 4.5, ...], "vertical": [...]}
    """
    di_per_plane = {}

    for plane_id, patterns in directivity_patterns.items():
        di_values = []
        for pattern in patterns:
            if pattern is None or not pattern:
                di_values.append(None)
                continue

            angles_deg = []
            norm_db = []
            for point in pattern:
                if point is None or len(point) < 2 or point[1] is None:
                    continue
                angles_deg.append(float(point[0]))
                norm_db.append(float(point[1]))

            if len(angles_deg) < 3:
                di_values.append(None)
                continue

            angles_rad = np.deg2rad(angles_deg)
            # Convert normalized dB back to linear pressure ratio
            p_linear = 10.0 ** (np.array(norm_db) / 20.0)

            # Trapezoidal integration of p²(θ) sin(θ) dθ over full range
            integrand = p_linear ** 2 * np.sin(angles_rad)
            # Handle θ=0 (sin=0) and θ=π (sin=0) gracefully
            integral = float(np.trapz(integrand, angles_rad))

            if integral > 0:
                # DI = 10 * log10(2 / integral)
                # Factor of 2 from axisymmetric assumption: 4π / (2π) = 2
                di = 10.0 * np.log10(2.0 / integral)
                di = max(0.0, di)
            else:
                di = 0.0

            di_values.append(float(di))

        di_per_plane[plane_id] = di_values

    return di_per_plane

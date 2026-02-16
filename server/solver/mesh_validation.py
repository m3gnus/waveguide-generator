"""
Frequency-adaptive mesh validation and coarsening.

Ensures mesh resolution is appropriate for frequency range:
- Validates element size against wavelength (≥6-10 elements per wavelength)
- Determines maximum valid frequency for given mesh
- Provides warnings when frequency exceeds mesh capability
"""

import numpy as np
from typing import Dict, Tuple, Optional


def calculate_mesh_statistics(
    vertices: np.ndarray,
    indices: np.ndarray
) -> Dict[str, float]:
    """
    Calculate mesh quality statistics.

    Args:
        vertices: Vertex array (3, N)
        indices: Triangle indices (3, M) or (M, 3)

    Returns:
        Dictionary with mesh statistics:
        - min_edge_length: Minimum edge length
        - max_edge_length: Maximum edge length
        - mean_edge_length: Mean edge length
        - median_edge_length: Median edge length
        - num_elements: Number of triangular elements
    """
    # Reshape indices if needed
    if indices.ndim == 1:
        indices = indices.reshape(-1, 3)
    elif indices.shape[0] == 3:
        indices = indices.T

    edge_lengths = []

    for tri in indices:
        if np.any(tri >= vertices.shape[1]):
            continue

        # Get vertices of triangle
        v0 = vertices[:, tri[0]]
        v1 = vertices[:, tri[1]]
        v2 = vertices[:, tri[2]]

        # Calculate edge lengths
        e0 = np.linalg.norm(v1 - v0)
        e1 = np.linalg.norm(v2 - v1)
        e2 = np.linalg.norm(v0 - v2)

        edge_lengths.extend([e0, e1, e2])

    edge_lengths = np.array(edge_lengths)

    return {
        'min_edge_length': np.min(edge_lengths),
        'max_edge_length': np.max(edge_lengths),
        'mean_edge_length': np.mean(edge_lengths),
        'median_edge_length': np.median(edge_lengths),
        'num_elements': indices.shape[0]
    }


def calculate_max_valid_frequency(
    mesh_stats: Dict[str, float],
    c: float = 343.0,
    elements_per_wavelength: float = 6.0
) -> float:
    """
    Calculate maximum frequency for which mesh has adequate resolution.

    Rule of thumb: Need at least 6-10 elements per wavelength for BEM.

    Args:
        mesh_stats: Dictionary from calculate_mesh_statistics
        c: Speed of sound in m/s
        elements_per_wavelength: Minimum elements per wavelength (default: 6)

    Returns:
        Maximum valid frequency in Hz
    """
    # Use maximum edge length to be conservative
    # (worst case: large elements limit high frequency accuracy)
    max_edge_m = mesh_stats['max_edge_length']  # in meters

    # Wavelength must be at least elements_per_wavelength * max_edge
    min_wavelength = elements_per_wavelength * max_edge_m

    # Frequency = c / wavelength
    max_freq = c / min_wavelength

    return max_freq


def validate_frequency_range(
    mesh_stats: Dict[str, float],
    frequency_range: Tuple[float, float],
    c: float = 343.0,
    elements_per_wavelength: float = 6.0,
    warn_threshold: float = 0.8
) -> Dict[str, any]:
    """
    Validate that mesh resolution is adequate for frequency range.

    Args:
        mesh_stats: Mesh statistics dictionary
        frequency_range: (min_freq, max_freq) in Hz
        c: Speed of sound in m/s
        elements_per_wavelength: Required elements per wavelength
        warn_threshold: Warn if max_freq > threshold * max_valid_freq

    Returns:
        Validation result dictionary:
        - is_valid: bool
        - max_valid_frequency: float
        - recommended_max_frequency: float
        - warnings: List[str]
        - elements_per_wavelength_at_max: float
    """
    min_freq, max_freq = frequency_range

    max_valid = calculate_max_valid_frequency(
        mesh_stats, c, elements_per_wavelength
    )

    # Calculate actual elements per wavelength at requested max frequency
    wavelength_at_max = c / max_freq
    elements_at_max = wavelength_at_max / mesh_stats['max_edge_length']

    warnings = []
    recommendations = []
    is_valid = True

    # Recommended frequency is 80% of theoretical max (safety margin)
    recommended_max = max_valid * 0.8

    if max_freq > max_valid:
        is_valid = False
        warnings.append(
            f"Requested max frequency ({max_freq:.0f} Hz) exceeds mesh capability "
            f"({max_valid:.0f} Hz). Results above {max_valid:.0f} Hz will be inaccurate."
        )
        warnings.append(
            f"Mesh has only {elements_at_max:.1f} elements per wavelength at {max_freq:.0f} Hz "
            f"(need ≥{elements_per_wavelength:.0f})."
        )

        # Calculate target element size
        target_wavelength = c / max_freq
        target_edge = target_wavelength / elements_per_wavelength

        recommendations.append(
            f"RECOMMENDED: Refine mesh to ~{target_edge:.5f} m max element size "
            f"(use use_gmsh=True with target_frequency={max_freq:.0f})"
        )
        recommendations.append(
            f"ALTERNATIVE: Reduce max frequency to {recommended_max:.0f} Hz "
            f"(80% safety margin)"
        )
        recommendations.append(
            f"You can proceed with current mesh - invalid frequencies will be filtered automatically"
        )

    elif max_freq > warn_threshold * max_valid:
        warnings.append(
            f"Max frequency ({max_freq:.0f} Hz) is close to mesh limit "
            f"({max_valid:.0f} Hz). Consider refining mesh for better accuracy."
        )
        recommendations.append(
            f"OPTIONAL: Refine mesh for improved accuracy at high frequencies"
        )
        recommendations.append(
            f"Current mesh is adequate - safe to proceed"
        )

    return {
        'is_valid': is_valid,
        'max_valid_frequency': max_valid,
        'recommended_max_frequency': recommended_max,
        'warnings': warnings,
        'recommendations': recommendations,
        'elements_per_wavelength_at_max': elements_at_max,
        'mesh_stats': mesh_stats
    }


def suggest_target_refinement(
    current_max_freq: float,
    desired_max_freq: float,
    current_mesh_stats: Dict[str, float],
    c: float = 343.0,
    elements_per_wavelength: float = 6.0
) -> Dict[str, any]:
    """
    Suggest mesh refinement target to reach desired frequency.

    Args:
        current_max_freq: Current maximum valid frequency
        desired_max_freq: Desired maximum frequency
        current_mesh_stats: Current mesh statistics
        c: Speed of sound
        elements_per_wavelength: Required elements per wavelength

    Returns:
        Refinement suggestion dictionary:
        - target_edge_length: Target maximum edge length in meters
        - refinement_factor: Approximate refinement factor needed
        - estimated_elements: Estimated number of elements after refinement
    """
    if desired_max_freq <= current_max_freq:
        return {
            'target_edge_length': current_mesh_stats['max_edge_length'],
            'refinement_factor': 1.0,
            'estimated_elements': current_mesh_stats['num_elements']
        }

    # Required wavelength at desired frequency
    wavelength_m = c / desired_max_freq

    # Target edge length
    target_edge = wavelength_m / elements_per_wavelength

    # Refinement factor (linear dimension)
    refinement_factor = current_mesh_stats['max_edge_length'] / target_edge

    # Elements scale as refinement_factor^2 (2D surface)
    estimated_elements = current_mesh_stats['num_elements'] * (refinement_factor ** 2)

    return {
        'target_edge_length': target_edge,
        'refinement_factor': refinement_factor,
        'estimated_elements': int(estimated_elements)
    }


def filter_frequencies_by_mesh_capability(
    frequencies: np.ndarray,
    max_valid_frequency: float,
    safety_factor: float = 0.9
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Split frequencies into valid and invalid ranges based on mesh capability.

    Args:
        frequencies: Array of frequencies to simulate
        max_valid_frequency: Maximum frequency mesh can accurately handle
        safety_factor: Conservative factor (default: 0.9 = use 90% of max)

    Returns:
        (valid_frequencies, invalid_frequencies)
    """
    threshold = max_valid_frequency * safety_factor

    valid_mask = frequencies <= threshold
    invalid_mask = ~valid_mask

    return frequencies[valid_mask], frequencies[invalid_mask]


def estimate_simulation_cost(
    mesh_stats: Dict[str, float],
    num_frequencies: int,
    symmetry_reduction: float = 1.0
) -> Dict[str, any]:
    """
    Estimate computational cost of simulation.

    Args:
        mesh_stats: Mesh statistics
        num_frequencies: Number of frequency points
        symmetry_reduction: Reduction factor from symmetry (1=full, 2=half, 4=quarter)

    Returns:
        Cost estimate dictionary:
        - effective_elements: Number of elements after symmetry reduction
        - cost_per_frequency: Relative cost per frequency
        - total_cost: Relative total cost
        - estimated_seconds: Rough time estimate
    """
    # Effective problem size after symmetry
    effective_elements = mesh_stats['num_elements'] / symmetry_reduction

    # BEM cost scales as O(N^2) without acceleration, O(N log N) with FMM
    # Assume FMM is enabled (bempp-cl default for newer versions)
    cost_per_frequency = effective_elements * np.log(effective_elements)

    total_cost = cost_per_frequency * num_frequencies

    # Rough time estimate (calibrated for typical desktop CPU)
    # ~1 second per 1000 elements per frequency with FMM
    estimated_seconds = (effective_elements / 1000.0) * num_frequencies * 1.5

    return {
        'effective_elements': int(effective_elements),
        'cost_per_frequency': cost_per_frequency,
        'total_cost': total_cost,
        'estimated_seconds': estimated_seconds,
        'estimated_minutes': estimated_seconds / 60.0
    }


def print_mesh_validation_report(
    validation: Dict,
    frequency_range: Tuple[float, float],
    num_frequencies: int,
    symmetry_factor: float = 1.0,
    verbose: bool = True
) -> None:
    """
    Print comprehensive mesh validation report.

    Args:
        validation: Validation result from validate_frequency_range
        frequency_range: (min_freq, max_freq) in Hz
        num_frequencies: Number of frequency points
        symmetry_factor: Symmetry reduction factor
        verbose: Print detailed statistics
    """
    if not verbose:
        # Print only warnings
        for warning in validation['warnings']:
            print(f"[Mesh] WARNING: {warning}")
        return

    print("\n" + "="*70)
    print("MESH VALIDATION REPORT")
    print("="*70)

    stats = validation['mesh_stats']
    print(f"\nMesh Statistics:")
    print(f"  Elements: {stats['num_elements']}")
    print(f"  Edge length: {stats['min_edge_length']:.5f} - {stats['max_edge_length']:.5f} m")
    print(f"  Mean edge: {stats['mean_edge_length']:.5f} m")

    if symmetry_factor > 1.0:
        print(f"\nSymmetry Reduction: {symmetry_factor:.1f}× → {int(stats['num_elements']/symmetry_factor)} effective elements")

    print(f"\nFrequency Range: {frequency_range[0]:.0f} - {frequency_range[1]:.0f} Hz ({num_frequencies} points)")

    print(f"\nMesh Capability:")
    print(f"  Max valid frequency: {validation['max_valid_frequency']:.0f} Hz")
    print(f"  Recommended max: {validation['recommended_max_frequency']:.0f} Hz (80% of limit)")
    print(f"  Elements/wavelength @ {frequency_range[1]:.0f} Hz: {validation['elements_per_wavelength_at_max']:.1f}")

    cost = estimate_simulation_cost(stats, num_frequencies, symmetry_factor)
    print(f"\nEstimated Runtime: {cost['estimated_minutes']:.1f} minutes")

    if len(validation['warnings']) > 0:
        print(f"\nWARNINGS:")
        for warning in validation['warnings']:
            print(f"  ⚠ {warning}")

        # Show recommendations
        if 'recommendations' in validation and len(validation['recommendations']) > 0:
            print(f"\nRECOMMENDATIONS:")
            for rec in validation['recommendations']:
                print(f"  → {rec}")
    else:
        print(f"\n✓ Mesh resolution is adequate for requested frequency range")

    print("="*70 + "\n")

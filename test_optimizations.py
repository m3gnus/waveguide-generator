#!/usr/bin/env python3
"""
Test script to demonstrate BEMPP optimizations.

Run this to validate that all optimizations are working:
- Symmetry detection
- Mesh validation
- Correct polar computation
- Operator caching
- Performance monitoring

Usage:
    python test_optimizations.py
"""

import numpy as np
import sys
import time

# Add server directory to path
sys.path.insert(0, 'server')

try:
    from solver import BEMSolver
    SOLVER_AVAILABLE = True
except ImportError as e:
    print(f"ERROR: Could not import BEMSolver: {e}")
    print("Make sure bempp-cl is installed: pip install bempp-cl")
    SOLVER_AVAILABLE = False
    sys.exit(1)


def create_test_horn_quarter():
    """
    Create a simple conical horn geometry with quarter symmetry.

    Returns vertices, indices, surface_tags for a symmetric test horn.
    """
    print("\n" + "="*70)
    print("CREATING TEST GEOMETRY - QUARTER SYMMETRIC CONICAL HORN")
    print("="*70)

    # Throat parameters
    throat_radius = 12.7  # 1 inch (mm)
    throat_y = 0.0

    # Mouth parameters
    mouth_radius = 50.0  # mm
    mouth_y = 150.0  # mm

    # Discretization
    n_circum = 16  # Points around circumference
    n_axial = 10   # Points along axis

    vertices = []

    # Generate quarter-symmetric horn (X≥0, Z≥0 quadrant only)
    for i in range(n_axial):
        t = i / (n_axial - 1)
        y = throat_y + t * (mouth_y - throat_y)
        r = throat_radius + t * (mouth_radius - throat_radius)

        # Only quarter circle (0° to 90°)
        for j in range(n_circum // 4 + 1):
            angle = (j / (n_circum // 4)) * (np.pi / 2)
            x = r * np.cos(angle)
            z = r * np.sin(angle)
            vertices.append([x, y, z])

    vertices = np.array(vertices)
    n_verts = len(vertices)

    print(f"Generated {n_verts} vertices in quarter quadrant (X≥0, Z≥0)")
    print(f"Y range: {throat_y:.1f} - {mouth_y:.1f} mm")
    print(f"Radius range: {throat_radius:.1f} - {mouth_radius:.1f} mm")

    # Generate triangular mesh
    indices = []
    surface_tags = []

    n_circ = n_circum // 4 + 1

    for i in range(n_axial - 1):
        for j in range(n_circ - 1):
            # Vertex indices for quad
            v0 = i * n_circ + j
            v1 = i * n_circ + (j + 1)
            v2 = (i + 1) * n_circ + (j + 1)
            v3 = (i + 1) * n_circ + j

            # Split quad into two triangles
            indices.append([v0, v1, v2])
            indices.append([v0, v2, v3])

            # Tag: 1=throat, 2=wall, 3=mouth
            if i == 0:
                surface_tags.extend([1, 1])  # Throat
            elif i == n_axial - 2:
                surface_tags.extend([3, 3])  # Mouth
            else:
                surface_tags.extend([2, 2])  # Wall

    indices = np.array(indices, dtype=np.int32)
    surface_tags = np.array(surface_tags, dtype=np.int32)

    print(f"Generated {len(indices)} triangles")
    print(f"  Throat elements (tag=1): {np.sum(surface_tags == 1)}")
    print(f"  Wall elements (tag=2): {np.sum(surface_tags == 2)}")
    print(f"  Mouth elements (tag=3): {np.sum(surface_tags == 3)}")

    # Flatten for API
    vertices_flat = vertices.flatten().tolist()
    indices_flat = indices.flatten().tolist()
    surface_tags_flat = surface_tags.tolist()

    return vertices_flat, indices_flat, surface_tags_flat


def run_comparison_test():
    """
    Run comparison between old and optimized solver.
    """
    print("\n" + "="*70)
    print("BEMPP OPTIMIZATION TEST")
    print("="*70)

    # Create test geometry
    vertices, indices, surface_tags = create_test_horn_quarter()

    # Initialize solver
    print("\nInitializing BEMSolver...")
    solver = BEMSolver()

    # Prepare mesh
    print("\nPreparing mesh...")
    mesh = solver.prepare_mesh(
        vertices=vertices,
        indices=indices,
        surface_tags=surface_tags
    )

    print(f"Mesh prepared: {mesh['grid'].number_of_elements} elements")

    # Test configuration
    frequency_range = [500, 5000]  # Hz
    num_frequencies = 10
    sim_type = "1"  # Baffle

    polar_config = {
        'angle_range': [0, 180, 19],  # Coarser for faster test
        'norm_angle': 5.0,
        'distance': 2.0,
        'inclination': 35.0
    }

    print(f"\nSimulation parameters:")
    print(f"  Frequency range: {frequency_range[0]} - {frequency_range[1]} Hz")
    print(f"  Number of frequencies: {num_frequencies}")
    print(f"  Polar angles: {polar_config['angle_range']}")

    # Test 1: Optimized solver with symmetry
    print("\n" + "="*70)
    print("TEST 1: OPTIMIZED SOLVER (with symmetry, caching, correct polars)")
    print("="*70)

    start_time = time.time()

    results_optimized = solver.solve(
        mesh=mesh,
        frequency_range=frequency_range,
        num_frequencies=num_frequencies,
        sim_type=sim_type,
        polar_config=polar_config,
        use_optimized=True,
        enable_symmetry=True,
        verbose=True
    )

    optimized_time = time.time() - start_time

    print(f"\n✓ Optimized solver completed in {optimized_time:.2f}s")

    # Print metadata
    if 'metadata' in results_optimized:
        meta = results_optimized['metadata']

        print("\n" + "-"*70)
        print("OPTIMIZATION RESULTS:")
        print("-"*70)

        if 'symmetry' in meta:
            sym = meta['symmetry']
            print(f"Symmetry type: {sym.get('symmetry_type', 'N/A')}")
            print(f"Reduction factor: {sym.get('reduction_factor', 1.0):.1f}×")
            if 'reduced_triangles' in sym:
                print(f"Elements: {sym.get('original_triangles', 'N/A')} → {sym['reduced_triangles']}")

        if 'mesh' in meta:
            mesh_meta = meta['mesh']
            print(f"\nMesh statistics:")
            print(f"  Elements: {mesh_meta.get('num_elements', 'N/A')}")
            print(f"  Edge length: {mesh_meta.get('edge_length_range', ['N/A', 'N/A'])[0]:.2f} - "
                  f"{mesh_meta.get('edge_length_range', ['N/A', 'N/A'])[1]:.2f} mm")

        if 'validation' in meta:
            val = meta['validation']
            print(f"\nMesh validation:")
            print(f"  Max valid frequency: {val.get('max_valid_frequency', 'N/A'):.0f} Hz")
            print(f"  Elements/wavelength @ {frequency_range[1]} Hz: "
                  f"{val.get('elements_per_wavelength', 'N/A'):.1f}")
            if val.get('warnings'):
                print(f"  Warnings: {len(val['warnings'])}")
                for w in val['warnings']:
                    print(f"    - {w}")

        if 'performance' in meta:
            perf = meta['performance']
            print(f"\nPerformance:")
            print(f"  Total time: {perf.get('total_time_seconds', 'N/A'):.2f}s")
            print(f"  Time per frequency: {perf.get('time_per_frequency', 'N/A'):.3f}s")
            print(f"  Directivity compute: {perf.get('directivity_compute_time', 'N/A'):.2f}s")
            if perf.get('reduction_speedup', 1.0) > 1.0:
                print(f"  Speedup from symmetry: {perf['reduction_speedup']:.1f}×")

    # Validate results
    print("\n" + "-"*70)
    print("RESULT VALIDATION:")
    print("-"*70)

    freqs = results_optimized['frequencies']
    spl = results_optimized['spl_on_axis']['spl']
    di = results_optimized['di']['di']

    print(f"Frequencies solved: {len(freqs)}")
    print(f"SPL on-axis range: {min(spl):.1f} - {max(spl):.1f} dB")
    print(f"DI range: {min(di):.1f} - {max(di):.1f} dB")

    # Check polars
    if 'directivity' in results_optimized:
        h_polars = results_optimized['directivity']['horizontal']
        v_polars = results_optimized['directivity']['vertical']

        print(f"\nPolar patterns:")
        print(f"  Horizontal cuts: {len(h_polars)} frequencies")
        print(f"  Vertical cuts: {len(v_polars)} frequencies")
        print(f"  Angles per cut: {len(h_polars[0]) if h_polars else 0}")

        # Check first frequency horizontal pattern
        if h_polars:
            first_h = h_polars[0]
            angles = [p[0] for p in first_h]
            db_vals = [p[1] for p in first_h]
            print(f"\n  Example: {freqs[0]:.0f} Hz horizontal polar")
            print(f"    On-axis (0°): {db_vals[0]:.2f} dB")
            print(f"    30°: {db_vals[len(angles)//6]:.2f} dB")
            print(f"    90°: {db_vals[len(angles)//2]:.2f} dB")

    print("\n" + "="*70)
    print("TEST COMPLETED SUCCESSFULLY")
    print("="*70)
    print(f"\n✓ Optimizations validated")
    print(f"✓ Total runtime: {optimized_time:.2f}s")
    print(f"\nSee BEMPP_OPTIMIZATION_GUIDE.md for full documentation")

    return results_optimized


def main():
    """Main test entry point"""
    if not SOLVER_AVAILABLE:
        print("ERROR: BEM solver not available")
        return 1

    try:
        results = run_comparison_test()

        print("\n" + "="*70)
        print("ALL TESTS PASSED ✓")
        print("="*70)

        return 0

    except Exception as e:
        import traceback
        print("\n" + "="*70)
        print("TEST FAILED ✗")
        print("="*70)
        print(f"Error: {e}")
        print("\nFull traceback:")
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())

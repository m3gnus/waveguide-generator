import { describe, expect, it } from 'vitest';
import { solverMeshArtifactToken, type SolverMeshResult } from '../api/solverMesh';
import meshFixture from './test-fixtures/tagged_sources-small.msh?raw';
import { solverMeshScene } from './solverMeshScene';

const result: SolverMeshResult = {
  plan: { engine: 'metal', formulation: 'full-3d', reason: 'test', eligibility_reasons: [] },
  msh_text: meshFixture,
  stats: {
    triangle_count: 12,
    vertex_count: 9,
    warnings: [],
    mesh_cache_key: 'abc123',
    mesh_cache_hit: false,
  },
  cut_planes: ['x0', 'y0'],
  quadrants: 1,
};

describe('solverMeshScene', () => {
  it('retains the artifact measurements for the Mesh card', () => {
    const scene = solverMeshScene('Measured mesh', { ...result, stats: { ...result.stats,
      surface_deviation: { peak_mm: 0.34, triangle_count: 12, sampled_triangle_count: 12 },
      element_shape: { p1_radius_ratio: 0.72 },
    } });
    expect(scene.tessellation?.surface_deviation?.peak_mm).toBe(0.34);
    expect(scene.tessellation?.surface_deviation_mm).toBeNull();
    expect(scene.tessellation?.element_shape?.p1_radius_ratio).toBe(0.72);
  });
  it('retains the native upper-bound distinction', () => {
    const scene = solverMeshScene('Certified mesh', { ...result, stats: { ...result.stats,
      surface_deviation: { peak_mm: 0.004, peak_is_upper_bound: true, triangle_count: 12, sampled_triangle_count: 0 },
    } });
    expect(scene.tessellation?.surface_deviation?.peak_is_upper_bound).toBe(true);
  });
  it('parses the artifact and reconstructs the full model across the cut planes', () => {
    const scene = solverMeshScene('Simulation mesh', result);
    expect(scene.source).toBe('solver');
    expect(scene.artifactToken).toBe(solverMeshArtifactToken('abc123'));
    expect(scene.solvedTriangleCount).toBe(12);
    // Two origin cuts mirror the reduced domain up to fourfold; triangles on a
    // cut plane are not duplicated, so the display count lies in between.
    expect(scene.triangleCount).toBeGreaterThan(0);
    const parsedTriangles = scene.scene.surfaces
      .filter((surface) => surface.solvedDomain)
      .reduce((count, surface) => count + surface.indices.length / 3, 0);
    expect(scene.triangleCount).toBeGreaterThanOrEqual(parsedTriangles);
    // The solver's reduced domain stays marked; mirrored copies are display-only.
    expect(scene.scene.surfaces.some((surface) => surface.solvedDomain)).toBe(true);
    expect(scene.scene.surfaces.some((surface) => !surface.solvedDomain)).toBe(true);
  });
});

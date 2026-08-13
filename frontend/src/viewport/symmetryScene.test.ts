import { Box3, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import type { FrameScene } from './frameScene';
import { expandImportedSymmetry, markParametricSolvedDomain, quadrantsForSolveMode } from './symmetryScene';
import type { SceneSurface } from './types';

function surface(positions: number[], indices: number[]): SceneSurface {
  return {
    key: 'surface', role: 'horn.inner', shading: 'smooth', materialClass: 'horn-smooth', enclosure: false,
    positions: Float32Array.from(positions),
    normals: Float32Array.from(positions.map((_value, index) => index % 3 === 2 ? 1 : 0)),
    indices: Uint32Array.from(indices), curvature: null,
  };
}

function scene(value: SceneSurface): FrameScene {
  return { surfaces: [value], bounds: new Box3(new Vector3(0, 0, 0), new Vector3(1, 1, 0)), hasCurvature: false };
}

describe('symmetry display geometry', () => {
  it('reflects a quarter solver mesh into a full model and reverses reflected winding', () => {
    const expanded = expandImportedSymmetry(scene(surface(
      [0, 0, 0, 1, 0, 0, 0, 1, 0],
      [0, 1, 2],
    )), ['x0', 'y0']);

    expect(expanded.surfaces).toHaveLength(4);
    expect(expanded.surfaces.filter((item) => item.solvedDomain)).toHaveLength(1);
    expect(expanded.surfaces.reduce((count, item) => count + item.indices.length / 3, 0)).toBe(4);
    expect(expanded.bounds.min.toArray()).toEqual([-1, -1, 0]);
    expect(expanded.bounds.max.toArray()).toEqual([1, 1, 0]);
    expect([...expanded.surfaces[1].indices]).toEqual([0, 2, 1]);
  });

  it('marks just Q1 for a quarter solve while retaining all full-model triangles', () => {
    const full = scene(surface(
      [
        1, 1, 0, 2, 1, 0, 1, 2, 0,
        -1, 1, 0, -2, 1, 0, -1, 2, 0,
        -1, -1, 0, -2, -1, 0, -1, -2, 0,
        1, -1, 0, 2, -1, 0, 1, -2, 0,
      ],
      [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    ));
    const marked = markParametricSolvedDomain(full, 1);

    expect(marked.surfaces.reduce((count, item) => count + item.indices.length / 3, 0)).toBe(4);
    expect(marked.surfaces.find((item) => item.solvedDomain)?.indices).toHaveLength(3);
    expect(marked.surfaces.find((item) => !item.solvedDomain)?.indices).toHaveLength(9);
  });

  it('maps UI solve modes to the physical domain masks', () => {
    expect(quadrantsForSolveMode('quarter')).toBe(1);
    expect(quadrantsForSolveMode('half_xz')).toBe(12);
    expect(quadrantsForSolveMode('half_yz')).toBe(14);
    expect(quadrantsForSolveMode('full')).toBe(1234);
    expect(quadrantsForSolveMode('auto', 1)).toBe(1);
  });
});

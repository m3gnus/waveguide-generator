import { describe, expect, it } from 'vitest';
import type { FieldPlaneSpec } from '../api/fieldPlane';
import {
  classifyFieldPlaneMask,
  createFieldPlaneMaskMesh,
  fieldPlaneMaskDistanceMetres,
  isPointInsideMaskMesh,
  isWatertightTriangleMesh,
} from './fieldPlaneMaskLogic';

const boxVertices = Float32Array.of(
  -1, -1, -1,
  1, -1, -1,
  1, 1, -1,
  -1, 1, -1,
  -1, -1, 1,
  1, -1, 1,
  1, 1, 1,
  -1, 1, 1,
);

const boxIndices = Uint32Array.of(
  0, 2, 1, 0, 3, 2,
  4, 5, 6, 4, 6, 7,
  0, 1, 5, 0, 5, 4,
  3, 7, 6, 3, 6, 2,
  0, 4, 7, 0, 7, 3,
  1, 2, 6, 1, 6, 5,
);

const interiorPlane: FieldPlaneSpec = {
  origin_m: [0, 0, 0],
  axis_u: [1, 0, 0],
  axis_v: [0, 1, 0],
  width_m: 0.5,
  height_m: 0.5,
  nx: 3,
  ny: 3,
};

describe('field-plane worker mask logic', () => {
  it('accepts a closed box and rejects the same box with one face removed', () => {
    expect(isWatertightTriangleMesh(boxIndices)).toBe(true);
    expect(isWatertightTriangleMesh(boxIndices.slice(0, -6))).toBe(false);
  });

  it('classifies points by ray parity against hand-built closed meshes', () => {
    const box = createFieldPlaneMaskMesh(boxVertices.slice(), boxIndices.slice());
    expect(isPointInsideMaskMesh(box, [0, 0, 0])).toBe(true);
    expect(isPointInsideMaskMesh(box, [1.5, 0, 0])).toBe(false);
    box.geometry.dispose();

    const tetrahedron = createFieldPlaneMaskMesh(
      Float32Array.of(0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1),
      Uint32Array.of(0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3),
    );
    expect(isPointInsideMaskMesh(tetrahedron, [0.1, 0.1, 0.1])).toBe(true);
    expect(isPointInsideMaskMesh(tetrahedron, [0.8, 0.8, 0.8])).toBe(false);
    tetrahedron.geometry.dispose();
  });

  it('skips interior masking for an open shell while retaining near-surface masking', () => {
    const closed = createFieldPlaneMaskMesh(boxVertices.slice(), boxIndices.slice());
    const open = createFieldPlaneMaskMesh(boxVertices.slice(), boxIndices.slice(0, -6));
    expect([...classifyFieldPlaneMask(closed, interiorPlane)]).toEqual(new Array(9).fill(1));
    expect([...classifyFieldPlaneMask(open, interiorPlane)]).toEqual(new Array(9).fill(0));

    const surfacePlane = { ...interiorPlane, origin_m: [0, 0, 1] as [number, number, number] };
    expect([...classifyFieldPlaneMask(open, surfacePlane)]).toEqual(new Array(9).fill(1));
    expect(fieldPlaneMaskDistanceMetres({ ...interiorPlane, width_m: 1e-5, height_m: 1e-5 }))
      .toBe(1e-4);
    closed.geometry.dispose();
    open.geometry.dispose();
  });
});

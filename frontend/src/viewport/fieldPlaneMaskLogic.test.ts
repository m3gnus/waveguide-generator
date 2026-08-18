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

const halfBoxIndices = Uint32Array.from([
  ...boxIndices.slice(0, 24),
  ...boxIndices.slice(30),
]);

function halfBoxVertices(seamX = 0): Float32Array {
  const vertices = boxVertices.slice();
  for (const index of [0, 3, 4, 7]) vertices[index * 3] = seamX;
  return vertices;
}

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

  it('closes a reduced half-box and classifies points in its mirrored half', () => {
    const reduced = createFieldPlaneMaskMesh(halfBoxVertices(), halfBoxIndices);
    const mirrored = createFieldPlaneMaskMesh(halfBoxVertices(), halfBoxIndices, 'yz');

    expect(reduced.watertight).toBe(false);
    expect(mirrored.watertight).toBe(true);
    expect(isPointInsideMaskMesh(mirrored, [-0.5, 0, 0])).toBe(true);
    expect(mirrored.geometry.index?.count).toBe(halfBoxIndices.length * 2);
    reduced.geometry.dispose();
    mirrored.geometry.dispose();
  });

  it('snaps near-plane vertices so mirrored seam indices weld exactly', () => {
    const mirrored = createFieldPlaneMaskMesh(halfBoxVertices(1e-8), halfBoxIndices, 'yz');
    const positions = mirrored.geometry.getAttribute('position');

    expect(mirrored.snappedVertexCount).toBe(4);
    expect(mirrored.watertight).toBe(true);
    expect(positions.count).toBe(12);
    for (const index of [0, 3, 4, 7]) expect(positions.getX(index)).toBe(0);
    mirrored.geometry.dispose();
  });

  it('reports zero snapped vertices when symmetry-plane coordinates do not change', () => {
    const mirrored = createFieldPlaneMaskMesh(halfBoxVertices(), halfBoxIndices, 'yz');

    expect(mirrored.snappedVertexCount).toBe(0);
    mirrored.geometry.dispose();
  });

  it('creates four consistently wound images for two active symmetry planes', () => {
    const vertices = halfBoxVertices(1e-8);
    for (const index of [0, 1, 4, 5]) vertices[index * 3 + 1] = 1e-8;
    const quarterBoxIndices = Uint32Array.from([
      ...boxIndices.slice(0, 12),
      ...boxIndices.slice(18, 24),
      ...boxIndices.slice(30),
    ]);
    const mirrored = createFieldPlaneMaskMesh(vertices, quarterBoxIndices, 'yz+xz');

    expect(mirrored.snappedVertexCount).toBe(6);
    expect(mirrored.watertight).toBe(true);
    expect(mirrored.geometry.index?.count).toBe(quarterBoxIndices.length * 4);
    expect(isPointInsideMaskMesh(mirrored, [-0.5, -0.5, 0])).toBe(true);
    mirrored.geometry.dispose();
  });
});

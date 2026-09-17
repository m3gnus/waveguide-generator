import { describe, expect, it } from 'vitest';
import fixture from './solverFrame.fixture.json';
import { parseMSH } from './mshParser';
import {
  SOLVER_FRAME_AXES,
  applyRowMajor,
  framePreviewProjection,
  type RowMajorMatrix,
} from './solverFrame';

/** A two-triangle model: a wall triangle, and a source triangle in the y=0 plane
 * behind it, authored radiating along the assembly's +Y. */
const MSH = [
  '$MeshFormat', '2.2 0 8', '$EndMeshFormat',
  '$PhysicalNames', '2',
  '2 1 "wg-import-v1|rigid"',
  '2 101 "wg-import-v1|tag=101|source_id=throat|instance_id=null|role=HF"',
  '$EndPhysicalNames',
  '$Nodes', '6',
  '1 0 60 0', '2 30 60 0', '3 0 60 30',
  '4 -10 0 0', '5 10 0 0', '6 0 0 10',
  '$EndNodes',
  '$Elements', '2',
  '1 2 2 1 1 1 2 3',
  '2 2 2 101 2 4 5 6',
  '$EndElements',
].join('\n');

describe('solver frame contract', () => {
  it('is exactly the server contract, for every axis', () => {
    expect(fixture.contract).toBe('cad-solver-frame-v1');
    expect(fixture.matrixConvention).toBe('row-major');
    expect(Object.keys(fixture.axes).sort()).toEqual([...SOLVER_FRAME_AXES].sort());
  });

  it('takes each axis to solver +Z when applied row-major', () => {
    const unit: Record<string, [number, number, number]> = {
      '+x': [1, 0, 0], '-x': [-1, 0, 0], '+y': [0, 1, 0], '-y': [0, -1, 0], '+z': [0, 0, 1], '-z': [0, 0, -1],
    };
    for (const axis of SOLVER_FRAME_AXES) {
      const matrix = fixture.axes[axis] as RowMajorMatrix;
      const mapped = applyRowMajor(matrix, ...unit[axis]);
      expect(mapped.map((value) => Math.round(value * 1e9) / 1e9 + 0)).toEqual([0, 0, 1]);
    }
    // Row-major, not column-major: +y -> +Z only reads that way.
    const transposed = (fixture.axes['+y'] as RowMajorMatrix)[0].map((_, column) =>
      (fixture.axes['+y'] as RowMajorMatrix).map((row) => row[column]));
    expect(applyRowMajor(transposed, 0, 1, 0)[2]).toBeCloseTo(-1);
  });

  it('projects the previewed model with the solver axis horizontal and marks the sources', () => {
    const projection = framePreviewProjection(parseMSH(MSH), fixture.axes['+y'] as RowMajorMatrix);
    expect(projection.views.map((view) => [view.name, view.vertical])).toEqual([['side', 'y'], ['top', 'x']]);
    const side = projection.views[0];
    expect([...side.source]).toEqual([0, 1]);
    // The wall sits 60 along the radiation axis once previewed along +y.
    const wallHorizontal = [side.triangles[0], side.triangles[2], side.triangles[4]];
    expect(wallHorizontal.every((value) => Math.abs(value - 60) < 1e-4)).toBe(true);
    const sourceHorizontal = [side.triangles[6], side.triangles[8], side.triangles[10]];
    expect(sourceHorizontal.every((value) => Math.abs(value) < 1e-4)).toBe(true);
    expect(projection.bounds.max[0]).toBeCloseTo(60);
    expect(projection.bounds.min[0]).toBeCloseTo(0);
  });

  it('shows the model as modelled with the identity', () => {
    const projection = framePreviewProjection(parseMSH(MSH), fixture.axes['+z'] as RowMajorMatrix);
    // Modelled along +Y, it lies across the solver axis: the wall is at 0 along it.
    expect(projection.bounds.max[0]).toBeCloseTo(30);
  });
});

import { Box3, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { planesToMark, symmetryPlaneOutline } from './SymmetryPlaneMarks';

describe('symmetry plane marks', () => {
  const bounds = new Box3(new Vector3(-0.2, -0.1, 0), new Vector3(0.2, 0.1, 0.5));

  it('outlines x = 0 across the model, a little past it', () => {
    const corners = symmetryPlaneOutline(bounds, 'x0');
    expect(corners).toHaveLength(12);
    for (let offset = 0; offset < corners.length; offset += 3) expect(corners[offset]).toBe(0);
    const ys = corners.filter((_value, index) => index % 3 === 1);
    const zs = corners.filter((_value, index) => index % 3 === 2);
    expect(Math.min(...ys)).toBeLessThan(-0.1);
    expect(Math.max(...ys)).toBeGreaterThan(0.1);
    expect(Math.min(...zs)).toBeLessThan(0);
    expect(Math.max(...zs)).toBeGreaterThan(0.5);
  });

  it('outlines y = 0 in its own plane', () => {
    const corners = symmetryPlaneOutline(bounds, 'y0');
    for (let offset = 1; offset < corners.length; offset += 3) expect(corners[offset]).toBe(0);
    const xs = corners.filter((_value, index) => index % 3 === 0);
    expect(Math.min(...xs)).toBeLessThan(-0.2);
    expect(Math.max(...xs)).toBeGreaterThan(0.2);
  });

  it('marks a reduced CAD solve\'s planes only while the viewer asks for them', () => {
    const scene = { symmetryPlanes: ['x0', 'y0'] as const };
    expect(planesToMark(scene, { markSymmetryPlanes: true })).toEqual(['x0', 'y0']);
    expect(planesToMark(scene, { markSymmetryPlanes: false })).toEqual([]);
    // A parametric preview names no mirror planes: nothing to mark.
    expect(planesToMark({}, { markSymmetryPlanes: true })).toEqual([]);
    expect(planesToMark(null, { markSymmetryPlanes: true })).toEqual([]);
  });
});

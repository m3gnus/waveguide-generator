import { Box3, Plane, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import type { FieldPlaneSpec } from '../api/fieldPlane';
import { deriveCapQuad, fieldPlaneToClipPlane } from './fieldPlaneClipping';

const fieldPlane: FieldPlaneSpec = {
  origin_m: [1, 2, 3],
  axis_u: [1, 0, 0],
  axis_v: [0, 1, 0],
  width_m: 1,
  height_m: 1,
  nx: 3,
  ny: 3,
};

describe('field-plane model clipping math', () => {
  it('converts the solver-metre plane equation and negates both terms when inverted', () => {
    const target = new Plane();
    expect(fieldPlaneToClipPlane(fieldPlane, 1_000, false, target)).toBe(target);
    expect(target.normal.toArray()).toEqual([0, 0, 1]);
    expect(target.constant).toBe(-3_000);
    expect(target.distanceToPoint(new Vector3(1_000, 2_000, 3_000))).toBe(0);

    fieldPlaneToClipPlane(fieldPlane, 1_000, true, target);
    expect(target.normal.x).toBeCloseTo(0);
    expect(target.normal.y).toBeCloseTo(0);
    expect(target.normal.z).toBe(-1);
    expect(target.constant).toBe(3_000);
  });

  it('derives an orthogonal cap basis and projected size from the clip plane and bounds', () => {
    const plane = new Plane(new Vector3(1, 0, 0), -2);
    const bounds = new Box3(new Vector3(-2, -3, -4), new Vector3(6, 5, 8));
    const cap = deriveCapQuad(plane, bounds);

    expect(cap.center.toArray()).toEqual([2, 1, 2]);
    expect(cap.width).toBeCloseTo(12 * 1.05);
    expect(cap.height).toBeCloseTo(8 * 1.05);
    expect(cap.axisU.dot(plane.normal)).toBeCloseTo(0);
    expect(cap.axisV.dot(plane.normal)).toBeCloseTo(0);
    expect(cap.axisU.clone().cross(cap.axisV).dot(plane.normal)).toBeCloseTo(1);
    expect(new Vector3(0, 0, 0).applyMatrix4(cap.matrix).toArray()).toEqual([2, 1, 2]);
  });
});

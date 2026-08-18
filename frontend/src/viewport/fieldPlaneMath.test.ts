import { describe, expect, it } from 'vitest';
import type { FieldPlaneSpec } from '../api/fieldPlane';
import {
  fieldPlaneOffsetMetres,
  fieldPlanePreset,
  rotateFieldPlane,
  rotationAngleFromRays,
  snapFieldPlaneRotation,
  translationDeltaAlongNormal,
  withFieldPlaneOffset,
} from './fieldPlaneMath';

const plane: FieldPlaneSpec = {
  origin_m: [0, 0, 0],
  axis_u: [1, 0, 0],
  axis_v: [0, 0, 1],
  width_m: 0.4,
  height_m: 0.8,
  nx: 96,
  ny: 96,
};

describe('field-plane preset and rotation math', () => {
  const bounds = {
    min: [-50, -30, 0] as [number, number, number],
    max: [50, 30, 200] as [number, number, number],
    unitsPerMetre: 1_000,
  };

  it('constructs H, V, and axial mouth planes in solver metres', () => {
    expect(fieldPlanePreset(plane, 'h', bounds)).toMatchObject({
      origin_m: [0, 0, 0], axis_u: [1, 0, 0], axis_v: [0, 0, 1], width_m: 0.2, height_m: 0.4,
    });
    expect(fieldPlanePreset(plane, 'v', bounds)).toMatchObject({
      origin_m: [0, 0, 0], axis_u: [0, 1, 0], axis_v: [0, 0, 1], width_m: 0.12, height_m: 0.4,
    });
    expect(fieldPlanePreset(plane, 'mouth', bounds)).toMatchObject({
      origin_m: [0, 0, 0.2], axis_u: [1, 0, 0], axis_v: [0, 1, 0], width_m: 0.2, height_m: 0.12,
    });
  });

  it('edits the signed normal offset without disturbing tangential origin', () => {
    const moved = withFieldPlaneOffset({ ...plane, origin_m: [0.1, 0.2, 0.3] }, 0.5);
    expect(moved.origin_m[0]).toBeCloseTo(0.1);
    expect(moved.origin_m[1]).toBeCloseTo(-0.5);
    expect(moved.origin_m[2]).toBeCloseTo(0.3);
    expect(fieldPlaneOffsetMetres(moved)).toBeCloseTo(0.5);
  });

  it('snaps rotation to 5 degrees unless free rotation is requested', () => {
    const sevenDegrees = 7 * Math.PI / 180;
    expect(snapFieldPlaneRotation(sevenDegrees, false)).toBeCloseTo(5 * Math.PI / 180);
    expect(snapFieldPlaneRotation(sevenDegrees, true)).toBe(sevenDegrees);
    const rotated = rotateFieldPlane(plane, 'u', Math.PI / 2).axis_v;
    expect(rotated[0]).toBeCloseTo(0);
    expect(rotated[1]).toBeCloseTo(-1);
    expect(rotated[2]).toBeCloseTo(0);
  });
});

describe('field-plane pointer gesture math', () => {
  it('projects pointer rays to a translation delta along the normal axis', () => {
    expect(translationDeltaAlongNormal(
      { origin: [5, 0, 0], direction: [-1, 0, 0] },
      { origin: [5, 0, 2], direction: [-1, 0, 0] },
      [0, 0, 0],
      [0, 0, 1],
    )).toBeCloseTo(2);
  });

  it('derives a signed rotation from ray intersections with the ring plane', () => {
    expect(rotationAngleFromRays(
      { origin: [1, 0, 5], direction: [0, 0, -1] },
      { origin: [0, 1, 5], direction: [0, 0, -1] },
      [0, 0, 0],
      [0, 0, 1],
    )).toBeCloseTo(Math.PI / 2);
  });
});

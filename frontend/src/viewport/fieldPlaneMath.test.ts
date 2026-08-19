import { describe, expect, it } from 'vitest';
import type { FieldPlaneSpec } from '../api/fieldPlane';
import {
  fieldPlaneOffsetMetres,
  FIELD_PLANE_MAX_EXTENT_M,
  FIELD_PLANE_MIN_EXTENT_M,
  probeFieldPlaneRay,
  resizeFieldPlane,
  translateFieldPlane,
  withFieldPlaneExtents,
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

describe('field-plane translation, resizing, and probing', () => {
  const tilted: FieldPlaneSpec = { ...plane, origin_m: [0.1, -0.2, 0.3] };

  const expectOrigin = (actual: readonly number[], expected: readonly number[]) => {
    actual.forEach((value, index) => expect(value).toBeCloseTo(expected[index], 12));
  };

  it('slides the plane along each of its own axes', () => {
    expectOrigin(translateFieldPlane(tilted, 'u', 0.05).origin_m, [0.15, -0.2, 0.3]);
    expectOrigin(translateFieldPlane(tilted, 'v', 0.05).origin_m, [0.1, -0.2, 0.35]);
    // axis_u x axis_v is -y for this H-plane, so the normal drag runs down -y.
    expectOrigin(translateFieldPlane(tilted, 'n', 0.05).origin_m, [0.1, -0.25, 0.3]);
  });

  it('leaves the normal offset alone when moving in the plane', () => {
    const moved = translateFieldPlane(tilted, 'u', 0.4);
    expect(fieldPlaneOffsetMetres(moved)).toBeCloseTo(fieldPlaneOffsetMetres(tilted), 12);
  });

  it('resizes symmetrically about the centre and clamps to the request limits', () => {
    const grown = resizeFieldPlane(plane, 0.2, -0.3);
    expect(grown.width_m).toBeCloseTo(0.6);
    expect(grown.height_m).toBeCloseTo(0.5);
    expect(grown.origin_m).toEqual(plane.origin_m);
    expect(resizeFieldPlane(plane, -10, 0).width_m).toBe(FIELD_PLANE_MIN_EXTENT_M);
    expect(resizeFieldPlane(plane, 0, 1e6).height_m).toBe(FIELD_PLANE_MAX_EXTENT_M);
    expect(withFieldPlaneExtents(plane, Number.NaN, 0.25).width_m).toBe(FIELD_PLANE_MIN_EXTENT_M);
  });

  it('reports plane coordinates for a ray that lands inside the quad', () => {
    // Rays are in viewport units; the plane is metres.
    const hit = probeFieldPlaneRay(plane, {
      origin: [100, 500, 200],
      direction: [0, -1, 0],
    }, 1_000);
    expect(hit).not.toBeNull();
    expect(hit!.offsetU_m).toBeCloseTo(0.1);
    expect(hit!.offsetV_m).toBeCloseTo(0.2);
    expect(hit!.u).toBeCloseTo(0.75);
    expect(hit!.v).toBeCloseTo(0.75);
    expect(hit!.point_m[1]).toBeCloseTo(0);
  });

  it('misses when the ray falls outside the quad or runs parallel to it', () => {
    expect(probeFieldPlaneRay(plane, { origin: [1_000, 500, 0], direction: [0, -1, 0] }, 1_000)).toBeNull();
    expect(probeFieldPlaneRay(plane, { origin: [0, 500, 0], direction: [1, 0, 0] }, 1_000)).toBeNull();
    expect(probeFieldPlaneRay(plane, { origin: [0, 500, 0], direction: [0, -1, 0] }, 0)).toBeNull();
  });
});

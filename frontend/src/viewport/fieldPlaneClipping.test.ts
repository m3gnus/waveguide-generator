import { Box3, Plane, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import type { FieldPlaneSpec } from '../api/fieldPlane';
import {
  deriveCapQuad,
  fieldPlaneToClipPlane,
  nextSectionCut,
  sectionClipPlane,
  type SectionAxis,
  type SectionCutState,
} from './fieldPlaneClipping';

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

describe('section-cut cycle', () => {
  const from = (clipMode: SectionCutState['clipMode'], sectionAxis: SectionAxis) =>
    nextSectionCut({ clipMode, sectionAxis });

  it('walks X, Y, Z, off over four presses and then repeats', () => {
    let state: SectionCutState = { clipMode: 'off', sectionAxis: 'x' };
    const walked: SectionCutState[] = [];
    for (let press = 0; press < 8; press += 1) {
      state = nextSectionCut(state);
      walked.push(state);
    }
    expect(walked).toEqual([
      { clipMode: 'section', sectionAxis: 'x' },
      { clipMode: 'section', sectionAxis: 'y' },
      { clipMode: 'section', sectionAxis: 'z' },
      { clipMode: 'off', sectionAxis: 'x' },
      { clipMode: 'section', sectionAxis: 'x' },
      { clipMode: 'section', sectionAxis: 'y' },
      { clipMode: 'section', sectionAxis: 'z' },
      { clipMode: 'off', sectionAxis: 'x' },
    ]);
  });

  it('re-enters at the first axis from any other clip mode, whatever axis it held', () => {
    // The field-plane clip owns the model's clipping while it is on, so a press
    // of the section button is a request for a cut the user can see, not a
    // resumption of one they cannot.
    expect(from('field-plane', 'z')).toEqual({ clipMode: 'section', sectionAxis: 'x' });
    expect(from('off', 'y')).toEqual({ clipMode: 'section', sectionAxis: 'x' });
  });
});

describe('section clip planes', () => {
  const bounds = new Box3(new Vector3(-120, -80, 0), new Vector3(120, 80, 300));

  it('keeps the positive half about the origin on the two symmetry axes', () => {
    for (const [axis, normal] of [
      ['x', [1, 0, 0]], ['y', [0, 1, 0]],
    ] as Array<[SectionAxis, number[]]>) {
      const plane = sectionClipPlane(axis, bounds);
      expect(plane.normal.toArray()).toEqual(normal);
      expect(plane.constant).toBe(0);
    }
  });

  it('cuts through the middle of an axis the origin does not sit inside', () => {
    // Throat at z=0, mouth at z=300: a cut at z=0 would clip nothing away and
    // the press would read as a dead button.
    const plane = sectionClipPlane('z', bounds);
    expect(plane.normal.toArray()).toEqual([0, 0, 1]);
    expect(plane.constant).toBe(-150);
    expect(plane.distanceToPoint(new Vector3(0, 0, 150))).toBe(0);
  });

  it('falls back to the origin without bounds and writes into a supplied plane', () => {
    const target = new Plane();
    expect(sectionClipPlane('z', undefined, target)).toBe(target);
    expect(target.constant).toBe(0);
    expect(sectionClipPlane('y', new Box3(), target).constant).toBe(0);
  });
});

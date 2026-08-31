import { describe, expect, it } from 'vitest';
import type { ResultData } from '../api/results';
import {
  angleForPoint,
  nearestMicrophone,
  observationFrameBasisOf,
  observationRig,
  pivotFor,
  transverseFor,
  type ObservationFrameBasis,
  type ObservationRigSpec,
} from './observationRig';

const basis: ObservationFrameBasis = {
  axis: [0, 0, 1],
  u: [1, 0, 0],
  v: [0, 1, 0],
  origin_m: [0, 0, 0.19],
  mouth_center_m: [0, 0, 0.19],
  source_center_m: [0, 0, 0],
};

const spec = (overrides: Partial<ObservationRigSpec> = {}): ObservationRigSpec => ({
  distanceM: 2,
  angleStartDeg: 0,
  angleEndDeg: 90,
  angleCount: 3,
  planes: ['horizontal'],
  inclinationDeg: 45,
  origin: 'mouth',
  ...overrides,
});

describe('observationFrameBasisOf', () => {
  it('reads a complete basis out of result metadata', () => {
    const result = { metadata: { observation_frame_basis: basis } } as unknown as ResultData;
    expect(observationFrameBasisOf(result)).toEqual(basis);
  });

  it('refuses anything it cannot place a microphone with', () => {
    expect(observationFrameBasisOf(undefined)).toBeNull();
    expect(observationFrameBasisOf({ metadata: {} } as ResultData)).toBeNull();
    expect(observationFrameBasisOf({
      metadata: { observation_frame_basis: { axis: [0, 0, 1], origin_m: [0, 0, 0] } },
    } as unknown as ResultData)).toBeNull();
    expect(observationFrameBasisOf({
      metadata: { observation_frame_basis: { ...basis, axis: [0, 0, Number.NaN] } },
    } as unknown as ResultData)).toBeNull();
  });

  it('keeps a basis that omits only the optional centres', () => {
    const bare = { axis: basis.axis, u: basis.u, v: basis.v, origin_m: basis.origin_m };
    expect(observationFrameBasisOf({
      metadata: { observation_frame_basis: bare },
    } as unknown as ResultData)).toEqual(bare);
  });
});

describe('pivotFor', () => {
  it('moves between the two published centres without a solve', () => {
    expect(pivotFor(basis, 'mouth')).toEqual([0, 0, 0.19]);
    expect(pivotFor(basis, 'throat')).toEqual([0, 0, 0]);
  });

  it('falls back to the solved origin when a centre is missing', () => {
    const bare: ObservationFrameBasis = { axis: basis.axis, u: basis.u, v: basis.v, origin_m: [0, 0, 0.5] };
    expect(pivotFor(bare, 'throat')).toEqual([0, 0, 0.5]);
  });
});

describe('transverseFor', () => {
  it('sweeps the diagonal at the stated inclination, as the solver does', () => {
    const [x, y, z] = transverseFor(basis, 'diagonal', 30);
    expect(x).toBeCloseTo(Math.cos(Math.PI / 6), 12);
    expect(y).toBeCloseTo(Math.sin(Math.PI / 6), 12);
    expect(z).toBeCloseTo(0, 12);
  });

  it('is the frame vectors themselves for the cardinal planes', () => {
    expect(transverseFor(basis, 'horizontal', 45)).toEqual(basis.u);
    expect(transverseFor(basis, 'vertical', 45)).toEqual(basis.v);
  });
});

describe('observationRig', () => {
  it('places microphones on the arc the solver would sample', () => {
    const rig = observationRig(basis, spec(), 1);
    expect(rig.microphones.map(({ angleDeg }) => angleDeg)).toEqual([0, 45, 90]);
    const [onAxis, , atNinety] = rig.microphones;
    expect(onAxis.position[0]).toBeCloseTo(0, 12);
    expect(onAxis.position[2]).toBeCloseTo(2.19, 12);
    expect(atNinety.position[0]).toBeCloseTo(2, 12);
    expect(atNinety.position[2]).toBeCloseTo(0.19, 12);
  });

  // A parametric preview is drawn in millimetres; the frame is always metres.
  it('scales into viewport units', () => {
    const rig = observationRig(basis, spec(), 1_000);
    expect(rig.origin).toEqual([0, 0, 190]);
    expect(rig.microphones[0].position[2]).toBeCloseTo(2_190, 9);
  });

  it('draws the arc more finely than the sweep is sampled', () => {
    const rig = observationRig(basis, spec({ angleCount: 3 }), 1);
    expect(rig.arcs).toHaveLength(1);
    expect(rig.arcs[0].positions.length).toBeGreaterThan(3 * 3);
    // Every drawn point sits at the measurement distance from the pivot. The
    // tolerance is float32's, because that is what a line geometry stores.
    for (let index = 0; index < rig.arcs[0].positions.length; index += 3) {
      const x = rig.arcs[0].positions[index] - rig.origin[0];
      const y = rig.arcs[0].positions[index + 1] - rig.origin[1];
      const z = rig.arcs[0].positions[index + 2] - rig.origin[2];
      expect(Math.hypot(x, y, z)).toBeCloseTo(2, 5);
    }
  });

  it('gives every enabled plane its own arc and its own microphones', () => {
    const rig = observationRig(basis, spec({ planes: ['horizontal', 'vertical', 'diagonal'] }), 1);
    expect(rig.arcs.map(({ plane }) => plane)).toEqual(['horizontal', 'vertical', 'diagonal']);
    expect(rig.microphones).toHaveLength(9);
  });

  it('collapses to the single measured point when the sweep has one angle', () => {
    const rig = observationRig(basis, spec({ angleCount: 1 }), 1);
    expect(rig.microphones.map(({ angleDeg }) => angleDeg)).toEqual([0]);
  });
});

describe('angleForPoint', () => {
  it('inverts the placement it was built from', () => {
    const planes = spec({ planes: ['horizontal', 'vertical'] });
    const rig = observationRig(basis, { ...planes, angleCount: 7 }, 1_000);
    rig.microphones.forEach((microphone) => {
      const found = angleForPoint(basis, planes, 1_000, microphone.position);
      expect(found?.angleDeg).toBeCloseTo(microphone.angleDeg, 9);
      // On-axis is the same physical point in every plane, so which plane
      // claims it is arbitrary and the first one wins. Everywhere else the
      // point belongs to exactly one plane.
      if (microphone.angleDeg !== 0) expect(found?.plane).toBe(microphone.plane);
    });
  });

  it('picks the plane a point off the arc is nearest to', () => {
    const off: [number, number, number] = [0, 1.9, 0.19];
    expect(angleForPoint(basis, spec({ planes: ['horizontal', 'vertical'] }), 1, off)?.plane).toBe('vertical');
  });
});

describe('nearestMicrophone', () => {
  it('finds the sampled angle a point is closest to', () => {
    const rig = observationRig(basis, spec({ angleCount: 3 }), 1);
    expect(nearestMicrophone(rig, [0.1, 0, 2.2])?.angleDeg).toBe(0);
    expect(nearestMicrophone(rig, [2, 0, 0.3])?.angleDeg).toBe(90);
    expect(nearestMicrophone({ origin: [0, 0, 0], microphones: [], arcs: [] }, [0, 0, 0])).toBeNull();
  });
});

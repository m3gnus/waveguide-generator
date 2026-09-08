import { describe, expect, it } from 'vitest';
import type { PolarSample } from '../api/results';
import type { ResultPayload } from './types';
import {
  measurementAnchorRefusal,
  measurementAngleLabel,
  measurementAngles,
  measurementPlanes,
  nearestMeasurementAngle,
  onAxisAngle,
  referencePlane,
  withMeasurementAngle,
} from './measurementAngle';
import { withNormalizationAngle } from './normalization';

const row = (levels: Array<[number, number | null]>): PolarSample[] =>
  levels.map(([angle, level]) => [angle, level] as PolarSample);

/**
 * One frequency, three angles, an on-axis absolute level of 94 dB, and a
 * pattern whose stored zero sits at 0 degrees. The shape is -3 dB at 30 and
 * -9 dB at 60, so the answers are 91 and 85.
 */
function payload(overrides: Partial<ResultPayload> = {}): ResultPayload {
  return {
    frequencies: [1000],
    directivity: { horizontal: [row([[0, 0], [30, -3], [60, -9]])] },
    directivity_phase: { horizontal: [[[0, -20], [30, -55], [60, -110]]] },
    spl_on_axis: { frequencies: [1000], spl: [94], phase_degrees: [-20] },
    ...overrides,
  } as ResultPayload;
}

describe('measurement grid', () => {
  it('lists the planes and angles the run carries', () => {
    const result = payload({
      directivity: {
        horizontal: [row([[0, 0], [30, -3]])],
        vertical: [row([[0, 0], [45, -8]])],
      },
    });
    expect(measurementPlanes(result)).toEqual(['horizontal', 'vertical']);
    expect(measurementAngles(result, 'vertical')).toEqual([0, 45]);
    expect(measurementPlanes({ frequencies: [] } as unknown as ResultPayload)).toEqual([]);
  });

  it('snaps a stale angle to the nearest one this run measured', () => {
    expect(nearestMeasurementAngle(payload(), 'horizontal', 25)).toBe(30);
    expect(nearestMeasurementAngle(payload(), 'horizontal', 400)).toBe(60);
  });

  it('prefers the angle metadata states over the nearest-zero guess', () => {
    const result = payload({
      directivity: { horizontal: [row([[10, 0], [40, -6]])] },
      metadata: { spl_on_axis: { requested_angle_degrees: 0, sampled_angle_degrees: 10 } },
    });
    expect(onAxisAngle(result, 'horizontal')).toBe(10);
  });

  it('falls back to the sample nearest zero when metadata is absent', () => {
    expect(onAxisAngle(payload({ directivity: { horizontal: [row([[-15, -1], [10, 0]])] } }), 'horizontal')).toBe(10);
  });

  it('writes an angle the way the legend does', () => {
    expect(measurementAngleLabel(30)).toBe('30°');
    expect(measurementAngleLabel(22.5)).toBe('22.5°');
  });
});

describe('withMeasurementAngle', () => {
  it('recovers absolute SPL at the requested angle', () => {
    const shifted = withMeasurementAngle(payload(), 'horizontal', 30);
    expect(shifted.spl_on_axis?.spl).toEqual([91]);
    expect(withMeasurementAngle(payload(), 'horizontal', 60).spl_on_axis?.spl).toEqual([85]);
  });

  it('takes phase straight from the raw wrapped samples', () => {
    expect(withMeasurementAngle(payload(), 'horizontal', 30).spl_on_axis?.phase_degrees).toEqual([-55]);
  });

  /**
   * The reason the two directivity terms are differenced rather than used
   * directly: the per-row constant is unknown and backend-dependent, and after
   * `withNormalizationAngle` it is whatever the user last typed. The recovered
   * absolute level must not move when it changes.
   */
  it('is unaffected by whatever the patterns are referenced to', () => {
    const raw = payload();
    const at30 = withMeasurementAngle(raw, 'horizontal', 30).spl_on_axis?.spl;
    [0, 30, 60].forEach((reference) => {
      const referenced = withNormalizationAngle(raw, reference);
      expect(withMeasurementAngle(referenced, 'horizontal', 30).spl_on_axis?.spl).toEqual(at30);
    });
  });

  it('reports an unavailable cell rather than guessing a level', () => {
    const result = payload({ directivity: { horizontal: [row([[0, 0], [30, null]])] } });
    expect(withMeasurementAngle(result, 'horizontal', 30).spl_on_axis?.spl).toEqual([null]);
  });

  it('returns the input untouched on-axis, and when the plane is missing', () => {
    const result = payload();
    expect(withMeasurementAngle(result, 'horizontal', 0)).toBe(result);
    expect(withMeasurementAngle(result, 'vertical', 30)).toBe(result);
  });

  it('leaves everything except spl_on_axis alone', () => {
    const result = payload();
    const shifted = withMeasurementAngle(result, 'horizontal', 30);
    expect(shifted.directivity).toBe(result.directivity);
    expect(shifted.directivity_phase).toBe(result.directivity_phase);
    expect(shifted.frequencies).toBe(result.frequencies);
  });

  it('caches per plane and angle', () => {
    const result = payload();
    expect(withMeasurementAngle(result, 'horizontal', 30)).toBe(withMeasurementAngle(result, 'horizontal', 30));
    expect(withMeasurementAngle(result, 'horizontal', 60)).not.toBe(withMeasurementAngle(result, 'horizontal', 30));
  });
});

/**
 * A grid that omits zero: 5 to 15 degrees, three planes.
 *
 * The reference sample is 5 degrees, which is one observation point in each
 * plane rather than one shared point. Horizontal reads 100/98 dB, vertical
 * 90/85, diagonal 95/94 -- and every `directivity` row is separately normalized
 * to its own 5-degree sample, so the offsets between the planes survive only in
 * `metadata.spl_on_axis.plane_reference_spl_db`. `spl_on_axis` is horizontal's,
 * because horizontal is the first measured plane.
 */
function zeroLessPayload(overrides: Partial<ResultPayload> = {}): ResultPayload {
  return {
    frequencies: [1000],
    directivity: {
      horizontal: [row([[5, 0], [15, -2]])],
      vertical: [row([[5, 0], [15, -5]])],
      diagonal: [row([[5, 0], [15, -1]])],
    },
    directivity_phase: {
      horizontal: [[[5, -20], [15, -55]]],
      vertical: [[[5, 10], [15, 40]]],
      diagonal: [[[5, -70], [15, -95]]],
    },
    spl_on_axis: { frequencies: [1000], spl: [100], phase_degrees: [-20] },
    metadata: {
      spl_on_axis: {
        requested_angle_degrees: 0,
        sampled_angle_degrees: 5,
        plane_reference_spl_db: { horizontal: [100], vertical: [90], diagonal: [95] },
      },
    },
    ...overrides,
  } as ResultPayload;
}

/** The same run as stored before the per-plane anchor existed in the contract. */
function zeroLessLegacyPayload(): ResultPayload {
  const stored = zeroLessPayload();
  return {
    ...stored,
    metadata: { spl_on_axis: { requested_angle_degrees: 0, sampled_angle_degrees: 5 } },
  } as ResultPayload;
}

describe('a reference sample that is not 0 degrees', () => {
  it('names the plane spl_on_axis was measured in, in payload order', () => {
    expect(referencePlane(zeroLessPayload())).toBe('horizontal');
    expect(referencePlane(zeroLessPayload({
      directivity: {
        vertical: [row([[5, 0], [15, -5]])],
        horizontal: [row([[5, 0], [15, -2]])],
      },
    }))).toBe('vertical');
    expect(referencePlane({ frequencies: [] } as unknown as ResultPayload)).toBeNull();
  });

  /**
   * The defect this fixture exists for: anchoring a secondary plane to the
   * first plane's level at a shared *angle* that is not a shared *point*.
   * Vertical at 15 degrees is 85 dB; the first-plane anchor would say 95.
   */
  it('anchors each plane to its own absolute level at the reference sample', () => {
    const result = zeroLessPayload();
    expect(withMeasurementAngle(result, 'horizontal', 15).spl_on_axis?.spl).toEqual([98]);
    expect(withMeasurementAngle(result, 'vertical', 15).spl_on_axis?.spl).toEqual([85]);
    expect(withMeasurementAngle(result, 'diagonal', 15).spl_on_axis?.spl).toEqual([94]);
  });

  it('returns the requested plane at the reference angle, not the first plane', () => {
    const result = zeroLessPayload();
    const vertical = withMeasurementAngle(result, 'vertical', 5);
    expect(vertical.spl_on_axis?.spl).toEqual([90]);
    expect(vertical.spl_on_axis?.phase_degrees).toEqual([10]);
    expect(withMeasurementAngle(result, 'diagonal', 5).spl_on_axis?.spl).toEqual([95]);
    // The first plane is what the stored block already says, so it is untouched.
    expect(withMeasurementAngle(result, 'horizontal', 5)).toBe(result);
  });

  it('takes phase from the requested plane at every angle', () => {
    const result = zeroLessPayload();
    expect(withMeasurementAngle(result, 'vertical', 15).spl_on_axis?.phase_degrees).toEqual([40]);
    expect(withMeasurementAngle(result, 'diagonal', 15).spl_on_axis?.phase_degrees).toEqual([-95]);
  });

  it('stays put when the patterns are re-referenced to another angle', () => {
    const raw = zeroLessPayload();
    const at15 = withMeasurementAngle(raw, 'vertical', 15).spl_on_axis?.spl;
    expect(at15).toEqual([85]);
    [5, 15].forEach((reference) => {
      const referenced = withNormalizationAngle(raw, reference);
      expect(withMeasurementAngle(referenced, 'vertical', 15).spl_on_axis?.spl).toEqual(at15);
    });
  });

  it('declines a secondary plane rather than anchoring an old payload wrongly', () => {
    const legacy = zeroLessLegacyPayload();
    const refusal = measurementAnchorRefusal(legacy, 'vertical');
    expect(refusal).toMatch(/vertical level unavailable/);
    expect(refusal).toContain('5°');
    // Not 95 dB, which is what the first plane's anchor would have produced.
    expect(withMeasurementAngle(legacy, 'vertical', 15).spl_on_axis?.spl).toEqual([null]);
    expect(withMeasurementAngle(legacy, 'vertical', 5).spl_on_axis?.spl).toEqual([null]);
    // Phase is raw per-plane pressure phase and is never anchored, so it stays.
    expect(withMeasurementAngle(legacy, 'vertical', 15).spl_on_axis?.phase_degrees).toEqual([40]);
  });

  it('still answers for the plane the old payload does anchor', () => {
    const legacy = zeroLessLegacyPayload();
    expect(measurementAnchorRefusal(legacy, 'horizontal')).toBeNull();
    expect(withMeasurementAngle(legacy, 'horizontal', 15).spl_on_axis?.spl).toEqual([98]);
  });
});

describe('a grid that contains 0 degrees', () => {
  /**
   * Every plane meets at a true 0 degrees, so the one stored anchor is each
   * plane's own and an old payload needs no per-plane reference to be correct.
   * This is the ordinary case, and it must not acquire a refusal.
   */
  const twoPlane = () => payload({
    directivity: {
      horizontal: [row([[0, 0], [30, -3], [60, -9]])],
      vertical: [row([[0, 0], [30, -6], [60, -15]])],
    },
    directivity_phase: {
      horizontal: [[[0, -20], [30, -55], [60, -110]]],
      vertical: [[[0, -20], [30, -80], [60, -140]]],
    },
  });

  it('anchors a secondary plane to spl_on_axis with no per-plane reference', () => {
    const result = twoPlane();
    expect(measurementAnchorRefusal(result, 'vertical')).toBeNull();
    expect(withMeasurementAngle(result, 'vertical', 30).spl_on_axis?.spl).toEqual([88]);
    expect(withMeasurementAngle(result, 'vertical', 60).spl_on_axis?.spl).toEqual([79]);
    expect(withMeasurementAngle(result, 'horizontal', 30).spl_on_axis?.spl).toEqual([91]);
  });

  it('leaves the on-axis selection untouched in every plane', () => {
    const result = twoPlane();
    expect(withMeasurementAngle(result, 'horizontal', 0)).toBe(result);
    expect(withMeasurementAngle(result, 'vertical', 0)).toBe(result);
  });

  it('agrees with an explicit per-plane reference that says the same thing', () => {
    const withReference = twoPlane();
    withReference.metadata = {
      spl_on_axis: {
        requested_angle_degrees: 0,
        sampled_angle_degrees: 0,
        plane_reference_spl_db: { horizontal: [94], vertical: [94] },
      },
    };
    expect(withMeasurementAngle(withReference, 'vertical', 30).spl_on_axis?.spl)
      .toEqual(withMeasurementAngle(twoPlane(), 'vertical', 30).spl_on_axis?.spl);
  });
});

import { describe, expect, it } from 'vitest';
import type { PolarSample } from '../api/results';
import type { ResultPayload } from './types';
import {
  measurementAngleLabel,
  measurementAngles,
  measurementPlanes,
  nearestMeasurementAngle,
  onAxisAngle,
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

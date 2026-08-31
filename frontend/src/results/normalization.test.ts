import { describe, expect, it } from 'vitest';
import type { PolarSample } from '../api/results';
import type { ResultPayload } from './types';
import {
  levelAtAngle,
  renormalizeRows,
  resolveNormalizationAngle,
  sampledAngleRange,
  withNormalizationAngle,
} from './normalization';

const row = (levels: Array<[number, number | null]>): PolarSample[] =>
  levels.map(([angle, level]) => [angle, level] as PolarSample);

function result(rows: PolarSample[][], metadata?: Record<string, unknown>): ResultPayload {
  return {
    frequencies: rows.map((_, index) => 100 * (index + 1)),
    directivity: { horizontal: rows },
    ...(metadata ? { metadata } : {}),
  } as ResultPayload;
}

describe('levelAtAngle', () => {
  it('interpolates linearly in dB between the bracketing samples', () => {
    expect(levelAtAngle(row([[0, 0], [10, -4], [20, -12]]), 5)).toBe(-2);
    expect(levelAtAngle(row([[0, 0], [10, -4], [20, -12]]), 15)).toBe(-8);
  });

  it('returns a sample exactly when the angle lands on one', () => {
    expect(levelAtAngle(row([[0, 0], [10, -4]]), 10)).toBe(-4);
  });

  // The server clamps the same way (`numpy.interp`), and the angle here is a
  // live UI value that an archived run's sweep may never have reached.
  it('clamps to the nearest measured angle outside the sweep', () => {
    const sweep = row([[10, -1], [20, -5]]);
    expect(levelAtAngle(sweep, -30)).toBe(-1);
    expect(levelAtAngle(sweep, 90)).toBe(-5);
  });

  it('ignores unavailable samples and gives up only when the row is empty', () => {
    expect(levelAtAngle(row([[0, null], [10, -4]]), 0)).toBe(-4);
    expect(levelAtAngle(row([[0, null], [10, null]]), 0)).toBeNull();
  });

  it('reads a complex sample as its magnitude in dB', () => {
    expect(levelAtAngle([[0, [10, 0]]] as PolarSample[], 0)).toBeCloseTo(20, 9);
  });
});

describe('renormalizeRows', () => {
  it('shifts each row so the requested angle reads 0 dB', () => {
    expect(renormalizeRows([row([[0, 0], [10, -3], [20, -9]])], 10)).toEqual([
      [[0, 3], [10, 0], [20, -6]],
    ]);
  });

  it('references each frequency row against its own level at the angle', () => {
    const shifted = renormalizeRows([
      row([[0, 0], [10, -3]]),
      row([[0, 0], [10, -12]]),
    ], 10);
    expect(shifted[0]).toEqual([[0, 3], [10, 0]]);
    expect(shifted[1]).toEqual([[0, 12], [10, 0]]);
  });

  it('leaves unavailable cells unavailable', () => {
    expect(renormalizeRows([row([[0, 0], [10, null], [20, -6]])], 0)).toEqual([
      [[0, 0], [10, null], [20, -6]],
    ]);
  });

  /**
   * The property the whole feature rests on: the server already shifted these
   * rows by an unknown constant, and re-shifting reaches the same answer as if
   * it never had. Without this, an archived run could not be re-referenced.
   */
  it('composes exactly, so a pre-shifted row reaches the same place', () => {
    const raw = row([[0, 94], [10, 91], [20, 85]]);
    const direct = renormalizeRows([raw], 20);
    const viaFive = renormalizeRows(renormalizeRows([raw], 5), 20);
    expect(viaFive).toEqual(direct);
  });
});

describe('resolveNormalizationAngle', () => {
  it('reports the requested angle when the sweep covers it', () => {
    expect(resolveNormalizationAngle(result([row([[0, 0], [30, -6]])]), 15))
      .toEqual({ angle: 15, clamped: false });
  });

  it('reports the clamp when it does not', () => {
    expect(resolveNormalizationAngle(result([row([[0, 0], [30, -6]])]), 45))
      .toEqual({ angle: 30, clamped: true });
  });

  it('falls through unchanged when the result carries no patterns', () => {
    expect(resolveNormalizationAngle({ frequencies: [] } as unknown as ResultPayload, 5))
      .toEqual({ angle: 5, clamped: false });
  });
});

describe('sampledAngleRange', () => {
  it('spans every plane the result carries', () => {
    const payload = {
      frequencies: [100],
      directivity: {
        horizontal: [row([[-30, -6], [0, 0]])],
        vertical: [row([[0, 0], [60, -12]])],
      },
    } as ResultPayload;
    expect(sampledAngleRange(payload)).toEqual([-30, 60]);
  });

  it('is null when nothing measurable is present', () => {
    expect(sampledAngleRange({ frequencies: [100] } as ResultPayload)).toBeNull();
  });
});

describe('withNormalizationAngle', () => {
  it('re-references the patterns and restates the angle in metadata', () => {
    const payload = result([row([[0, 0], [10, -3], [20, -9]])], {
      directivity: { normalization_angle_degrees: 0, distance_m: 2 },
    });
    const shifted = withNormalizationAngle(payload, 10);
    expect(shifted.directivity?.horizontal).toEqual([[[0, 3], [10, 0], [20, -6]]]);
    expect(shifted.metadata?.directivity).toEqual({
      normalization_angle_degrees: 10,
      distance_m: 2,
    });
  });

  it('states the clamped angle in metadata, not the one that was asked for', () => {
    const payload = result([row([[0, 0], [30, -6]])], {
      directivity: { normalization_angle_degrees: 0 },
    });
    expect(withNormalizationAngle(payload, 90).metadata?.directivity)
      .toEqual({ normalization_angle_degrees: 30 });
  });

  it('never moves absolute SPL or raw phase', () => {
    const payload = {
      ...result([row([[0, 0], [10, -3]])]),
      spl_on_axis: { frequencies: [100], spl: [94], phase_degrees: [-20] },
      directivity_phase: { horizontal: [[[0, -20], [10, -35]]] },
    } as ResultPayload;
    const shifted = withNormalizationAngle(payload, 10);
    expect(shifted.spl_on_axis).toBe(payload.spl_on_axis);
    expect(shifted.directivity_phase).toBe(payload.directivity_phase);
  });

  it('returns the same object for the same angle, and the input when there is nothing to shift', () => {
    const payload = result([row([[0, 0], [10, -3]])]);
    expect(withNormalizationAngle(payload, 10)).toBe(withNormalizationAngle(payload, 10));
    const empty = { frequencies: [100] } as ResultPayload;
    expect(withNormalizationAngle(empty, 10)).toBe(empty);
  });
});

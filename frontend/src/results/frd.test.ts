import { describe, expect, it, vi } from 'vitest';
import { applySmoothing, type SmoothingMode } from './smoothing';
import type { ResultPayload } from './types';
import { buildOnAxisFrd, buildPolarFrdSet } from './frd';

const frequencies = [100, 200, 400, 800, 1_600];

const fixture: ResultPayload = {
  frequencies,
  spl_on_axis: {
    frequencies,
    spl: [89.25, 91.5, 90.125, 94.75, 93.375],
    phase_degrees: [-72.5, -31.25, 8.75, 46.5, 103.125],
  },
  directivity: {
    horizontal: frequencies.map((_, index) => [
      [-30, -12 - index],
      [0, -index * 0.25],
      [30, -10 - index * 0.5],
    ]),
    vertical: frequencies.map((_, index) => [
      [-15, -8 - index],
      [0, -index * 0.25],
      [15, -7 - index * 0.75],
    ]),
  },
};

function preferences(smoothing: SmoothingMode = 'none') {
  return { smoothing, outputName: 'test_horn', counter: 7 };
}

function dataRows(text: string): string[][] {
  return text.trim().split('\n')
    .filter((line) => line.length > 0 && !line.startsWith('*'))
    .map((line) => line.split('\t'));
}

describe('FRD builders', () => {
  it('writes on-axis frequency, smoothed SPL, and the real varying phase', () => {
    const text = buildOnAxisFrd(fixture, preferences());
    const rows = dataRows(text);

    expect(rows).toHaveLength(frequencies.length);
    expect(rows.every((row) => row.length === 3)).toBe(true);
    expect(rows.map((row) => Number(row[2]))).toEqual([-72.5, -31.25, 8.75, 46.5, 103.125]);
    expect(new Set(rows.map((row) => row[2])).size).toBeGreaterThan(1);
    expect(rows.some((row) => Number(row[2]) !== 0)).toBe(true);
  });

  it('applies the smoothing preference to on-axis and polar SPL only', () => {
    const smoothingFrequencies = [100, 119, 141, 168, 200];
    const spiky: ResultPayload = {
      ...fixture,
      frequencies: smoothingFrequencies,
      spl_on_axis: {
        frequencies: smoothingFrequencies,
        spl: [0, 0, 12, 0, 0],
        phase_degrees: [-20, -10, 0, 10, 20],
      },
      directivity: {
        horizontal: smoothingFrequencies.map((_, index) => [[0, [0, 0, 12, 0, 0][index]]]),
      },
    };
    const expected = applySmoothing(smoothingFrequencies, [0, 0, 12, 0, 0], '1/1');
    const onAxisRows = dataRows(buildOnAxisFrd(spiky, preferences('1/1')));
    const polarRows = dataRows(buildPolarFrdSet(spiky, preferences('1/1'))[0].text);

    expect(Number(onAxisRows[2][1])).toBeCloseTo(Number(expected[2]), 3);
    expect(Number(onAxisRows[2][1])).not.toBe(12);
    expect(Number(onAxisRows[2][2])).toBe(0);
    expect(Number(polarRows[2][1])).toBeCloseTo(Number(expected[2]), 3);
  });

  it('writes every polar as explicitly magnitude-only two-column data', () => {
    const files = buildPolarFrdSet(fixture, preferences());

    expect(files).toHaveLength(6);
    files.forEach(({ text }) => {
      expect(text).toContain('magnitude-only; phase is not available and is intentionally omitted');
      expect(dataRows(text).every((row) => row.length === 2)).toBe(true);
      expect(dataRows(text)).toHaveLength(frequencies.length);
    });
  });

  it('uses the VACS spherical filename convention for both signed planes', () => {
    expect(buildPolarFrdSet(fixture, preferences()).map(({ filename }) => filename)).toEqual([
      'test_horn_7 Phi180Theta030.frd',
      'test_horn_7 Phi000Theta000.frd',
      'test_horn_7 Phi000Theta030.frd',
      'test_horn_7 Phi270Theta015.frd',
      'test_horn_7 Phi090Theta000.frd',
      'test_horn_7 Phi090Theta015.frd',
    ]);
  });

  it('always uses period decimals without locale formatting or grouping', () => {
    const localeSpy = vi.spyOn(Number.prototype, 'toLocaleString').mockReturnValue('100,500');
    const decimalFixture: ResultPayload = {
      frequencies: [100.5, 1e21],
      spl_on_axis: {
        frequencies: [100.5, 1e21],
        spl: [90.25, 91.5],
        phase_degrees: [-12.75, 15.25],
      },
    };

    const text = buildOnAxisFrd(decimalFixture, preferences());
    expect(text).toContain('100.500000\t90.250\t-12.7500');
    expect(text).not.toContain('100,500');
    expect(text).not.toMatch(/\d[eE][+-]?\d/);
    expect(localeSpy).not.toHaveBeenCalled();
    localeSpy.mockRestore();
  });

  it('returns an empty set when there is no directivity', () => {
    expect(buildPolarFrdSet({ frequencies }, preferences())).toEqual([]);
    expect(buildPolarFrdSet({ ...fixture, directivity: {} }, preferences())).toEqual([]);
  });

  it('omits incomplete rows instead of inventing or blanking numeric cells', () => {
    const incomplete: ResultPayload = {
      frequencies: [100, 200, Number.NaN, 400],
      spl_on_axis: {
        frequencies: [100, 200, Number.NaN, 400],
        spl: [90, 91, 92, Number.POSITIVE_INFINITY],
        phase_degrees: [-10, null, 20, 30],
      },
      directivity: {
        horizontal: [
          [[0, 0]],
          [[0, null]],
          [[0, -2]],
          [[0, Number.POSITIVE_INFINITY]],
        ],
      },
    };

    expect(dataRows(buildOnAxisFrd(incomplete, preferences()))).toEqual([
      ['100.000000', '90.000', '-10.0000'],
    ]);
    expect(dataRows(buildPolarFrdSet(incomplete, preferences())[0].text)).toEqual([
      ['100.000000', '0.000'],
    ]);
  });
});

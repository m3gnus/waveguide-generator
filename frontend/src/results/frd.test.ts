import { describe, expect, it, vi } from 'vitest';
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

function withPolarPhase(result: ResultPayload = fixture): ResultPayload {
  return {
    ...result,
    directivity_phase: {
      horizontal: frequencies.map((_, index) => [
        [-30, -100 + index], [0, -80 + index], [30, -60 + index],
      ]),
      vertical: frequencies.map((_, index) => [
        [-15, -50 + index], [0, -30 + index], [15, -10 + index],
      ]),
    },
  };
}

function dataRows(text: string): string[][] {
  return text.trim().split('\n')
    .filter((line) => line.length > 0 && !line.startsWith('*'))
    .map((line) => line.split('\t'));
}

function rewVituixCadRows(text: string): string[][] {
  return text.trim().split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith('*'))
    .map((line) => line.split(/[\t ;]+/));
}

describe('FRD builders', () => {
  it('keeps exact decimal bytes for large levels, rounding boundaries and signed zero', () => {
    const levels = [1e21, -1e21, 1.23456789e25, -1.23456789e25, 1.23445, 1.23455, -0, -0.00001];
    const result: ResultPayload = {
      frequencies: levels.map((_, index) => index + 1),
      spl_on_axis: { spl: levels, phase_degrees: levels.map(() => 0) },
      directivity: { horizontal: levels.map((level) => [[0, level]]) },
    };
    expect({
      onAxis: buildOnAxisFrd(result),
      polar: buildPolarFrdSet(result, 'decimals'),
    }).toMatchSnapshot();
  });

  it('accepts a channel scope without combining the flat wrapper', () => {
    const wrapped: ResultPayload = {
      frequencies: [],
      channels: { first: fixture, second: { ...fixture, spl_on_axis: { frequencies, spl: [70, 71, 72, 73, 74], phase_degrees: [0, 0, 0, 0, 0] } } },
      channel_order: ['first', 'second'],
    };
    expect(() => buildOnAxisFrd(wrapped)).toThrow('Choose a drive channel');
    const rows = dataRows(buildOnAxisFrd(wrapped, 'second'));
    expect(rows.map((row) => Number(row[1]))).toEqual([70, 71, 72, 73, 74]);
  });

  it('writes on-axis frequency, smoothed SPL, and the real varying phase', () => {
    const text = buildOnAxisFrd(fixture);
    const rows = dataRows(text);

    expect(rows).toHaveLength(frequencies.length);
    expect(rows.every((row) => row.length === 3)).toBe(true);
    expect(rows.map((row) => Number(row[2]))).toEqual([-72.5, -31.25, 8.75, 46.5, 103.125]);
    expect(new Set(rows.map((row) => row[2])).size).toBeGreaterThan(1);
    expect(rows.some((row) => Number(row[2]) !== 0)).toBe(true);
  });

  it('keeps convention metadata in comments without changing REW/VituixCAD data rows', () => {
    const documented: ResultPayload = {
      ...withPolarPhase(),
      metadata: {
        phase_time_convention: 'exp(+ikr)',
        observation: {
          effective_distance_m: 1.5,
          requested_distance_m: 2,
          observation_origin: 'mouth',
        },
        directivity: { normalization_angle_degrees: 5 },
      },
    };
    const onAxis = buildOnAxisFrd(documented);
    const polar = buildPolarFrdSet(documented, 'test_horn_7')[0].text;

    expect(onAxis).toContain("* Phasor convention: engineering exp(+j omega t); converted from the solver's exp(-i omega t)");
    expect(onAxis).toContain('* Phase sign: a later arrival is a phase lag (falling phase), as REW and VituixCAD expect');
    expect(onAxis).toContain('* Observation distance: 1.5 m effective; requested 2 m');
    expect(onAxis).toContain('* Observation origin: mouth');
    expect(onAxis).toContain('* Normalization: absolute SPL re 20 µPa; level normalization none');
    expect(polar).toContain('* Normalization: per-frequency polar level; 0 dB at 5 deg');
    expect(rewVituixCadRows(onAxis)).toEqual([
      // exp(+ikr) raw phase, conjugated to engineering phase.
      ['100.000000', '89.2500', '72.5000'],
      ['200.000000', '91.5000', '31.2500'],
      ['400.000000', '90.1250', '-8.7500'],
      ['800.000000', '94.7500', '-46.5000'],
      ['1600.000000', '93.3750', '-103.1250'],
    ]);
    expect(rewVituixCadRows(polar)).toEqual([
      ['100.000000', '-12.0000', '100.0000'],
      ['200.000000', '-13.0000', '99.0000'],
      ['400.000000', '-14.0000', '98.0000'],
      ['800.000000', '-15.0000', '97.0000'],
      ['1600.000000', '-16.0000', '96.0000'],
    ]);
  });

  it('writes unsmoothed magnitude beside raw phase, whatever the charts smooth', () => {
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
    const onAxis = buildOnAxisFrd(spiky);
    const polar = buildPolarFrdSet(spiky, 'test_horn_7')[0].text;

    expect(dataRows(onAxis).map((row) => Number(row[1]))).toEqual([0, 0, 12, 0, 0]);
    expect(dataRows(polar).map((row) => Number(row[1]))).toEqual([0, 0, 12, 0, 0]);
    expect(onAxis).toContain('* Smoothing: none (FRD files are always unsmoothed; smooth in the receiving tool)');
  });

  it('writes every polar as explicitly magnitude-only two-column data', () => {
    const files = buildPolarFrdSet(fixture, 'test_horn_7');

    expect(files).toHaveLength(6);
    files.forEach(({ text }) => {
      expect(text).toContain('magnitude-only; phase is not available and is intentionally omitted');
      expect(dataRows(text).every((row) => row.length === 2)).toBe(true);
      expect(dataRows(text)).toHaveLength(frequencies.length);
    });
  });

  it('writes three-column polar files without a magnitude-only header when phase is present', () => {
    const files = buildPolarFrdSet(withPolarPhase(), 'test_horn_7');

    expect(files).toHaveLength(6);
    files.forEach(({ text }) => {
      expect(text).not.toContain('magnitude-only');
      // Relative, not absolute: the on-axis file beside it is the one carrying SPL re 20 uPa.
      expect(text).toContain('Freq(Hz)\tSPL_rel(dB)\tPhase(degrees)');
      expect(dataRows(text)).toHaveLength(frequencies.length);
      expect(dataRows(text).every((row) => row.length === 3)).toBe(true);
    });
  });

  it('removes the common time-of-flight and wraps corrected polar phase to (-180, 180]', () => {
    const result: ResultPayload = {
      frequencies: [1],
      directivity: { horizontal: [[[0, 0]]] },
      directivity_phase: { horizontal: [[[0, -100]]] },
      metadata: {
        phase_time_convention: 'exp(+ikr)',
        observation: { effective_distance_m: 1, sound_speed_m_per_s: 4 },
      },
    };
    const text = buildPolarFrdSet(result, 'test_horn_7')[0].text;

    // Raw: -100 - 360 * 1 Hz * 1 m / 4 m/s = -190 deg; engineering +190 deg, wrapped to -170 deg.
    expect(dataRows(text)).toEqual([['1.000000', '0.0000', '-170.0000']]);
    expect(text).toContain('d=1 m, c=4 m/s');
  });

  it.each([
    ['distance', { sound_speed_m_per_s: 4 }],
    ['speed of sound', { effective_distance_m: 1 }],
  ])('emits raw polar phase and an honest header when %s metadata is missing', (_missing, observation) => {
    const result: ResultPayload = {
      frequencies: [1],
      directivity: { horizontal: [[[0, 0]]] },
      directivity_phase: { horizontal: [[[0, -100]]] },
      metadata: { observation },
    };
    const text = buildPolarFrdSet(result, 'test_horn_7')[0].text;

    expect(dataRows(text)).toEqual([['1.000000', '0.0000', '-100.0000']]);
    expect(text).toContain('Common time-of-flight not removed');
    expect(text).toContain('raw phase emitted');
  });

  it('uses the same propagation correction for on-axis phase', () => {
    const result: ResultPayload = {
      frequencies: [1],
      spl_on_axis: { frequencies: [1], spl: [90], phase_degrees: [-100] },
      metadata: {
        phase_time_convention: 'exp(+ikr)',
        observation: { effective_distance_m: 1, sound_speed_m_per_s: 4 },
      },
    };

    expect(dataRows(buildOnAxisFrd(result))).toEqual([
      ['1.000000', '90.0000', '-170.0000'],
    ]);
  });

  it('adds propagation phase for the explicit exp(-ikr) convention', () => {
    const result: ResultPayload = {
      frequencies: [1],
      directivity: { horizontal: [[[0, 0]]] },
      directivity_phase: { horizontal: [[[0, -100]]] },
      metadata: {
        phase_time_convention: 'exp(-ikr)',
        observation: { effective_distance_m: 1, sound_speed_m_per_s: 4 },
      },
    };

    expect(dataRows(buildPolarFrdSet(result, 'test_horn_7')[0].text)).toEqual([
      ['1.000000', '0.0000', '-10.0000'],
    ]);
  });

  it('writes a pure delay as engineering phase lag, -360 f tau', () => {
    // Solver convention exp(-i omega t): a delay tau multiplies the phasor by
    // exp(+i omega tau), so the raw angle rises. REW and VituixCAD read a delay
    // as exp(-j omega tau): falling phase.
    const tauS = 0.25e-3;
    const delayFrequencies = [100, 500, 1_000];
    const result: ResultPayload = {
      frequencies: delayFrequencies,
      spl_on_axis: {
        frequencies: delayFrequencies,
        spl: [90, 90, 90],
        phase_degrees: delayFrequencies.map((frequency) => 360 * frequency * tauS),
      },
      metadata: { phase_time_convention: 'exp(+ikr)' },
    };

    expect(dataRows(buildOnAxisFrd(result)).map((row) => Number(row[2])))
      .toEqual([-9, -45, -90]);
  });

  it('does not invent a correction sign when phase convention metadata is missing', () => {
    const result: ResultPayload = {
      frequencies: [1],
      spl_on_axis: { frequencies: [1], spl: [90], phase_degrees: [-100] },
      metadata: {
        observation: { effective_distance_m: 1, sound_speed_m_per_s: 4 },
      },
    };
    const text = buildOnAxisFrd(result);

    expect(dataRows(text)).toEqual([['1.000000', '90.0000', '-100.0000']]);
    expect(text).toContain('phase convention metadata is unavailable');
  });

  it('names polar files the way the Fusion addin already does for VituixCAD', () => {
    expect(buildPolarFrdSet(fixture, 'test_horn_7').map(({ filename }) => filename)).toEqual([
      'hor/test_horn_7 -30.frd',
      'hor/test_horn_7 0.frd',
      'hor/test_horn_7 30.frd',
      'ver/test_horn_7 -15.frd',
      'ver/test_horn_7 0.frd',
      'ver/test_horn_7 15.frd',
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

    const text = buildOnAxisFrd(decimalFixture);
    expect(text).toContain('100.500000\t90.2500\t-12.7500');
    expect(text).not.toContain('100,500');
    expect(text).not.toMatch(/\d[eE][+-]?\d/);
    expect(localeSpy).not.toHaveBeenCalled();
    localeSpy.mockRestore();
  });

  it('returns an empty set when there is no directivity', () => {
    expect(buildPolarFrdSet({ frequencies }, 'test_horn_7')).toEqual([]);
    expect(buildPolarFrdSet({ ...fixture, directivity: {} }, 'test_horn_7')).toEqual([]);
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

    expect(dataRows(buildOnAxisFrd(incomplete))).toEqual([
      ['100.000000', '90.0000', '-10.0000'],
    ]);
    expect(dataRows(buildPolarFrdSet(incomplete, 'test_horn_7')[0].text)).toEqual([
      ['100.000000', '0.0000'],
    ]);
  });
});

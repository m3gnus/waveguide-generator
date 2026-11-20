import { describe, expect, it } from 'vitest';
import { applySmoothing, SMOOTHING_MODES, type SmoothingMode } from './smoothing';
import { SERIES_FIXTURES } from './series.fixtures';
import { flatDi, powerResponseOnDiGrid, smoothedExportSeries } from './series';
import { buildDerivedAcoustics } from './derivedAcoustics';
import { directivityIndexSeries, impedanceSeries, powerResponseSeries, splSeries } from './mappers';
import type { ResultPayload } from './types';

/*
 * The series layer replaced three copies of the same rules. These are those
 * copies as they stood before (WG dev ef17eccb1), frozen as oracles: every
 * consumer must produce exactly what its own copy produced, smoothing order
 * included.
 */
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/** mappers.ts `splSeries` data, `impedanceSeries`, `directivityIndexSeries` and `powerResponseSeries` values. */
const screenOracle = {
  spl(result: ResultPayload, smoothing: SmoothingMode) {
    const frequencies = result.spl_on_axis?.frequencies?.length ? result.spl_on_axis.frequencies : result.frequencies;
    const spl = applySmoothing(frequencies, result.spl_on_axis?.spl ?? [], smoothing, 'level_db');
    return frequencies.map((frequency, index) => [frequency, spl[index] ?? null]);
  },
  impedance(result: ResultPayload, smoothing: SmoothingMode) {
    const frequencies = result.impedance?.frequencies?.length ? result.impedance.frequencies : result.frequencies;
    const real = applySmoothing(frequencies, result.impedance?.real ?? [], smoothing, 'linear');
    const imaginary = applySmoothing(frequencies, result.impedance?.imaginary ?? [], smoothing, 'linear');
    return [frequencies.map((f, i) => [f, real[i] ?? null]), frequencies.map((f, i) => [f, imaginary[i] ?? null])];
  },
  di(result: ResultPayload, smoothing: SmoothingMode) {
    const frequencies = result.di?.frequencies?.length ? result.di.frequencies : result.frequencies;
    const raw = result.di?.di;
    const entries = Array.isArray(raw) ? [['DI', raw] as const] : Object.entries(raw ?? {});
    return entries.filter(([, values]) => values.some(finite)).map(([name, values]) => {
      const smoothed = applySmoothing(frequencies, values, smoothing, 'level_db');
      return { name, data: frequencies.map((frequency, index) => [frequency, smoothed[index] ?? null]) };
    });
  },
  power(result: ResultPayload, smoothing: SmoothingMode) {
    const frequencies = result.di?.frequencies?.length ? result.di.frequencies : result.frequencies;
    const rawDi = result.di?.di;
    const di = Array.isArray(rawDi) ? rawDi : rawDi?.horizontal ?? Object.values(rawDi ?? {})[0] ?? [];
    const splFrequencies = result.spl_on_axis?.frequencies?.length ? result.spl_on_axis.frequencies : result.frequencies;
    const splByFrequency = new Map(splFrequencies.map((frequency, index) => [frequency, result.spl_on_axis?.spl?.[index] ?? null]));
    const raw = frequencies.map((frequency, index) => {
      const onAxis = splByFrequency.get(frequency);
      const directivityIndex = di[index];
      return finite(onAxis) && finite(directivityIndex) ? onAxis - directivityIndex : null;
    });
    if (!raw.some(finite)) return [];
    const values = applySmoothing(frequencies, raw, smoothing, 'level_db');
    return frequencies.map((frequency, index) => [frequency, values[index] ?? null]);
  },
};

/** exporters.ts `smoothedSeries` (CSV, summary and PNG payload). */
function exportOracle(result: ResultPayload, smoothing: SmoothingMode) {
  const frequencies = result.spl_on_axis?.frequencies?.length ? result.spl_on_axis.frequencies : result.frequencies;
  const impedanceFrequencies = result.impedance?.frequencies?.length ? result.impedance.frequencies : frequencies;
  const diFrequencies = result.di?.frequencies?.length ? result.di.frequencies : frequencies;
  const values = result.di?.di;
  const flat = Array.isArray(values) ? values : !values ? [] : values.horizontal ?? Object.values(values)[0] ?? [];
  return {
    frequencies,
    spl: applySmoothing(frequencies, result.spl_on_axis?.spl ?? [], smoothing, 'level_db'),
    diFrequencies,
    di: applySmoothing(diFrequencies, flat, smoothing, 'level_db'),
    impedanceFrequencies,
    impedanceReal: applySmoothing(impedanceFrequencies, result.impedance?.real ?? [], smoothing, 'linear'),
    impedanceImaginary: applySmoothing(impedanceFrequencies, result.impedance?.imaginary ?? [], smoothing, 'linear'),
  };
}

/** derivedAcoustics.ts power response column: unsmoothed SPL minus DI per row, where both are present. */
function derivedPowerOracle(rows: ReturnType<typeof buildDerivedAcoustics>['rows']) {
  return rows.map(({ on_axis_spl_db: onAxis, directivity_index_db: di }) => (onAxis !== null && di !== null ? onAxis - di : null));
}

const cases = Object.entries(SERIES_FIXTURES).flatMap(([name, result]) => (
  SMOOTHING_MODES.map(([mode]) => [name, mode, result] as const)
));

describe('one series layer keeps every consumer’s values', () => {
  it.each(cases)('screen series: %s, smoothing %s', (_name, mode, result) => {
    const item = [{ id: 'run', label: 'Run', result }];
    expect(splSeries(item, mode).map(({ data }) => data)).toEqual([screenOracle.spl(result, mode)]);
    expect(impedanceSeries(result, 'cartesian', mode).map(({ data }) => data)).toEqual(screenOracle.impedance(result, mode));
    expect(directivityIndexSeries(result, mode).map(({ name, data }) => ({ name, data }))).toEqual(screenOracle.di(result, mode));
    const power = powerResponseSeries(result, mode);
    expect(power.map(({ data }) => data)).toEqual(screenOracle.power(result, mode).length ? [screenOracle.power(result, mode)] : []);
  });

  it.each(cases)('export series: %s, smoothing %s', (_name, mode, result) => {
    expect(smoothedExportSeries(result, mode)).toEqual(exportOracle(result, mode));
  });

  it.each(Object.entries(SERIES_FIXTURES))('derived power response: %s', (_name, result) => {
    const { rows } = buildDerivedAcoustics(result);
    expect(rows.map(({ power_response_db_spl_avg: value }) => value)).toEqual(derivedPowerOracle(rows));
  });

  it('smooths the screen power response after forming it', () => {
    const result = SERIES_FIXTURES['shared grid, plane DI'];
    const screen = powerResponseSeries(result, '1/3')[0].data.map(([, value]) => value);
    const onDiGrid = powerResponseOnDiGrid(result);
    // The screen forms SPL - DI on the DI grid, then smooths that one curve.
    expect(screen).toEqual(applySmoothing(onDiGrid.frequencies, onDiGrid.values, '1/3', 'level_db'));
    // The DI every flat curve shows is the horizontal plane.
    expect(flatDi(result)).toBe((result.di!.di as Record<string, number[]>).horizontal);
  });
});

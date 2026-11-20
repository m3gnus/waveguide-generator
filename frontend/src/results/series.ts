import { applySmoothing, type SmoothingMode, type SmoothingValue } from './smoothing';
import type { ResultPayload } from './types';

/**
 * The curves every consumer derives from a result, defined once.
 *
 * The screen (`mappers.ts`), the CSV/summary/PNG-payload exports
 * (`exporters.ts`) and the derived-acoustics sidecar the HTML report reads
 * (`derivedAcoustics.ts`) all need the same four rules: which frequency grid
 * a quantity is on, which DI curve a flat chart or column shows, how the power
 * response is formed, and how a curve is smoothed. Each lives here.
 *
 * What stays with each consumer is its order of operations, because changing
 * it would change the numbers it has always written:
 * - the screen forms the power response first and smooths the difference
 *   (`powerResponseOnDiGrid`, then `smoothLevels`);
 * - the exports smooth SPL and DI separately and never form a power response
 *   (`smoothedExportSeries`);
 * - the derived sidecar forms the power response from unsmoothed values on the
 *   union of every grid (`powerResponseDb` per row).
 */

/** A quantity's own frequency grid when it has one, else `fallback`. */
export function seriesFrequencies(own: number[] | undefined, fallback: number[]): number[] {
  return own?.length ? own : fallback;
}

/** The on-axis SPL grid: its own, else the result's. */
export function splFrequencies(result: Pick<ResultPayload, 'frequencies' | 'spl_on_axis'>): number[] {
  return seriesFrequencies(result.spl_on_axis?.frequencies, result.frequencies);
}

/** The DI series to show: a flat array as it is, else the horizontal plane, else the first plane. */
export function flatDi(result: Pick<ResultPayload, 'di'>): Array<number | null> {
  const values = result.di?.di;
  if (Array.isArray(values)) return values;
  if (!values) return [];
  return values.horizontal ?? Object.values(values)[0] ?? [];
}

function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Spatially averaged SPL: on-axis SPL minus directivity index, where both are numbers. */
export function powerResponseDb(onAxisSpl: unknown, directivityIndex: unknown): number | null {
  return finiteNumber(onAxisSpl) && finiteNumber(directivityIndex) ? onAxisSpl - directivityIndex : null;
}

/** Smooth a curve of levels in dB (SPL, DI, power response). */
export function smoothLevels(frequencies: number[], values: SmoothingValue[], smoothing: SmoothingMode): SmoothingValue[] {
  return applySmoothing(frequencies, values, smoothing, 'level_db');
}

/** Smooth a linear quantity (impedance components). */
export function smoothLinear(frequencies: number[], values: SmoothingValue[], smoothing: SmoothingMode): SmoothingValue[] {
  return applySmoothing(frequencies, values, smoothing, 'linear');
}

/**
 * The unsmoothed power response on the DI grid, as the screen draws it.
 *
 * Each DI sample is paired with the on-axis SPL at the same frequency (the last
 * SPL sample listed at it); a frequency with no SPL, or a missing value, is null.
 */
export function powerResponseOnDiGrid(result: ResultPayload): { frequencies: number[]; values: Array<number | null> } {
  const frequencies = seriesFrequencies(result.di?.frequencies, result.frequencies);
  const di = flatDi(result);
  const splByFrequency = new Map(splFrequencies(result).map((frequency, index) => [
    frequency,
    result.spl_on_axis?.spl?.[index] ?? null,
  ]));
  return {
    frequencies,
    values: frequencies.map((frequency, index) => powerResponseDb(splByFrequency.get(frequency), di[index])),
  };
}

/**
 * SPL, DI and impedance as the exports write them: each smoothed on its own
 * grid. DI and impedance without a grid of their own take the SPL grid.
 */
export function smoothedExportSeries(result: ResultPayload, smoothing: SmoothingMode) {
  const frequencies = splFrequencies(result);
  const impedanceFrequencies = seriesFrequencies(result.impedance?.frequencies, frequencies);
  const diFrequencies = seriesFrequencies(result.di?.frequencies, frequencies);
  return {
    frequencies,
    spl: smoothLevels(frequencies, result.spl_on_axis?.spl ?? [], smoothing),
    diFrequencies,
    di: smoothLevels(diFrequencies, flatDi(result), smoothing),
    impedanceFrequencies,
    impedanceReal: smoothLinear(impedanceFrequencies, result.impedance?.real ?? [], smoothing),
    impedanceImaginary: smoothLinear(impedanceFrequencies, result.impedance?.imaginary ?? [], smoothing),
  };
}

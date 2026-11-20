import type { ResultPayload } from './types';

/**
 * Results that exercise every rule in `series.ts`: shared and separate grids,
 * plane and flat DI, missing quantities, gaps, non-finite samples and repeated
 * frequencies. Used by the series identity test.
 */
const grid = (count: number, start = 100, ratio = 1.25) => Array.from({ length: count }, (_, index) => Number((start * ratio ** index).toFixed(3)));
const wave = (frequencies: number[], offset: number, swing: number, period = 3) => frequencies.map((_, index) => offset + swing * Math.sin(index / period) + (index % 4) * 0.37);

const shared = grid(24);
const own = grid(17, 120, 1.31);
const coarse = grid(9, 90, 1.7);

export const SERIES_FIXTURES: Record<string, ResultPayload> = {
  'shared grid, plane DI': {
    frequencies: shared,
    spl_on_axis: { frequencies: shared, spl: wave(shared, 94, 3) },
    di: { frequencies: shared, di: { horizontal: wave(shared, 8, 2), vertical: wave(shared, 10, 1.5) } },
    impedance: { frequencies: shared, real: wave(shared, 0.9, 0.2), imaginary: wave(shared, 0.1, 0.3) },
  } as unknown as ResultPayload,
  'separate grids, flat DI with gaps': {
    frequencies: shared,
    spl_on_axis: { frequencies: own, spl: wave(own, 92, 4).map((value, index) => (index === 5 ? null : value)) },
    di: { frequencies: coarse, di: wave(coarse, 7, 3).map((value, index) => (index === 2 ? null : value)) },
    impedance: { frequencies: own, real: wave(own, 1.1, 0.4), imaginary: wave(own, -0.2, 0.5) },
  } as unknown as ResultPayload,
  'no own grids, vertical-only DI, no impedance': {
    frequencies: shared,
    spl_on_axis: { frequencies: [], spl: wave(shared, 90, 5) },
    di: { di: { vertical: wave(shared, 9, 2.5) } },
  } as unknown as ResultPayload,
  'repeated SPL frequencies, non-finite samples, DI off the SPL grid': {
    frequencies: shared,
    spl_on_axis: {
      frequencies: [...shared.slice(0, 10), shared[9], ...shared.slice(10, 20)],
      spl: [...wave(shared.slice(0, 10), 95, 2), 101.5, ...wave(shared.slice(10, 20), 93, 2)]
        .map((value, index) => (index === 3 ? Number.NaN : value)),
    },
    di: { frequencies: [shared[1], 77.7, ...shared.slice(4, 22)], di: { horizontal: wave(shared.slice(0, 20), 6, 2) } },
    impedance: { frequencies: [], real: wave(shared, 0.8, 0.1), imaginary: wave(shared, 0.05, 0.1) },
  } as unknown as ResultPayload,
  'no DI at all': {
    frequencies: coarse,
    spl_on_axis: { spl: wave(coarse, 88, 6) },
    impedance: { real: wave(coarse, 1, 0.3), imaginary: wave(coarse, 0, 0.2) },
  } as unknown as ResultPayload,
};

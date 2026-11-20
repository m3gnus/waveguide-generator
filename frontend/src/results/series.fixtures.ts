import type { ResultPayload } from './types';

/**
 * Results that exercise every rule in `series.ts` and every grid fallback that
 * reads it: shared and separate grids, plane and flat DI, missing quantities,
 * gaps, non-finite samples, repeated frequencies, polar rows, on-axis phase,
 * beam fits with and without their own grid, and a driven (ohms) channel.
 * Used by the series identity test and its frozen capture
 * (`series.capture.json`); changing a fixture means recapturing from old code.
 */
const grid = (count: number, start = 100, ratio = 1.25) => Array.from({ length: count }, (_, index) => Number((start * ratio ** index).toFixed(3)));
const wave = (frequencies: number[], offset: number, swing: number, period = 3) => frequencies.map((_, index) => offset + swing * Math.sin(index / period) + (index % 4) * 0.37);

const shared = grid(24);
const own = grid(17, 120, 1.31);
const coarse = grid(9, 90, 1.7);

const ANGLES = [-30, -15, 0, 15, 30];
const polarRows = (frequencies: number[], level: number) => frequencies.map((_, index) => (
  ANGLES.map((angle) => [angle, level - Math.abs(angle) / 10 - (index % 5) * 0.2 * Math.abs(angle) / 15] as [number, number])
));
const phaseRows = (frequencies: number[]) => frequencies.map((_, index) => (
  ANGLES.map((angle) => [angle, ((index * 37 + angle * 3) % 360) - 180] as [number, number])
));
const observation = { observation: { effective_distance_m: 2, sound_speed_m_per_s: 343 }, phase_time_convention: 'exp(+ikr)' };
const beam = (frequencies: number[]) => ({
  frequencies,
  horizontal_beamwidth_deg: wave(own.slice(0, Math.max(frequencies.length, 9)), 90, 20),
  vertical_beamwidth_deg: wave(own.slice(0, Math.max(frequencies.length, 9)), 60, 15),
  aspect_ratio: wave(own.slice(0, Math.max(frequencies.length, 9)), 1.5, 0.2),
  shape_exponent: wave(own.slice(0, Math.max(frequencies.length, 9)), 2.2, 0.3),
  fit_residual_percent: wave(own.slice(0, Math.max(frequencies.length, 9)), 4, 1),
  spherical_di_db: wave(own.slice(0, Math.max(frequencies.length, 9)), 9, 2),
});

export const SERIES_FIXTURES: Record<string, ResultPayload> = {
  'shared grid, plane DI': {
    frequencies: shared,
    spl_on_axis: { frequencies: shared, spl: wave(shared, 94, 3), phase_degrees: wave(shared, -40, 120, 2) },
    di: { frequencies: shared, di: { horizontal: wave(shared, 8, 2), vertical: wave(shared, 10, 1.5) } },
    impedance: { frequencies: shared, real: wave(shared, 0.9, 0.2), imaginary: wave(shared, 0.1, 0.3) },
    directivity: { horizontal: polarRows(shared, 94), vertical: polarRows(shared, 93) },
    directivity_phase: { horizontal: phaseRows(shared), vertical: phaseRows(shared) },
    beam_shape: beam(coarse),
    balloon: { frequencies: shared },
    metadata: observation,
  } as unknown as ResultPayload,
  'separate grids, flat DI with gaps': {
    frequencies: shared,
    spl_on_axis: {
      frequencies: own,
      spl: wave(own, 92, 4).map((value, index) => (index === 5 ? null : value)),
      phase_degrees: wave(own, 10, 90, 2),
    },
    di: { frequencies: coarse, di: wave(coarse, 7, 3).map((value, index) => (index === 2 ? null : value)) },
    impedance: { frequencies: own, real: wave(own, 1.1, 0.4), imaginary: wave(own, -0.2, 0.5) },
    directivity: { vertical: polarRows(own, 91), horizontal: polarRows(own, 92) },
    beam_shape: beam([]),
    balloon: { frequencies: coarse },
    metadata: observation,
  } as unknown as ResultPayload,
  'no own grids, vertical-only DI, no impedance': {
    frequencies: shared,
    spl_on_axis: { frequencies: [], spl: wave(shared, 90, 5), phase_degrees: wave(shared, 0, 100, 2) },
    di: { di: { vertical: wave(shared, 9, 2.5) } },
    directivity: { horizontal: polarRows(shared, 90) },
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
    beam_shape: beam(shared.slice(0, 12)),
  } as unknown as ResultPayload,
  'no DI at all': {
    frequencies: coarse,
    spl_on_axis: { spl: wave(coarse, 88, 6) },
    impedance: { real: wave(coarse, 1, 0.3), imaginary: wave(coarse, 0, 0.2) },
  } as unknown as ResultPayload,
  'driven channel, ohms on its own grid': {
    frequencies: shared,
    spl_on_axis: { frequencies: shared, spl: wave(shared, 96, 4), phase_degrees: wave(shared, 20, 80, 2) },
    di: { frequencies: shared, di: wave(shared, 7, 2) },
    impedance: { frequencies: own, real: wave(own, 6.5, 2), imaginary: wave(own, 1, 3) },
    metadata: {
      ...observation,
      impedance_units: 'ohms', impedance_quantity: 'electrical_input_impedance',
      impedance_phase_convention: 'engineering-exp-plus-jwt',
      drive: { voltage_v: 2.83, rg_ohm: 0.3 },
    },
  } as unknown as ResultPayload,
  'driven channel, ohms without a grid': {
    frequencies: coarse,
    spl_on_axis: { spl: wave(coarse, 90, 3) },
    impedance: { real: wave(coarse, 7, 1.5), imaginary: wave(coarse, -1, 2) },
    metadata: {
      impedance_units: 'ohms', impedance_quantity: 'electrical_input_impedance',
      passive_cardioid: { drive_voltage_v: 4, rg_ohm: 0.1 },
    },
  } as unknown as ResultPayload,
};

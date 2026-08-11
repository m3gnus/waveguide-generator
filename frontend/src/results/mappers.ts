import type { JobResults, NullableNumber, PolarSample } from '../api/results';
import { applySmoothing, type SmoothingMode } from './smoothing';
import { resultChannels, type ResultPayload } from './types';

export interface NamedResult { id: string; label: string; result: JobResults }

/** Expand an imported result's unit-drive bases into the same named flow as runs. */
export function expandResultChannels(id: string, label: string, result: JobResults): NamedResult[] {
  if (!result.channels) return [{ id, label, result }];
  const channels = resultChannels(result as ResultPayload);
  if (!channels.length) return [{ id, label, result }];
  return channels.map(({ id: channel, result: channelResult }) => ({
    id: `${id}#${channel}`,
    label: `${label} · ${channel}`,
    result: channelResult,
  }));
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function complexToDb(real: number, imaginary: number, reference = 1): number | null {
  const magnitude = Math.hypot(real, imaginary);
  if (!Number.isFinite(magnitude) || magnitude <= 0 || reference <= 0) return null;
  return 20 * Math.log10(magnitude / reference);
}

function patternDb(value: NullableNumber | [number, number]): number | null {
  if (Array.isArray(value)) return complexToDb(Number(value[0]), Number(value[1]));
  return finite(value) ? value : null;
}

function frequencyAxis(result: JobResults, nested?: number[]): number[] {
  return nested?.length ? nested : result.frequencies;
}

export function splSeries(items: NamedResult[], smoothing: SmoothingMode = 'none') {
  return items.map(({ label, result }) => {
    const frequencies = frequencyAxis(result, result.spl_on_axis?.frequencies);
    const spl = applySmoothing(frequencies, result.spl_on_axis?.spl ?? [], smoothing);
    return {
      name: label,
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      data: frequencies.map((frequency, index) => [frequency, spl[index] ?? null]),
    };
  });
}

export interface DirectivityGrid {
  frequencies: number[];
  angles: number[];
  data: Array<[number, number, number | null]>;
  minDb: number;
  maxDb: number;
}

export function directivityGrid(result: JobResults, plane = 'horizontal'): DirectivityGrid {
  const frequencies = result.frequencies;
  const patterns = (result.directivity as Record<string, PolarSample[][]> | undefined)?.[plane] ?? [];
  const angles = [...new Set(patterns.flatMap((row) => row.map((sample) => Number(sample[0]))))]
    .filter(Number.isFinite).sort((a, b) => a - b);
  const data: DirectivityGrid['data'] = [];
  let minDb = 0;
  let maxDb = -Infinity;
  patterns.forEach((row, frequencyIndex) => {
    row.forEach((sample: PolarSample) => {
      const db = patternDb(sample[1]);
      if (db !== null) {
        minDb = Math.min(minDb, db);
        maxDb = Math.max(maxDb, db);
      }
      data.push([frequencies[frequencyIndex], Number(sample[0]), db]);
    });
  });
  return { frequencies, angles, data, minDb: Math.max(-60, Math.floor(minDb / 5) * 5), maxDb: Number.isFinite(maxDb) ? maxDb : 0 };
}

export function nearestFrequencyIndex(frequencies: number[], target: number): number {
  if (!frequencies.length) return 0;
  return frequencies.reduce((best, frequency, index) => (
    Math.abs(frequency - target) < Math.abs(frequencies[best] - target) ? index : best
  ), 0);
}

export function polarSeries(result: JobResults, frequencyIndex: number, plane: 'horizontal' | 'vertical') {
  const row = result.directivity?.[plane]?.[frequencyIndex] ?? [];
  return row.map(([angle, value]) => [patternDb(value), angle]);
}

export function impedanceSeries(result: JobResults, mode: 'cartesian' | 'polar', smoothing: SmoothingMode = 'none') {
  const frequencies = frequencyAxis(result, result.impedance?.frequencies);
  const real = applySmoothing(frequencies, result.impedance?.real ?? [], smoothing);
  const imaginary = applySmoothing(frequencies, result.impedance?.imaginary ?? [], smoothing);
  if (mode === 'cartesian') {
    return [
      { name: 'Re', type: 'line' as const, showSymbol: false, data: frequencies.map((f, i) => [f, real[i] ?? null]) },
      { name: 'Im', type: 'line' as const, showSymbol: false, data: frequencies.map((f, i) => [f, imaginary[i] ?? null]) },
    ];
  }
  return [
    { name: '|Z|', type: 'line' as const, showSymbol: false, data: frequencies.map((f, i) => [f, finite(real[i]) && finite(imaginary[i]) ? Math.hypot(real[i]!, imaginary[i]!) : null]) },
    { name: 'phase', type: 'line' as const, yAxisIndex: 1, showSymbol: false, data: frequencies.map((f, i) => [f, finite(real[i]) && finite(imaginary[i]) ? Math.atan2(imaginary[i]!, real[i]!) * 180 / Math.PI : null]) },
  ];
}

export function directivityIndexSeries(result: ResultPayload, smoothing: SmoothingMode = 'none') {
  const frequencies = result.di?.frequencies?.length ? result.di.frequencies : result.frequencies;
  const raw = result.di?.di;
  const entries = Array.isArray(raw) ? [['DI', raw] as const] : Object.entries(raw ?? {});
  return entries.map(([name, values]) => {
    const smoothed = applySmoothing(frequencies, values, smoothing);
    return {
      name,
      type: 'line' as const,
      showSymbol: false,
      smooth: 0.32,
      smoothMonotone: 'x' as const,
      data: frequencies.map((frequency, index) => [frequency, smoothed[index] ?? null]),
    };
  });
}

export function beamShapeSeries(result: ResultPayload) {
  const beam = result.beam_shape;
  const frequencies = beam?.frequencies?.length ? beam.frequencies : result.balloon?.frequencies ?? [];
  return [
    { name: 'H −6 dB', values: beam?.horizontal_beamwidth_deg ?? [] },
    { name: 'V −6 dB', values: beam?.vertical_beamwidth_deg ?? [] },
  ].map(({ name, values }) => ({
    name,
    type: 'line' as const,
    showSymbol: false,
    smooth: 0.32,
    smoothMonotone: 'x' as const,
    data: frequencies.map((frequency, index) => [frequency, values[index] ?? null]),
  }));
}

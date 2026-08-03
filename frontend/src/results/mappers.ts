import type { JobResults, NullableNumber, PolarSample } from '../api/results';

export interface NamedResult { id: string; label: string; result: JobResults }

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

export function splSeries(items: NamedResult[]) {
  return items.map(({ label, result }) => {
    const frequencies = frequencyAxis(result, result.spl_on_axis?.frequencies);
    const spl = result.spl_on_axis?.spl ?? [];
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

export function directivityGrid(result: JobResults, plane: 'horizontal' | 'vertical' = 'horizontal'): DirectivityGrid {
  const frequencies = result.frequencies;
  const patterns = result.directivity?.[plane] ?? [];
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

export function impedanceSeries(result: JobResults, mode: 'cartesian' | 'polar') {
  const frequencies = frequencyAxis(result, result.impedance?.frequencies);
  const real = result.impedance?.real ?? [];
  const imaginary = result.impedance?.imaginary ?? [];
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


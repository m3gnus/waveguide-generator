import type { ChartDensity } from './chartAxes';

/**
 * The round frequencies a log axis is read against, per card size.
 *
 * 1-2-5 per decade fits a dock card without ECharts thinning it; thinning by
 * overlap is what used to drop 1k and 10k, the two ticks people read against.
 * The detail view has room for 300 and 3k as well.
 */
const LABEL_MANTISSAS: Record<ChartDensity, readonly number[]> = { compact: [1, 2, 5], regular: [1, 2, 5], full: [1, 2, 3, 5] };

/** Round frequencies at the given mantissas of each decade, within the range. */
function logarithmicFrequencyGuides(frequencyMin: number, frequencyMax: number, mantissas: readonly number[] = [1, 2, 3, 5]): number[] {
  if (!(frequencyMin > 0) || frequencyMax < frequencyMin) return [];
  const guides: number[] = [];
  const firstDecade = Math.floor(Math.log10(frequencyMin));
  const lastDecade = Math.ceil(Math.log10(frequencyMax));
  // A bound computed from a sweep can sit a rounding error inside 20 kHz.
  const lower = frequencyMin * (1 - 1e-9);
  const upper = frequencyMax * (1 + 1e-9);
  for (let decade = firstDecade; decade <= lastDecade; decade += 1) {
    for (const mantissa of mantissas) {
      const frequency = mantissa * (10 ** decade);
      if (frequency >= lower && frequency <= upper) guides.push(frequency);
    }
  }
  return [...new Set(guides)].sort((a, b) => a - b);
}

/** Labelled ticks for a log frequency axis on a card of this size. */
export function logFrequencyTicks(frequencyMin: number, frequencyMax: number, density: ChartDensity): number[] {
  return logarithmicFrequencyGuides(frequencyMin, frequencyMax, LABEL_MANTISSAS[density]);
}

function isLabelledFrequency(frequency: number, density: ChartDensity): boolean {
  const mantissa = frequency / (10 ** Math.floor(Math.log10(frequency) + 1e-9));
  return LABEL_MANTISSAS[density].some((candidate) => Math.abs(candidate - mantissa) < 1e-6);
}

function linearFrequencyTicks(frequencyMin: number, frequencyMax: number, domainMin: number, domainMax: number, step: number): number[] {
  const lower = Math.max(frequencyMin, domainMin);
  const upper = Math.min(frequencyMax, domainMax);
  if (upper < lower) return [];
  const first = Math.ceil(lower / step) * step;
  if (first > upper + step * 1e-9) return [];
  const ticks: number[] = [];
  for (let frequency = first; frequency < upper + step * 0.5; frequency += step) ticks.push(frequency);
  return ticks;
}

/**
 * Return the clean directivity frequency ticks used by WG v1.
 *
 * That plot labeled every 100 Hz from 100 Hz through 1 kHz and every 1 kHz
 * from 1 kHz through 10 kHz, retaining sparse log guides outside that span.
 */
export function preferredDirectivityFrequencyTicks(frequencyMin: number, frequencyMax: number): number[] {
  if (!(frequencyMin > 0) || frequencyMax <= frequencyMin) return [];
  const ticks = [
    ...linearFrequencyTicks(frequencyMin, frequencyMax, 100, 1_000, 100),
    ...linearFrequencyTicks(frequencyMin, frequencyMax, 1_000, 10_000, 1_000),
  ];
  if (frequencyMin < 100) ticks.push(...logarithmicFrequencyGuides(frequencyMin, Math.min(frequencyMax, 100)));
  if (frequencyMax > 10_000) ticks.push(...logarithmicFrequencyGuides(Math.max(frequencyMin, 10_000), frequencyMax));
  return [...new Set(ticks.length ? ticks : logarithmicFrequencyGuides(frequencyMin, frequencyMax))].sort((a, b) => a - b);
}

export function directivityFrequencyTickLabel(value: number): string {
  if (!Number.isFinite(value)) return '';
  if (value >= 1_000) return value % 1_000 === 0 ? `${value / 1_000}k` : `${(value / 1_000).toFixed(1)}k`;
  return String(Math.trunc(value));
}

/** Match each ideal log-axis tick to the nearest dense category cell. */
function directivityFrequencyTicks(frequencies: number[]): Map<number, number> {
  if (frequencies.length < 2) return new Map();
  const ticks = preferredDirectivityFrequencyTicks(frequencies[0], frequencies.at(-1)!);
  const assignments = new Map<number, { tick: number; error: number }>();
  ticks.forEach((tick) => {
    let nearestIndex = 0;
    let nearestError = Infinity;
    frequencies.forEach((frequency, index) => {
      const error = frequency > 0 ? Math.abs(Math.log(frequency / tick)) : Infinity;
      if (error < nearestError) {
        nearestIndex = index;
        nearestError = error;
      }
    });
    const existing = assignments.get(nearestIndex);
    if (!existing || nearestError < existing.error) assignments.set(nearestIndex, { tick, error: nearestError });
  });
  return new Map([...assignments].map(([index, { tick }]) => [index, tick]));
}

/** Every WG v1 graticule tick, by category index, with its label. */
export function directivityFrequencyTickLabels(frequencies: number[]): Map<number, string> {
  return new Map([...directivityFrequencyTicks(frequencies)].map(([index, tick]) => [index, directivityFrequencyTickLabel(tick)]));
}

/**
 * The graticule ticks that carry a label on a card of this size. Every tick
 * keeps its guide line; only the labels are thinned, by round value rather
 * than by ECharts' overlap test.
 */
export function directivityFrequencyLabelIndices(frequencies: number[], density: ChartDensity): Set<number> {
  return new Set([...directivityFrequencyTicks(frequencies)].flatMap(([index, tick]) => isLabelledFrequency(tick, density) ? [index] : []));
}

export const SMOOTHING_MODES = [
  ['none', 'None'],
  ['1/1', '1/1 Oct'],
  ['1/2', '1/2 Oct'],
  ['1/3', '1/3 Oct'],
  ['1/6', '1/6 Oct'],
  ['1/12', '1/12 Oct'],
  ['1/24', '1/24 Oct'],
  ['1/48', '1/48 Oct'],
  ['variable', 'Variable'],
  ['psychoacoustic', 'Psychoacoustic'],
  ['erb', 'ERB'],
] as const;

export type SmoothingMode = typeof SMOOTHING_MODES[number][0];
export type SmoothingValue = number | null;

function finiteValue(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function log2Frequency(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? Math.log2(numeric) : Number.NaN;
}

function fixedFraction(
  frequencies: number[], values: SmoothingValue[], fractionOctave: number,
): SmoothingValue[] {
  if (frequencies.length !== values.length) return values;
  const halfBandwidth = 1 / (2 * fractionOctave);
  const logSigma = halfBandwidth / 2;
  const twoSigmaSquared = 2 * logSigma * logSigma;
  const logFrequencies = frequencies.map(log2Frequency);
  return values.map((original, index) => {
    const center = logFrequencies[index];
    if (!Number.isFinite(center)) return original;
    let weightedSum = 0;
    let weightSum = 0;
    logFrequencies.forEach((frequency, sampleIndex) => {
      if (!Number.isFinite(frequency) || frequency < center - halfBandwidth || frequency > center + halfBandwidth) return;
      const value = finiteValue(values[sampleIndex]);
      if (value === null) return;
      const distance = frequency - center;
      const weight = Math.exp(-(distance * distance) / twoSigmaSquared);
      weightedSum += value * weight;
      weightSum += weight;
    });
    return weightSum > 0 ? weightedSum / weightSum : original;
  });
}

export function fractionalOctaveSmoothing(
  frequencies: number[], values: SmoothingValue[], fractionOctave: number,
): SmoothingValue[] {
  return fixedFraction(frequencies, values, fractionOctave);
}

export function variableSmoothing(frequencies: number[], values: SmoothingValue[]): SmoothingValue[] {
  if (frequencies.length !== values.length) return values;
  const logFrequencies = frequencies.map(log2Frequency);
  const lowReference = Math.log10(100);
  const highReference = Math.log10(10_000);
  return values.map((original, index) => {
    const centerFrequency = frequencies[index];
    const center = logFrequencies[index];
    if (!Number.isFinite(center)) return original;
    let fractionOctave: number;
    if (centerFrequency < 100) fractionOctave = 48;
    else if (centerFrequency > 10_000) fractionOctave = 3;
    else {
      const interpolation = (Math.log10(centerFrequency) - lowReference) / (highReference - lowReference);
      fractionOctave = 48 * Math.pow(3 / 48, interpolation);
    }
    const halfBandwidth = 1 / (2 * fractionOctave);
    const logSigma = halfBandwidth / 2;
    const twoSigmaSquared = 2 * logSigma * logSigma;
    let weightedSum = 0;
    let weightSum = 0;
    logFrequencies.forEach((frequency, sampleIndex) => {
      if (!Number.isFinite(frequency) || frequency < center - halfBandwidth || frequency > center + halfBandwidth) return;
      const value = finiteValue(values[sampleIndex]);
      if (value === null) return;
      const distance = frequency - center;
      const weight = Math.exp(-(distance * distance) / twoSigmaSquared);
      weightedSum += value * weight;
      weightSum += weight;
    });
    return weightSum > 0 ? weightedSum / weightSum : original;
  });
}

export function psychoacousticSmoothing(frequencies: number[], values: SmoothingValue[]): SmoothingValue[] {
  if (frequencies.length !== values.length) return values;
  const logFrequencies = frequencies.map(log2Frequency);
  const lowReference = Math.log10(100);
  const highReference = Math.log10(1_000);
  return values.map((original, index) => {
    const centerFrequency = frequencies[index];
    const center = logFrequencies[index];
    if (!Number.isFinite(center)) return original;
    let fractionOctave: number;
    if (centerFrequency < 100) fractionOctave = 3;
    else if (centerFrequency > 1_000) fractionOctave = 6;
    else fractionOctave = 3 + ((Math.log10(centerFrequency) - lowReference) / (highReference - lowReference)) * 3;
    const halfBandwidth = 1 / (2 * fractionOctave);
    const logSigma = halfBandwidth / 2;
    const twoSigmaSquared = 2 * logSigma * logSigma;
    let weightedSum = 0;
    let weightSum = 0;
    logFrequencies.forEach((frequency, sampleIndex) => {
      if (!Number.isFinite(frequency) || frequency < center - halfBandwidth || frequency > center + halfBandwidth) return;
      const value = finiteValue(values[sampleIndex]);
      if (value === null) return;
      const distance = frequency - center;
      const weight = Math.exp(-(distance * distance) / twoSigmaSquared);
      weightedSum += Math.pow(Math.abs(value), 3) * Math.sign(value) * weight;
      weightSum += weight;
    });
    if (weightSum === 0) return original;
    const averageCubed = weightedSum / weightSum;
    return Math.pow(Math.abs(averageCubed), 1 / 3) * Math.sign(averageCubed);
  });
}

export function erbSmoothing(frequencies: number[], values: SmoothingValue[]): SmoothingValue[] {
  if (frequencies.length !== values.length) return values;
  const numericFrequencies = frequencies.map((value) => Number.isFinite(value) && value > 0 ? value : Number.NaN);
  return values.map((original, index) => {
    const center = numericFrequencies[index];
    if (!Number.isFinite(center)) return original;
    const erbHz = 107.77 * (center / 1_000) + 24.673;
    const halfBandwidth = erbHz / 2;
    const sigma = erbHz / 4;
    const twoSigmaSquared = 2 * sigma * sigma;
    let weightedSum = 0;
    let weightSum = 0;
    numericFrequencies.forEach((frequency, sampleIndex) => {
      if (!Number.isFinite(frequency) || frequency < center - halfBandwidth || frequency > center + halfBandwidth) return;
      const value = finiteValue(values[sampleIndex]);
      if (value === null) return;
      const distance = frequency - center;
      const weight = Math.exp(-(distance * distance) / twoSigmaSquared);
      weightedSum += value * weight;
      weightSum += weight;
    });
    return weightSum > 0 ? weightedSum / weightSum : original;
  });
}

export function applySmoothing(
  frequencies: number[], values: SmoothingValue[], mode: SmoothingMode,
): SmoothingValue[] {
  if (mode === 'none') return values;
  if (mode === 'variable') return variableSmoothing(frequencies, values);
  if (mode === 'psychoacoustic') return psychoacousticSmoothing(frequencies, values);
  if (mode === 'erb') return erbSmoothing(frequencies, values);
  const fraction = Number(mode.slice(2));
  return fractionalOctaveSmoothing(frequencies, values, fraction);
}

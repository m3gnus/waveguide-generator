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

/**
 * What a smoothed series is, which decides what its kernel averages.
 *
 * - `level_db`: a level in dB (SPL, relative polar level, DI, power response).
 *   Averaged as power, 10^(L/10), and converted back: smoothing averages
 *   energy, so a dip does not pull the curve down as hard as a peak lifts it
 *   (REW smooths squared magnitudes the same way). Psychoacoustic mode takes
 *   the cubic mean of that power, REW's "cube root of the average of the cubed
 *   values", which weights peaks further.
 * - `linear`: a quantity averaged as itself, here impedance real and imaginary
 *   parts, which together is complex smoothing of Z. A cubic mean of a signed
 *   component means nothing, so psychoacoustic mode keeps its bandwidth and
 *   averages plainly.
 */
export type SmoothingQuantity = 'level_db' | 'linear';

interface Band {
  /** Half-width of the window and the Gaussian sigma, on the kernel's own axis. */
  halfBandwidth: number;
  sigma: number;
}

type Mean = 'arithmetic' | 'power' | 'cubic_power';

function fractionBand(fractionOctave: number): Band {
  const halfBandwidth = 1 / (2 * fractionOctave);
  return { halfBandwidth, sigma: halfBandwidth / 2 };
}

/**
 * The one Gaussian kernel every mode shares. `axis` places samples (log2 Hz
 * for octave modes, Hz for ERB); `band` gives the window at a centre.
 */
function gaussianSmoothing(
  frequencies: number[], values: SmoothingValue[],
  axis: (frequency: unknown) => number, band: (centerHz: number) => Band, mean: Mean,
): SmoothingValue[] {
  if (frequencies.length !== values.length) return values;
  const positions = frequencies.map(axis);
  const numeric = values.map(finiteValue);
  return values.map((original, index) => {
    const center = positions[index];
    if (!Number.isFinite(center)) return original;
    const { halfBandwidth, sigma } = band(frequencies[index]);
    const twoSigmaSquared = 2 * sigma * sigma;
    // Levels are averaged relative to the centre (or first) level so the
    // power of a large dB value cannot overflow; the offset cancels exactly.
    const reference = numeric[index] ?? numeric.find((value) => value !== null) ?? 0;
    let weightedSum = 0;
    let weightSum = 0;
    positions.forEach((position, sampleIndex) => {
      if (!Number.isFinite(position) || position < center - halfBandwidth || position > center + halfBandwidth) return;
      const value = numeric[sampleIndex];
      if (value === null) return;
      const distance = position - center;
      const weight = Math.exp(-(distance * distance) / twoSigmaSquared);
      const term = mean === 'arithmetic' ? value
        : 10 ** (((mean === 'cubic_power' ? 3 : 1) * (value - reference)) / 10);
      weightedSum += term * weight;
      weightSum += weight;
    });
    if (!(weightSum > 0)) return original;
    const average = weightedSum / weightSum;
    if (mean === 'arithmetic') return average;
    return reference + (10 * Math.log10(average)) / (mean === 'cubic_power' ? 3 : 1);
  });
}

function levelMean(quantity: SmoothingQuantity): Mean {
  return quantity === 'level_db' ? 'power' : 'arithmetic';
}

export function fractionalOctaveSmoothing(
  frequencies: number[], values: SmoothingValue[], fractionOctave: number, quantity: SmoothingQuantity,
): SmoothingValue[] {
  return gaussianSmoothing(frequencies, values, log2Frequency, () => fractionBand(fractionOctave), levelMean(quantity));
}

/** REW's variable smoothing: 1/48 octave below 100 Hz to 1/3 octave above 10 kHz. */
export function variableSmoothing(frequencies: number[], values: SmoothingValue[], quantity: SmoothingQuantity): SmoothingValue[] {
  const lowReference = Math.log10(100);
  const highReference = Math.log10(10_000);
  return gaussianSmoothing(frequencies, values, log2Frequency, (centerFrequency) => {
    if (centerFrequency < 100) return fractionBand(48);
    if (centerFrequency > 10_000) return fractionBand(3);
    const interpolation = (Math.log10(centerFrequency) - lowReference) / (highReference - lowReference);
    return fractionBand(48 * Math.pow(3 / 48, interpolation));
  }, levelMean(quantity));
}

/** REW's psychoacoustic smoothing: 1/3 octave below 100 Hz to 1/6 above 1 kHz, cubic mean of power. */
export function psychoacousticSmoothing(frequencies: number[], values: SmoothingValue[], quantity: SmoothingQuantity): SmoothingValue[] {
  const lowReference = Math.log10(100);
  const highReference = Math.log10(1_000);
  return gaussianSmoothing(frequencies, values, log2Frequency, (centerFrequency) => {
    if (centerFrequency < 100) return fractionBand(3);
    if (centerFrequency > 1_000) return fractionBand(6);
    return fractionBand(3 + ((Math.log10(centerFrequency) - lowReference) / (highReference - lowReference)) * 3);
  }, quantity === 'level_db' ? 'cubic_power' : 'arithmetic');
}

/** Equivalent rectangular bandwidth, (107.77 f/kHz + 24.673) Hz, as REW states it. */
export function erbSmoothing(frequencies: number[], values: SmoothingValue[], quantity: SmoothingQuantity): SmoothingValue[] {
  return gaussianSmoothing(
    frequencies, values,
    (value) => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : Number.NaN),
    (center) => {
      const erbHz = 107.77 * (center / 1_000) + 24.673;
      return { halfBandwidth: erbHz / 2, sigma: erbHz / 4 };
    },
    levelMean(quantity),
  );
}

export function applySmoothing(
  frequencies: number[], values: SmoothingValue[], mode: SmoothingMode, quantity: SmoothingQuantity,
): SmoothingValue[] {
  if (mode === 'none') return values;
  if (mode === 'variable') return variableSmoothing(frequencies, values, quantity);
  if (mode === 'psychoacoustic') return psychoacousticSmoothing(frequencies, values, quantity);
  if (mode === 'erb') return erbSmoothing(frequencies, values, quantity);
  const fraction = Number(mode.slice(2));
  return fractionalOctaveSmoothing(frequencies, values, fraction, quantity);
}

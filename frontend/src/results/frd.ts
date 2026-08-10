import type { Preferences } from '../prefs/preferences';
import { exportBaseName } from '../prefs/preferences';
import { applySmoothing, type SmoothingValue } from './smoothing';
import type { ResultPayload } from './types';

export interface PolarFrdFile {
  filename: string;
  text: string;
}

const COMMENT = '*';

// REW writes its own .frd with a comma delimiter, but its documented *accepted*
// separators are tab, space and semicolon, and VituixCAD's are the same three.
// Comma is not on either accept list and is ambiguous under a comma-decimal
// locale, so tab is the one separator both tools read for certain.
const DELIMITER = '\t';

// Per column, matching what REW emits. Frequency needs the extra digits: a log
// sweep puts neighbouring low-frequency points well inside 0.001 Hz of each
// other, and rounding them to three places would collide two rows into one.
const FREQUENCY_PRECISION = 6;
const LEVEL_PRECISION = 3;
const PHASE_PRECISION = 4;
const VALUE_PRECISION = LEVEL_PRECISION;

type PolarPlane = 'horizontal' | 'vertical';

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function fixed(value: number, precision = VALUE_PRECISION): string {
  const formatted = value.toFixed(precision);
  if (!/[eE]/.test(formatted)) return formatted;

  // Number#toFixed delegates to exponential notation at |value| >= 1e21.
  // FRD readers expect ordinary decimals, so expand that rare case explicitly.
  const [coefficient, exponentText] = value.toExponential(precision).split('e');
  const sign = coefficient.startsWith('-') ? '-' : '';
  const digits = coefficient.replace('-', '').replace('.', '');
  const exponent = Number(exponentText);
  return `${sign}${digits}${'0'.repeat(Math.max(0, exponent - precision))}.${'0'.repeat(precision)}`;
}

function onAxisFrequencies(result: ResultPayload): number[] {
  return result.spl_on_axis?.frequencies?.length
    ? result.spl_on_axis.frequencies
    : result.frequencies;
}

// Kept local so this standalone module does not depend on exporters.ts or mappers.ts.
function patternDb(value: unknown): number | null {
  if (Array.isArray(value)) {
    const real = Number(value[0]);
    const imaginary = Number(value[1]);
    const magnitude = Math.hypot(real, imaginary);
    return Number.isFinite(magnitude) && magnitude > 0
      ? 20 * Math.log10(magnitude)
      : null;
  }
  return finite(value) ? value : null;
}

// REW states the smoothing it applied in its own header. These files are
// smoothed too, so they have to say so -- an unlabelled smoothed curve read as
// raw is a quietly wrong measurement.
function smoothingNote(smoothing: Preferences['smoothing']): string {
  return `Smoothing: ${smoothing === 'none' ? 'none' : smoothing}`;
}

function fileText(header: string[], rows: string[]): string {
  return `${header.map((line) => `${COMMENT} ${line}`).concat(rows).join('\n')}\n`;
}

/**
 * Build a three-column FRD response. Only complete finite triples are emitted:
 * a blank phase field can be interpreted differently by readers, while dropping
 * the incomplete row cannot be mistaken for measured phase.
 */
export function buildOnAxisFrd(
  result: ResultPayload,
  preferences: Pick<Preferences, 'smoothing'>,
): string {
  const frequencies = onAxisFrequencies(result);
  const spl = applySmoothing(
    frequencies,
    result.spl_on_axis?.spl ?? [],
    preferences.smoothing,
  );
  const phase = result.spl_on_axis?.phase_degrees ?? [];
  const rows: string[] = [];

  frequencies.forEach((frequency, index) => {
    const magnitude = spl[index];
    const phaseDegrees = phase[index];
    if (!finite(frequency) || !finite(magnitude) || !finite(phaseDegrees)) return;
    rows.push([
      fixed(frequency, FREQUENCY_PRECISION),
      fixed(magnitude, LEVEL_PRECISION),
      fixed(phaseDegrees, PHASE_PRECISION),
    ].join(DELIMITER));
  });

  return fileText([
    'HornLab on-axis frequency response',
    smoothingNote(preferences.smoothing),
    ['Freq(Hz)', 'SPL(dB)', 'Phase(degrees)'].join(DELIMITER),
  ], rows);
}

function measuredAngles(patterns: NonNullable<ResultPayload['directivity']>[PolarPlane]): number[] {
  const angles = new Set<number>();
  patterns?.forEach((pattern) => pattern.forEach(([angle]) => {
    if (finite(angle)) angles.add(angle);
  }));
  return [...angles].sort((a, b) => a - b);
}

function valuesAtAngle(
  patterns: NonNullable<ResultPayload['directivity']>[PolarPlane],
  angle: number,
  frequencyCount: number,
): SmoothingValue[] {
  return Array.from({ length: frequencyCount }, (_, frequencyIndex) => {
    const sample = patterns?.[frequencyIndex]?.find(([sampleAngle]) => sampleAngle === angle);
    return sample ? patternDb(sample[1]) : null;
  });
}

function vacsCoordinate(plane: PolarPlane, signedAngle: number): { phi: number; theta: number } {
  // A negative angle is the opposite meridian, not a negative one: phi is a
  // rotation about the forward axis and stays in [0,360). Vertical negative is
  // therefore 270, not -90 -- which would also spell a four-character Phi field
  // and break the fixed-width three-digit [mmm] the filename pattern expects.
  const positivePhi = plane === 'horizontal' ? 0 : 90;
  const negativePhi = plane === 'horizontal' ? 180 : 270;
  return {
    phi: signedAngle < 0 ? negativePhi : positivePhi,
    theta: Math.abs(signedAngle),
  };
}

function vacsDegrees(value: number): string {
  const rounded = Math.round(value);
  const sign = rounded < 0 ? '-' : '';
  return `${sign}${String(Math.abs(rounded)).padStart(3, '0')}`;
}

/**
 * VituixCAD's VACS 3D preset parses `NAME Phi[mmm]Theta[ppp].frd`.
 * Keeping the complete convention here makes a future switch to Generic 2D
 * naming (or a corrected spherical mapping) a one-function change.
 */
function polarFilename(baseName: string, plane: PolarPlane, angle: number): string {
  const { phi, theta } = vacsCoordinate(plane, angle);
  return `${baseName} Phi${vacsDegrees(phi)}Theta${vacsDegrees(theta)}.frd`;
}

/** Build one honest, magnitude-only FRD file for every measured plane/angle. */
export function buildPolarFrdSet(
  result: ResultPayload,
  preferences: Pick<Preferences, 'smoothing' | 'outputName' | 'counter'>,
): PolarFrdFile[] {
  if (!result.directivity) return [];

  const frequencies = result.frequencies;
  const baseName = exportBaseName(preferences);
  const files: PolarFrdFile[] = [];

  (['horizontal', 'vertical'] as const).forEach((plane) => {
    const patterns = result.directivity?.[plane];
    measuredAngles(patterns).forEach((angle) => {
      const magnitudes = applySmoothing(
        frequencies,
        valuesAtAngle(patterns, angle, frequencies.length),
        preferences.smoothing,
      );
      const rows: string[] = [];
      frequencies.forEach((frequency, index) => {
        const magnitude = magnitudes[index];
        if (!finite(frequency) || !finite(magnitude)) return;
        rows.push([
          fixed(frequency, FREQUENCY_PRECISION),
          fixed(magnitude, LEVEL_PRECISION),
        ].join(DELIMITER));
      });
      files.push({
        filename: polarFilename(baseName, plane, angle),
        text: fileText([
          'HornLab polar frequency response — magnitude-only; phase is not available and is intentionally omitted',
          `Plane: ${plane}, angle ${angle} deg`,
          smoothingNote(preferences.smoothing),
          ['Freq(Hz)', 'SPL(dB)'].join(DELIMITER),
        ], rows),
      });
    });
  });

  return files;
}

export const REFERENCE_PRESSURE_PA = 2e-5;
export const FIELD_PLANE_WINDOW_DB = 60;
export const FIELD_PLANE_NORMALIZED_MIN_DB = -40;
export const FIELD_PLANE_PHASE_LIMIT_DEGREES = 180;
export const FIELD_PLANE_ISOLINE_STEP_DB = 6;
export const FIELD_PLANE_LUT_SIZE = 256;
export const FIELD_PLANE_INSTANTANEOUS_PERCENTILE = 98;

const FIELD_PLANE_DEFAULT_PRESSURE_LIMIT_PA = 1;

export type FieldPlaneDisplayMode = 'spl' | 'normalized' | 'phase' | 'instantaneous';
export type FieldPlaneWindowUnit = 'dB' | 'deg' | 'Pa';

export interface FieldPlaneValueWindow {
  minimum: number;
  maximum: number;
  unit: FieldPlaneWindowUnit;
}

export interface FieldPlaneDisplayOptions {
  fieldMaxSplDb: number;
  phaseRadians: number;
}

export const FIELD_PLANE_DISPLAY_MODES: ReadonlyArray<{
  value: FieldPlaneDisplayMode;
  label: string;
}> = [
  { value: 'spl', label: 'SPL' },
  { value: 'normalized', label: 'Normalized' },
  { value: 'phase', label: 'Phase' },
  { value: 'instantaneous', label: 'Instantaneous' },
];

export const FIELD_PLANE_MODE_UNIFORM: Readonly<Record<FieldPlaneDisplayMode, number>> = {
  spl: 0,
  normalized: 1,
  phase: 2,
  instantaneous: 3,
};

/** The repeated endpoint makes interpolation across the phase seam cyclic. */
export const FIELD_PLANE_PHASE_COLORMAP: readonly string[] = [
  '#271858',
  '#354a9f',
  '#2389b7',
  '#32b39a',
  '#a9c45b',
  '#e5a044',
  '#d35c67',
  '#80316f',
  '#271858',
];

export const FIELD_PLANE_DIVERGING_COLORMAP: readonly string[] = [
  '#243b8f',
  '#4778bd',
  '#91b5d2',
  '#e7e4dc',
  '#dc987c',
  '#bd514d',
  '#76233b',
];

export type Rgba8 = readonly [number, number, number, number];

/** CPU oracle for the field-plane fragment shader. Keep its constants and
 * clamping rules in parity with fieldPlaneShader.ts. */
export function splDb(real: number, imag: number): number {
  return 20 * Math.log10(Math.hypot(real, imag) / REFERENCE_PRESSURE_PA);
}

export function normalizedSplDb(real: number, imag: number, fieldMaxSplDb: number): number {
  return splDb(real, imag) - fieldMaxSplDb;
}

export function phaseDegrees(real: number, imag: number): number {
  return Math.atan2(imag, real) * 180 / Math.PI;
}

/** Instantaneous pressure for the solver's e^-iwt convention. */
export function instantaneousPressure(real: number, imag: number, phaseRadians: number): number {
  return real * Math.cos(phaseRadians) + imag * Math.sin(phaseRadians);
}

export function fieldPlaneDisplayValue(
  mode: FieldPlaneDisplayMode,
  real: number,
  imag: number,
  options: FieldPlaneDisplayOptions,
): number {
  switch (mode) {
    case 'spl': return splDb(real, imag);
    case 'normalized': return normalizedSplDb(real, imag, options.fieldMaxSplDb);
    case 'phase': return phaseDegrees(real, imag);
    case 'instantaneous': return instantaneousPressure(real, imag, options.phaseRadians);
  }
}

export function windowNormalize(value: number, lo: number, hi: number): number {
  if (!(hi > lo) || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(1, (value - lo) / (hi - lo)));
}

export function lutIndex(normalized: number, size = FIELD_PLANE_LUT_SIZE): number {
  if (!Number.isSafeInteger(size) || size < 1) throw new Error('LUT size must be a positive integer');
  return Math.round(Math.max(0, Math.min(1, normalized)) * (size - 1));
}

export function sampleLut(lut: Uint8Array, normalized: number): { index: number; rgba: Rgba8 } {
  if (lut.length === 0 || lut.length % 4 !== 0) throw new Error('LUT must contain RGBA texels');
  const index = lutIndex(normalized, lut.length / 4);
  const offset = index * 4;
  return { index, rgba: [lut[offset], lut[offset + 1], lut[offset + 2], lut[offset + 3]] };
}

function parseHexColor(value: string): Rgba8 {
  const hex = value.trim().replace(/^#/, '');
  const expanded = hex.length === 3
    ? hex.split('').map((digit) => `${digit}${digit}`).join('')
    : hex;
  if (!/^[0-9a-fA-F]{6}$/.test(expanded)) throw new Error(`Unsupported colormap colour: ${value}`);
  return [
    Number.parseInt(expanded.slice(0, 2), 16),
    Number.parseInt(expanded.slice(2, 4), 16),
    Number.parseInt(expanded.slice(4, 6), 16),
    255,
  ];
}

/** Expand colour stops to the 256 texels sampled by the shader. */
export function buildLutRgba(colormap: readonly string[], size = FIELD_PLANE_LUT_SIZE): Uint8Array {
  if (colormap.length === 0) throw new Error('Colormap must contain at least one colour');
  if (!Number.isSafeInteger(size) || size < 2) throw new Error('LUT size must be an integer of at least 2');
  const stops = colormap.map(parseHexColor);
  const output = new Uint8Array(size * 4);
  for (let index = 0; index < size; index += 1) {
    const stopPosition = index / (size - 1) * (stops.length - 1);
    const lower = Math.floor(stopPosition);
    const upper = Math.min(stops.length - 1, lower + 1);
    const mix = stopPosition - lower;
    for (let channel = 0; channel < 4; channel += 1) {
      output[index * 4 + channel] = Math.round(
        stops[lower][channel] * (1 - mix) + stops[upper][channel] * mix,
      );
    }
  }
  return output;
}

export function fieldPlaneColormap(
  mode: FieldPlaneDisplayMode,
  sequential: readonly string[],
): readonly string[] {
  if (mode === 'phase') return FIELD_PLANE_PHASE_COLORMAP;
  if (mode === 'instantaneous') return FIELD_PLANE_DIVERGING_COLORMAP;
  return sequential;
}

export function maxFieldSplDb(real: Float32Array, imag: Float32Array): number {
  if (real.length !== imag.length) throw new Error('Complex field components must have equal lengths');
  let maximum = -Infinity;
  for (let index = 0; index < real.length; index += 1) {
    const value = splDb(real[index], imag[index]);
    if (Number.isFinite(value) && value > maximum) maximum = value;
  }
  return Number.isFinite(maximum) ? maximum : 0;
}

export function maxFieldMagnitudePa(real: Float32Array, imag: Float32Array): number {
  if (real.length !== imag.length) throw new Error('Complex field components must have equal lengths');
  let maximum = 0;
  for (let index = 0; index < real.length; index += 1) {
    const value = Math.hypot(real[index], imag[index]);
    if (Number.isFinite(value) && value > maximum) maximum = value;
  }
  return maximum;
}

function instantaneousFieldLimitPa(real: Float32Array, imag: Float32Array): number {
  if (real.length !== imag.length) throw new Error('Complex field components must have equal lengths');
  const magnitudes: number[] = [];
  let maximum = 0;
  for (let index = 0; index < real.length; index += 1) {
    const magnitude = Math.hypot(real[index], imag[index]);
    if (!Number.isFinite(magnitude)) continue;
    magnitudes.push(magnitude);
    if (magnitude > maximum) maximum = magnitude;
  }
  if (magnitudes.length === 0 || maximum === 0) return FIELD_PLANE_DEFAULT_PRESSURE_LIMIT_PA;
  magnitudes.sort((left, right) => left - right);
  const percentileIndex = Math.floor(
    FIELD_PLANE_INSTANTANEOUS_PERCENTILE / 100 * (magnitudes.length - 1),
  );
  const percentile = magnitudes[percentileIndex];
  return percentile > 0 ? percentile : maximum;
}

export function fieldPlaneWindowForMode(
  mode: FieldPlaneDisplayMode,
  real: Float32Array,
  imag: Float32Array,
): FieldPlaneValueWindow {
  switch (mode) {
    case 'spl': {
      const maximum = maxFieldSplDb(real, imag);
      return { minimum: maximum - FIELD_PLANE_WINDOW_DB, maximum, unit: 'dB' };
    }
    case 'normalized':
      return { minimum: FIELD_PLANE_NORMALIZED_MIN_DB, maximum: 0, unit: 'dB' };
    case 'phase':
      return {
        minimum: -FIELD_PLANE_PHASE_LIMIT_DEGREES,
        maximum: FIELD_PLANE_PHASE_LIMIT_DEGREES,
        unit: 'deg',
      };
    case 'instantaneous': {
      const maximum = instantaneousFieldLimitPa(real, imag);
      return { minimum: -maximum, maximum, unit: 'Pa' };
    }
  }
}

export function defaultFieldPlaneWindow(mode: FieldPlaneDisplayMode): FieldPlaneValueWindow {
  if (mode === 'normalized') {
    return { minimum: FIELD_PLANE_NORMALIZED_MIN_DB, maximum: 0, unit: 'dB' };
  }
  if (mode === 'phase') {
    return {
      minimum: -FIELD_PLANE_PHASE_LIMIT_DEGREES,
      maximum: FIELD_PLANE_PHASE_LIMIT_DEGREES,
      unit: 'deg',
    };
  }
  if (mode === 'instantaneous') {
    return {
      minimum: -FIELD_PLANE_DEFAULT_PRESSURE_LIMIT_PA,
      maximum: FIELD_PLANE_DEFAULT_PRESSURE_LIMIT_PA,
      unit: 'Pa',
    };
  }
  return { minimum: -FIELD_PLANE_WINDOW_DB, maximum: 0, unit: 'dB' };
}

export function isolineCoordinate(valueDb: number): number {
  return valueDb / FIELD_PLANE_ISOLINE_STEP_DB;
}

export function isolineDistanceDb(valueDb: number): number {
  const coordinate = isolineCoordinate(valueDb);
  return Math.abs(coordinate - Math.round(coordinate)) * FIELD_PLANE_ISOLINE_STEP_DB;
}

export function advanceFieldPlanePhase(currentRadians: number, deltaSeconds: number, cyclesPerSecond: number): number {
  const turn = 2 * Math.PI;
  const next = currentRadians + deltaSeconds * cyclesPerSecond * turn;
  return ((next % turn) + turn) % turn;
}

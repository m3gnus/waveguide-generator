export const REFERENCE_PRESSURE_PA = 2e-5;
export const FIELD_PLANE_WINDOW_DB = 60;
export const FIELD_PLANE_LUT_SIZE = 256;

export type Rgba8 = readonly [number, number, number, number];

/** CPU oracle for the field-plane fragment shader. Keep its constants and
 * clamping rules in parity with fieldPlaneShader.ts. */
export function splDb(real: number, imag: number): number {
  return 20 * Math.log10(Math.hypot(real, imag) / REFERENCE_PRESSURE_PA);
}

export function windowNormalize(spl: number, lo: number, hi: number): number {
  if (!(hi > lo) || Number.isNaN(spl)) return 0;
  return Math.max(0, Math.min(1, (spl - lo) / (hi - lo)));
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

/** Expand theme colour stops to the 256 texels sampled by the shader. */
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

export function maxFieldSplDb(real: Float32Array, imag: Float32Array): number {
  if (real.length !== imag.length) throw new Error('Complex field components must have equal lengths');
  let maximum = -Infinity;
  for (let index = 0; index < real.length; index += 1) {
    const value = splDb(real[index], imag[index]);
    if (Number.isFinite(value) && value > maximum) maximum = value;
  }
  return Number.isFinite(maximum) ? maximum : 0;
}

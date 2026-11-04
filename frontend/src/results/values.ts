/** Convert complex pressure to dB, retaining the existing reference convention. */
export function complexToDb(real: number, imaginary: number, reference = 1): number | null {
  const magnitude = Math.hypot(real, imaginary);
  if (!Number.isFinite(magnitude) || magnitude <= 0 || reference <= 0) return null;
  return 20 * Math.log10(magnitude / reference);
}

/** Stored polar values are finite levels or complex pairs; only pair components coerce. */
export function polarValueDb(value: unknown): number | null {
  if (Array.isArray(value)) return complexToDb(Number(value[0]), Number(value[1]));
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

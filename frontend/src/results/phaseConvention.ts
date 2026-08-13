const POSITIVE_SPATIAL_CONVENTIONS = new Set([
  'exp(+ikr)', 'e(+ikr)', '+ikr', 'positive', 'positive-spatial', 'metal',
  'solver-exp-plus-ikr',
  'hornlab-metal', 'metal-bem', 'hornlab-metal-bem', 'bempp', 'bempp-cl',
  'bemppcl', 'hornlab-bempp-bem', 'bempp-cl-numba', 'bempp-cl-opencl',
]);
const NEGATIVE_SPATIAL_CONVENTIONS = new Set([
  'exp(-ikr)', 'e(-ikr)', '-ikr', 'negative', 'negative-spatial', 'legacy',
  'solver-exp-minus-ikr',
  'auto', 'default',
]);

/** Normalize phase tags once so FRD and ZMA cannot quietly drift apart. */
export function normalizePhaseConvention(value: unknown): string {
  return String(value ?? '').trim().toLowerCase().replaceAll('_', '-').replaceAll(' ', '');
}

/** Spatial sign carried by the solver result contract. */
export function phaseSpatialSign(value: unknown): 1 | -1 | null {
  const normalized = normalizePhaseConvention(value);
  if (POSITIVE_SPATIAL_CONVENTIONS.has(normalized)) return 1;
  if (NEGATIVE_SPATIAL_CONVENTIONS.has(normalized)) return -1;
  return null;
}

/** Wrap degrees to (-180, 180], deliberately mapping -180 to +180. */
export function wrapPhaseDegrees(value: number): number {
  const positive = ((value % 360) + 360) % 360;
  return positive > 180 ? positive - 360 : positive;
}

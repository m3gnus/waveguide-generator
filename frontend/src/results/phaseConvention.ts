import type { ResultPayload } from './types';

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

export interface PropagationReference {
  distanceM: number;
  speedOfSoundMps: number;
  spatialSign: 1 | -1;
}

/**
 * The common time-of-flight carried by every on-axis phase sample.
 *
 * Shared by the FRD exporter and the on-screen phase and group-delay charts.
 * Both have to remove the same bulk delay or they describe the same result
 * differently: at r = 2 m the propagation term alone wraps the phase ~100
 * times by 20 kHz, which aliases under any unwrap and swamps a group delay
 * whose interesting features are tens of microseconds.
 */
export function propagationReference(result: ResultPayload): PropagationReference | null {
  const observation = result.metadata?.observation;
  if (!observation || typeof observation !== 'object') return null;
  const distanceM = Number(observation.effective_distance_m ?? observation.requested_distance_m);
  const speedOfSoundMps = Number(observation.sound_speed_m_per_s);
  const spatialSign = phaseSpatialSign(result.metadata?.phase_time_convention);
  return Number.isFinite(distanceM) && Number.isFinite(speedOfSoundMps)
    && distanceM >= 0 && speedOfSoundMps > 0 && spatialSign !== null
    ? { distanceM, speedOfSoundMps, spatialSign }
    : null;
}

/**
 * Frequency response and phase at an angle other than on-axis.
 *
 * The solve already measures every angle in the sweep; `spl_on_axis` is only
 * the sample nearest zero, picked out of the same pressure field the polar
 * patterns come from. The off-axis responses were therefore always in the
 * payload -- `buildPolarFrdSet` has been exporting one file per angle for as
 * long as it has existed -- and were simply never drawn. Nothing here needs a
 * new solve, a new backend field, or a new request.
 *
 * Absolute level at an angle is recovered as
 *
 *   L(theta, f) = spl_on_axis(f) + D(theta, f) - D(theta_axis, f)
 *
 * where `D` is the stored directivity row and `theta_axis` is that row's own
 * on-axis sample. The two `D` terms carry whatever per-row constant the server
 * (and `withNormalizationAngle` after it) shifted by, so the difference is free
 * of it -- which is what makes this correct against all three backends, whose
 * unshifted `directivity_db` means three different things.
 *
 * Phase needs no correction at all: `directivity_phase` is raw wrapped pressure
 * phase at every measured angle, and is never level-normalized.
 *
 * Only angles the solve actually sampled are offered. Interpolating level
 * between two angles would be defensible; interpolating wrapped phase is not,
 * and a control that silently gave one an interpolated partner would be worse
 * than one that only offers what was measured.
 */
import type { NullableNumber, PolarSample, ResultData } from '../api/results';
import type { ResultPayload } from './types';

export const POLAR_PLANES = ['horizontal', 'vertical', 'diagonal'] as const;
export type MeasurementPlane = typeof POLAR_PLANES[number];

/** How many angles may be overlaid at once before the legend stops being read. */
export const MAX_MEASUREMENT_ANGLES = 6;

type PatternRows = PolarSample[][];
type PhaseRows = Array<Array<[number, NullableNumber]>>;

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function sampleDb(value: PolarSample[1]): number | null {
  if (Array.isArray(value)) {
    const magnitude = Math.hypot(Number(value[0]), Number(value[1]));
    return Number.isFinite(magnitude) && magnitude > 0 ? 20 * Math.log10(magnitude) : null;
  }
  return finite(value) ? value : null;
}

function rowsFor(result: ResultData, plane: MeasurementPlane): PatternRows | undefined {
  return (result.directivity as Record<string, PatternRows | undefined> | undefined)?.[plane];
}

/** Planes this result carries at least one measured row for. */
export function measurementPlanes(result: ResultData): MeasurementPlane[] {
  return POLAR_PLANES.filter((plane) => (rowsFor(result, plane)?.length ?? 0) > 0);
}

/** Every angle the sweep sampled in `plane`, ascending. */
export function measurementAngles(result: ResultData, plane: MeasurementPlane): number[] {
  const angles = new Set<number>();
  rowsFor(result, plane)?.forEach((row) => row.forEach(([angle]) => {
    if (finite(angle)) angles.add(angle);
  }));
  return [...angles].sort((left, right) => left - right);
}

/**
 * The angle `spl_on_axis` actually speaks for.
 *
 * The server picks the finite sample nearest zero and says so in metadata when
 * it is not zero; falling back to the same rule here keeps the two in step for
 * results stored before that metadata existed.
 */
export function onAxisAngle(result: ResultData, plane: MeasurementPlane): number | null {
  const stated = (result.metadata?.spl_on_axis as Record<string, unknown> | undefined)?.sampled_angle_degrees;
  if (finite(stated)) return stated;
  const angles = measurementAngles(result, plane);
  if (!angles.length) return null;
  return angles.reduce((best, angle) => (Math.abs(angle) < Math.abs(best) ? angle : best), angles[0]);
}

/** The sampled angle nearest `angle`, so a stale selection lands somewhere real. */
export function nearestMeasurementAngle(result: ResultData, plane: MeasurementPlane, angle: number): number | null {
  const angles = measurementAngles(result, plane);
  if (!angles.length) return null;
  return angles.reduce((best, candidate) => (
    Math.abs(candidate - angle) < Math.abs(best - angle) ? candidate : best
  ), angles[0]);
}

function levelAt(row: PolarSample[] | undefined, angle: number): number | null {
  const sample = row?.find(([sampleAngle]) => sampleAngle === angle);
  return sample ? sampleDb(sample[1]) : null;
}

function phaseAt(row: Array<[number, NullableNumber]> | undefined, angle: number): number | null {
  const sample = row?.find(([sampleAngle]) => sampleAngle === angle);
  return sample && finite(sample[1]) ? sample[1] : null;
}

/** How an angle is written in a legend entry and a file name. */
export function measurementAngleLabel(angle: number): string {
  return `${Number(angle.toFixed(3))}°`;
}

const cache = new WeakMap<object, Map<string, ResultPayload>>();

/**
 * `result` with `spl_on_axis` replaced by the response at `angle` in `plane`.
 *
 * Returning a whole payload rather than a bare series is what keeps this small:
 * `splSeries`, `phaseSeries`, smoothing, the propagation reference and the PNG
 * exporter all read `spl_on_axis`, and every one of them is correct on an
 * off-axis response without knowing that is what it is holding. The observation
 * distance is the same at every angle on the arc, so the time-of-flight the
 * phase readers remove is the same too.
 *
 * The on-axis angle returns the input unchanged, so the default selection draws
 * byte-identically to the chart that existed before this did.
 */
export function withMeasurementAngle<T extends ResultData>(result: T, plane: MeasurementPlane, angle: number): T {
  const axis = onAxisAngle(result, plane);
  if (axis === null || angle === axis) return result;
  const rows = rowsFor(result, plane);
  if (!rows?.length) return result;
  const key = `${plane}@${angle}`;
  let byAngle = cache.get(result);
  if (!byAngle) {
    byAngle = new Map();
    cache.set(result, byAngle);
  }
  const hit = byAngle.get(key);
  if (hit) return hit as unknown as T;

  const payload = result as unknown as ResultPayload;
  const onAxis = payload.spl_on_axis ?? {};
  const frequencies = onAxis.frequencies?.length ? onAxis.frequencies : result.frequencies;
  const phaseRows = (payload.directivity_phase as Record<string, PhaseRows | undefined> | undefined)?.[plane];
  const spl: NullableNumber[] = [];
  const phase: NullableNumber[] = [];
  frequencies.forEach((_frequency, index) => {
    const reference = onAxis.spl?.[index];
    const here = levelAt(rows[index], angle);
    const axisLevel = levelAt(rows[index], axis);
    spl.push(finite(reference) && here !== null && axisLevel !== null ? reference + here - axisLevel : null);
    phase.push(phaseAt(phaseRows?.[index], angle));
  });

  const shifted = {
    ...payload,
    spl_on_axis: { ...onAxis, frequencies, spl, phase_degrees: phase },
  } as ResultPayload;
  byAngle.set(key, shifted);
  return shifted as unknown as T;
}

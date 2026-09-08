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
 *   L(theta, f) = A(plane, f) + D(theta, f) - D(theta_axis, f)
 *
 * where `D` is the stored directivity row, `theta_axis` is that row's own
 * on-axis sample, and `A` is that plane's absolute level at `theta_axis`. The
 * two `D` terms carry whatever per-row constant the server (and
 * `withNormalizationAngle` after it) shifted by, so the difference is free of it
 * -- which is what makes this correct against all three backends, whose
 * unshifted `directivity_db` means three different things.
 *
 * `A` is the part that is not free. `spl_on_axis` is the *first* plane's
 * absolute level at `theta_axis`. When `theta_axis` is 0 every plane meets
 * there and that one number anchors all of them; when the grid omits zero --
 * 5 to 85 in 10 degree steps -- `theta_axis` is a different observation point
 * in every plane, and each `directivity` row was separately normalized, so the
 * per-plane offsets are not in the patterns at all. The server therefore
 * publishes every plane's own anchor as
 * `metadata.spl_on_axis.plane_reference_spl_db`, and that is what `A` reads.
 * A payload stored before that field existed carries no recoverable anchor for
 * a secondary plane on a zero-less grid, so the absolute projection is declined
 * -- `measurementAnchorRefusal` says why, and the levels come back null --
 * rather than anchored to a plane the reader is not looking at. Phase is
 * unaffected either way and is still drawn.
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

/**
 * The plane `spl_on_axis` speaks for.
 *
 * The server reads it out of plane index 0 of the pressure field and writes the
 * planes into `directivity` in that same order, which JSON preserves as key
 * order. `measurementPlanes` sorts into a fixed display order and so cannot
 * answer this -- a run measured vertical-first has `spl_on_axis` from vertical.
 */
export function referencePlane(result: ResultData): MeasurementPlane | null {
  const patterns = result.directivity as Record<string, PatternRows | undefined> | undefined;
  if (!patterns) return null;
  const known = new Set<string>(POLAR_PLANES);
  const first = Object.keys(patterns).find((name) => known.has(name) && (patterns[name]?.length ?? 0) > 0);
  return (first ?? null) as MeasurementPlane | null;
}

/** The reference sample is 0 degrees, where every plane is the same point. */
function isTrueAxis(angle: number): boolean {
  return Math.abs(angle) <= 1e-9;
}

/** This plane's own absolute level at the reference sample, when the run has one. */
function publishedPlaneReference(result: ResultData, plane: MeasurementPlane): NullableNumber[] | null {
  const stated = (result.metadata?.spl_on_axis as Record<string, unknown> | undefined)?.plane_reference_spl_db;
  const row = (stated as Record<string, unknown> | undefined)?.[plane];
  if (!Array.isArray(row) || !row.length) return null;
  return row.map((value) => (finite(value) ? value : null));
}

/**
 * The absolute level `angle`'s response in `plane` must be measured from, or a
 * refusal saying why the run cannot supply one.
 *
 * The stored `spl_on_axis` block comes first because for the reference plane --
 * and for every plane when the reference sample is a true 0 degrees -- it *is*
 * this plane's anchor, and reusing it is what keeps the default selection an
 * untouched payload rather than an equal-valued copy.
 */
function planeAnchor(result: ResultData, plane: MeasurementPlane, axis: number): {
  spl: NullableNumber[] | undefined;
  refusal: string | null;
} {
  if (isTrueAxis(axis) || plane === referencePlane(result)) {
    return { spl: (result as unknown as ResultPayload).spl_on_axis?.spl, refusal: null };
  }
  const published = publishedPlaneReference(result, plane);
  if (published) return { spl: published, refusal: null };
  return { spl: undefined, refusal: anchorRefusalText(result, plane, axis) };
}

function anchorRefusalText(result: ResultData, plane: MeasurementPlane, axis: number): string {
  const reference = referencePlane(result) ?? 'first';
  return `${plane} level unavailable: this run's reference sample is ${measurementAngleLabel(axis)}`
    + `, a different point in ${plane} than in ${reference}, and it stores no ${plane} level there`;
}

/**
 * Why this run cannot put an absolute scale on `plane`, or `null` when it can.
 *
 * Surfaces that state what a chart is showing read this so a declined
 * projection is explained rather than silently empty. The refusal depends only
 * on the run and the plane, not on which angle is selected: an anchor the run
 * does not carry is missing at every angle, the reference sample included.
 */
export function measurementAnchorRefusal(result: ResultData, plane: MeasurementPlane): string | null {
  const axis = onAxisAngle(result, plane);
  if (axis === null) return null;
  if (!rowsFor(result, plane)?.length) return null;
  return planeAnchor(result, plane, axis).refusal;
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
 * The on-axis angle returns the input unchanged whenever the stored block is
 * already this plane's answer there, so the default selection draws
 * byte-identically to the chart that existed before this did. It is *not*
 * unchanged for a secondary plane on a grid that omits zero: the stored block
 * is another plane's observation point, and the whole point of this module's
 * anchor rules is that such a payload must not be relabelled as this plane's.
 */
export function withMeasurementAngle<T extends ResultData>(result: T, plane: MeasurementPlane, angle: number): T {
  const axis = onAxisAngle(result, plane);
  if (axis === null) return result;
  const rows = rowsFor(result, plane);
  if (!rows?.length) return result;
  const anchor = planeAnchor(result, plane, axis);
  if (angle === axis && anchor.spl === (result as unknown as ResultPayload).spl_on_axis?.spl) return result;
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
    const reference = anchor.spl?.[index];
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

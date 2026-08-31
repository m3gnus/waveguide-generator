/**
 * Display-time re-referencing of the directivity patterns.
 *
 * The server ships `directivity` already shifted so the *submitted* norm angle
 * reads 0 dB (`_renormalize_directivity`). That made the normalization angle a
 * solve setting in practice: changing it moved nothing on screen until the run
 * was solved again, which is minutes to hours for the sake of a display
 * reference. It is also what the field reads as a bug -- the control says it
 * changes only the display, and then nothing changes.
 *
 * The shift is a single constant per plane/frequency row, so re-referencing an
 * already-referenced row to a different angle is exactly the same operation and
 * composes exactly:
 *
 *   (row - D(a)) - ((row - D(a))(b)) = row - D(b)
 *
 * whatever `a` the run happened to be solved with. That is what makes this
 * safe to do on the client, and safe to do to results archived before this
 * existed: nothing here needs to know the run's own norm angle, and no result
 * payload changes shape. The server keeps normalizing as it always has, so the
 * stored contract and every older reader stay valid.
 *
 * The three backends disagree about what the unshifted `directivity_db` means
 * -- bempp aliases absolute `spl_db`, Metal emits dB re 1 Pa, `combine` emits
 * an already-on-axis-referenced pattern -- and this function is indifferent to
 * all three for the same reason.
 */
import type { NullableNumber, PolarSample, ResultData } from '../api/results';
import type { ResultPayload } from './types';

/** The portable default, matching `_portable_defaults` on the server. */
export const DEFAULT_NORMALIZATION_ANGLE = 5;

type PatternRows = PolarSample[][];
type DirectivityBlock = NonNullable<ResultData['directivity']>;

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** dB of one stored sample, which may be a level or a complex pair. */
function sampleDb(value: PolarSample[1]): number | null {
  if (Array.isArray(value)) {
    const magnitude = Math.hypot(Number(value[0]), Number(value[1]));
    return Number.isFinite(magnitude) && magnitude > 0 ? 20 * Math.log10(magnitude) : null;
  }
  return finite(value) ? value : null;
}

/**
 * Level at `angle` in one row, linear in dB between the two samples that
 * bracket it, clamped at both ends.
 *
 * Clamping is what `numpy.interp` does on the server, and it is the behaviour
 * that matters here: the angle on screen is a live UI value and the run being
 * drawn may be an old one whose sweep never reached it. Clamping references
 * such a run to its nearest measured angle instead of dropping the map.
 */
export function levelAtAngle(row: PolarSample[], angle: number): number | null {
  const points: Array<[number, number]> = [];
  row.forEach(([sampleAngle, value]) => {
    const db = sampleDb(value);
    if (finite(sampleAngle) && db !== null) points.push([sampleAngle, db]);
  });
  if (!points.length) return null;
  points.sort((left, right) => left[0] - right[0]);
  if (angle <= points[0][0]) return points[0][1];
  const last = points[points.length - 1];
  if (angle >= last[0]) return last[1];
  const upper = points.findIndex(([sampleAngle]) => sampleAngle >= angle);
  const [leftAngle, leftDb] = points[upper - 1];
  const [rightAngle, rightDb] = points[upper];
  const span = rightAngle - leftAngle;
  if (span <= 0) return leftDb;
  return leftDb + ((rightDb - leftDb) * (angle - leftAngle)) / span;
}

/** One plane's rows, each shifted so `angle` reads 0 dB. */
export function renormalizeRows(rows: PatternRows, angle: number): PatternRows {
  return rows.map((row) => {
    const reference = levelAtAngle(row, angle);
    if (reference === null) return row;
    return row.map(([sampleAngle, value]) => {
      const db = sampleDb(value);
      return [sampleAngle, db === null ? (value as NullableNumber) : db - reference] as PolarSample;
    });
  });
}

/**
 * The angles a result actually carries, across every plane it holds.
 *
 * Used to report what a requested normalization angle was clamped to, which is
 * the one thing a viewer cannot infer from the map itself.
 */
export function sampledAngleRange(result: ResultData): [number, number] | null {
  let minimum = Infinity;
  let maximum = -Infinity;
  Object.values((result.directivity ?? {}) as Record<string, PatternRows | undefined>).forEach((rows) => {
    rows?.forEach((row) => row.forEach(([angle, value]) => {
      if (finite(angle) && sampleDb(value) !== null) {
        minimum = Math.min(minimum, angle);
        maximum = Math.max(maximum, angle);
      }
    }));
  });
  return Number.isFinite(minimum) && Number.isFinite(maximum) ? [minimum, maximum] : null;
}

/**
 * The angle a request for `angle` is actually answered at, and whether the
 * sweep had to be clamped to reach it.
 */
export function resolveNormalizationAngle(result: ResultData, angle: number): { angle: number; clamped: boolean } {
  const requested = finite(angle) ? angle : DEFAULT_NORMALIZATION_ANGLE;
  const range = sampledAngleRange(result);
  if (!range) return { angle: requested, clamped: false };
  const [minimum, maximum] = range;
  if (requested < minimum) return { angle: minimum, clamped: true };
  if (requested > maximum) return { angle: maximum, clamped: true };
  return { angle: requested, clamped: false };
}

/**
 * Cache keyed by the payload identity first and the angle second.
 *
 * A heatmap is rebuilt on every theme token, density and live-snapshot change,
 * and the interpolator behind it already walks the whole grid; re-shifting
 * several hundred rows underneath it on each of those would be a needless cost
 * for a value that changes only when the user types in the field. The WeakMap
 * outer key means a result that leaves the panel takes its entries with it.
 */
const cache = new WeakMap<object, Map<number, ResultPayload>>();

/**
 * `result` with its directivity patterns referenced to `angle`.
 *
 * Returns the input untouched when there is nothing to shift, so callers can
 * apply it unconditionally. Only `directivity` is replaced: `directivity_phase`
 * is raw wrapped phase and is never level-normalized, and `spl_on_axis` is
 * absolute SPL that this must not move either.
 *
 * `metadata.directivity.normalization_angle_degrees` is restated to the angle
 * actually applied. Every reader of that field -- the summary row, the FRD
 * header -- is describing the patterns in the same payload, so leaving the
 * solve-time value behind would make those two disagree.
 */
export function withNormalizationAngle<T extends ResultData>(result: T, angle: number): T {
  const patterns = result.directivity;
  if (!patterns || !finite(angle)) return result;
  const planes = Object.entries(patterns as Record<string, PatternRows | undefined>)
    .filter((entry): entry is [string, PatternRows] => Array.isArray(entry[1]) && entry[1].length > 0);
  if (!planes.length) return result;
  const { angle: resolved } = resolveNormalizationAngle(result, angle);
  let byAngle = cache.get(result);
  if (!byAngle) {
    byAngle = new Map();
    cache.set(result, byAngle);
  }
  const hit = byAngle.get(resolved);
  if (hit) return hit as unknown as T;
  const directivity: DirectivityBlock = { ...patterns };
  planes.forEach(([plane, rows]) => {
    (directivity as Record<string, PatternRows>)[plane] = renormalizeRows(rows, resolved);
  });
  const metadata = result.metadata;
  const directivityMetadata = metadata?.directivity;
  const shifted = {
    ...result,
    directivity,
    ...(directivityMetadata && typeof directivityMetadata === 'object'
      ? {
        metadata: {
          ...metadata,
          directivity: { ...directivityMetadata as Record<string, unknown>, normalization_angle_degrees: resolved },
        },
      }
      : {}),
  } as unknown as ResultPayload;
  byAngle.set(resolved, shifted);
  return shifted as unknown as T;
}

/** Mesher CSV coordinates are centimetres and already include global Scale. */
export function profileSections(text: string): number[][][] {
  return text.split(/\r?\n\s*\r?\n/).map((section) => section.split(/\r?\n/).flatMap((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return [];
    const values = trimmed.split(';').map(Number);
    return values.length === 3 && values.every(Number.isFinite) ? [values] : [];
  })).filter((rows) => rows.length >= 2);
}

function axisSection(sections: number[][][], axis: 'H' | 'V'): number[][] {
  return sections.reduce<{ rows: number[][]; ratio: number; magnitude: number }>((best, rows) => {
    const magnitude = rows.reduce((sum, [x, y]) => sum + Math.abs(axis === 'H' ? x : y), 0) / rows.length;
    const cross = rows.reduce((sum, [x, y]) => sum + Math.abs(axis === 'H' ? y : x), 0) / rows.length;
    const ratio = cross / Math.max(magnitude, Number.EPSILON);
    return ratio < best.ratio || (ratio === best.ratio && magnitude > best.magnitude) ? { rows, ratio, magnitude } : best;
  }, { rows: [], ratio: Infinity, magnitude: -Infinity }).rows;
}

export interface ProfileSample {
  z: number;
  r: number;
}

function meridianSamples(rows: number[][], scale: number): ProfileSample[] {
  return rows.map(([x, y, z]) => ({ z: z * 10 / scale, r: Math.hypot(x, y) * 10 / scale }));
}

export interface ProfileSamples { H: ProfileSample[]; V: ProfileSample[] }

/** Physical mm by default; conversion to authored anchors can explicitly remove scale. */
export function profileSamples(text: string, scale = 1): ProfileSamples {
  const sections = profileSections(text);
  if (sections.length < 2) throw new Error('Profile export did not contain horizontal and vertical meridians.');
  return {
    H: meridianSamples(axisSection(sections, 'H'), scale),
    V: meridianSamples(axisSection(sections, 'V'), scale),
  };
}

/** One plane's generated-profile change between two profile exports. */
export interface GeneratedPlaneChange {
  /** z extents of each export in physical (scaled) mm. */
  extents: { before: [number, number]; after: [number, number] };
  domain: 'full' | 'overlap_only' | 'no_overlap' | 'unavailable';
  /** Largest |Δr| (physical mm) at the exported z rows both profiles share; null when they share none. */
  maxChangeMm: number | null;
  atZMm: number | null;
  sampleCount: number;
  /**
   * Largest |Δr| with each export read as the polyline it wrote, at every
   * exported z of either inside the overlap. A row the edit adds or removes
   * shows up here as a resampling difference even when the curve is unchanged.
   */
  interpolatedMaxChangeMm: number | null;
  interpolatedAtZMm: number | null;
  interpolatedSampleCount: number;
  reason?: string;
}

export interface GeneratedProfileChange { H: GeneratedPlaneChange; V: GeneratedPlaneChange }

/** Row tolerance: the export is written in centimetres with finite digits. */
const GENERATED_Z_TOLERANCE_MM = 1e-6;

function interpolateAt(points: ProfileSample[], z: number): number {
  const high = points.findIndex((point) => point.z >= z);
  if (high <= 0) return points[Math.max(0, high)].r;
  const low = points[high - 1]; const next = points[high];
  return low.r + ((z - low.z) / (next.z - low.z)) * (next.r - low.r);
}

/** The export may list a meridian from mouth to throat; compare along increasing z. */
function alongZ(points: ProfileSample[]): ProfileSample[] {
  return points.length >= 2 && points[0].z > points.at(-1)!.z ? [...points].reverse() : points;
}

function largest(zs: number[], difference: (z: number) => number): { change: number | null; at: number | null } {
  let change: number | null = null; let at: number | null = null;
  for (const z of zs) {
    const value = difference(z);
    if (change === null || value > change) { change = value; at = z; }
  }
  return { change, at };
}

function comparePlane(rawBefore: ProfileSample[], rawAfter: ProfileSample[]): GeneratedPlaneChange {
  const before = alongZ(rawBefore); const after = alongZ(rawAfter);
  const extent = (points: ProfileSample[]): [number, number] => [points[0]?.z ?? NaN, points.at(-1)?.z ?? NaN];
  const extents = { before: extent(before), after: extent(after) };
  const empty = { maxChangeMm: null, atZMm: null, sampleCount: 0, interpolatedMaxChangeMm: null, interpolatedAtZMm: null, interpolatedSampleCount: 0 };
  const increasing = (points: ProfileSample[]) => points.length >= 2 && points.every((point, index) => index === 0 || point.z > points[index - 1].z);
  if (!increasing(before) || !increasing(after)) {
    return { extents, domain: 'unavailable', ...empty,
      reason: 'The generated meridian is not a single-valued r(z) polyline, so a radius change cannot be measured along z.' };
  }
  const start = Math.max(extents.before[0], extents.after[0]);
  const stop = Math.min(extents.before[1], extents.after[1]);
  if (!(start <= stop)) return { extents, domain: 'no_overlap', ...empty };
  const equal = Math.abs(extents.before[0] - extents.after[0]) <= GENERATED_Z_TOLERANCE_MM
    && Math.abs(extents.before[1] - extents.after[1]) <= GENERATED_Z_TOLERANCE_MM;
  const inside = (points: ProfileSample[]) => points.filter(({ z }) => z >= start - GENERATED_Z_TOLERANCE_MM && z <= stop + GENERATED_Z_TOLERANCE_MM);
  // Shared rows: an exported z of `before` that `after` also wrote.
  const pairs = inside(before).flatMap((point) => {
    const match = after.find(({ z }) => Math.abs(z - point.z) <= GENERATED_Z_TOLERANCE_MM);
    return match ? [{ z: point.z, change: Math.abs(point.r - match.r) }] : [];
  });
  const shared = largest(pairs.map(({ z }) => z), (z) => pairs.find((pair) => pair.z === z)!.change);
  const zs = [...new Set([...inside(before), ...inside(after)].map(({ z }) => Math.min(stop, Math.max(start, z))))].sort((a, b) => a - b);
  const interpolated = largest(zs, (z) => Math.abs(interpolateAt(before, z) - interpolateAt(after, z)));
  return {
    extents, domain: equal ? 'full' : 'overlap_only',
    maxChangeMm: shared.change, atZMm: shared.at, sampleCount: pairs.length,
    interpolatedMaxChangeMm: interpolated.change, interpolatedAtZMm: interpolated.at, interpolatedSampleCount: zs.length,
  };
}

/**
 * Generated-profile change between two profile exports of the same design.
 *
 * Both inputs are the exported H/V meridians in physical mm (Scale applied).
 * The primary figure compares radii at the z rows both exports wrote; the
 * secondary reads each export as its polyline at every row of either, which
 * also counts rows the edit added or removed. Both are limited by the
 * export's own axial sampling and measure only these two meridians: not the
 * whole wall, off-axis sections, the mouth outline or the source cap.
 */
export function compareProfileSamples(before: ProfileSamples, after: ProfileSamples): GeneratedProfileChange {
  return { H: comparePlane(before.H, after.H), V: comparePlane(before.V, after.V) };
}

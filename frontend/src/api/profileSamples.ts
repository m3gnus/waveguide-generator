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
  /** Largest |Δr| found, in physical mm, and where; null when nothing comparable. */
  maxChangeMm: number | null;
  atZMm: number | null;
  /** Abscissae compared: every exported sample of both profiles inside the overlap. */
  sampleCount: number;
  reason?: string;
}

export interface GeneratedProfileChange { H: GeneratedPlaneChange; V: GeneratedPlaneChange }

/** Extent tolerance: the export is written in centimetres with finite digits. */
const GENERATED_EXTENT_TOLERANCE_MM = 1e-6;

function interpolateAt(points: ProfileSample[], z: number): number {
  let high = points.findIndex((point) => point.z >= z);
  if (high <= 0) return points[Math.max(0, high)].r;
  const low = points[high - 1]; const next = points[high];
  return low.r + ((z - low.z) / (next.z - low.z)) * (next.r - low.r);
}

/** The export may list a meridian from mouth to throat; compare along increasing z. */
function alongZ(points: ProfileSample[]): ProfileSample[] {
  return points.length >= 2 && points[0].z > points.at(-1)!.z ? [...points].reverse() : points;
}

function comparePlane(rawBefore: ProfileSample[], rawAfter: ProfileSample[]): GeneratedPlaneChange {
  const before = alongZ(rawBefore); const after = alongZ(rawAfter);
  const extent = (points: ProfileSample[]): [number, number] => [points[0]?.z ?? NaN, points.at(-1)?.z ?? NaN];
  const extents = { before: extent(before), after: extent(after) };
  const increasing = (points: ProfileSample[]) => points.length >= 2 && points.every((point, index) => index === 0 || point.z > points[index - 1].z);
  if (!increasing(before) || !increasing(after)) {
    return { extents, domain: 'unavailable', maxChangeMm: null, atZMm: null, sampleCount: 0,
      reason: 'The generated meridian is not a single-valued r(z) polyline, so a radius change cannot be measured along z.' };
  }
  const start = Math.max(extents.before[0], extents.after[0]);
  const stop = Math.min(extents.before[1], extents.after[1]);
  if (!(start <= stop)) return { extents, domain: 'no_overlap', maxChangeMm: null, atZMm: null, sampleCount: 0 };
  const equal = Math.abs(extents.before[0] - extents.after[0]) <= GENERATED_EXTENT_TOLERANCE_MM
    && Math.abs(extents.before[1] - extents.after[1]) <= GENERATED_EXTENT_TOLERANCE_MM;
  const zs = [...new Set([...before, ...after].map((point) => point.z).filter((z) => z >= start && z <= stop))].sort((a, b) => a - b);
  let maxChangeMm = 0; let atZMm = zs[0] ?? start;
  for (const z of zs) {
    const change = Math.abs(interpolateAt(before, z) - interpolateAt(after, z));
    if (change > maxChangeMm) { maxChangeMm = change; atZMm = z; }
  }
  return { extents, domain: equal ? 'full' : 'overlap_only', maxChangeMm, atZMm, sampleCount: zs.length };
}

/**
 * Generated-profile change between two profile exports of the same plane set.
 *
 * Both inputs are the exported H/V meridians in physical mm (Scale applied).
 * Each is treated as the polyline the export wrote; radii are linearly
 * interpolated at every exported z of both inside the common z range, so the
 * result is a sampled estimate limited by the export's own axial sampling. It
 * measures only these two meridians: not the whole wall, off-axis sections,
 * the mouth outline or the source cap.
 */
export function compareProfileSamples(before: ProfileSamples, after: ProfileSamples): GeneratedProfileChange {
  return { H: comparePlane(before.H, after.H), V: comparePlane(before.V, after.V) };
}

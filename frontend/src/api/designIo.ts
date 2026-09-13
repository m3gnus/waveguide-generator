import {
  designForFamily,
  decodeQuadrants,
  serializeDesign,
  type CrossSectionStation,
  type DesignDocument,
  type DesignFamily,
  type ExprNumber,
} from '../stores/design';
import { designWireWithSolveSettings, wgSolveSettingsFromStore } from '../stores/designWire';
import { designFilename } from '../stores/designName';
import type { WgSolveSettings } from '../stores/wgSolveBlock';
import type { PolarConfig } from '../stores/solveOptions';
import type { CadLinkClassification, DesignIdentity } from '../stores/document';
import { writeToOutputFolder, type OutputFolderWrite } from './workspace';
import type { ConfirmReplacements } from './exportDestination';

export interface MigrationApplication {
  name: string;
  note: string;
}

/** A setting the file states that the solve does not read. */
export interface IgnoredSetting {
  key: string;
  value: string;
  note: string;
}

export interface ImportReport {
  dialect: 'mwg' | 'ath';
  migrationsApplied: MigrationApplication[];
  /** Optional: a server that predates the field simply sends nothing. */
  ignoredSettings?: IgnoredSetting[];
  passthrough: {
    keysPreserved: string[];
    blocksPreserved: string[];
    keyCount: number;
    blockCount: number;
  };
}

export interface CadLinkOpenState {
  identity: (DesignIdentity & {
    editVersion: number;
    savedAt: string;
    savedDesignHash: string;
    schema: number;
  }) | null;
  classification: CadLinkClassification;
  adoptionCandidate: (DesignIdentity & { filename: string }) | null;
}

export interface InspectDesignResponse extends ImportReport {
  cadlink: CadLinkOpenState;
}

export interface OpenDesignResponse extends InspectDesignResponse {
  design: Record<string, unknown>;
}

export interface SerializeDesignResponse {
  text: string;
  suggestedFilename: string;
}

export interface WgLinkExportResponse {
  bundlePath: string;
  bundleId: string;
  exportId: string;
  sequence: number;
  designHash: string;
  geometryHash: string;
  artifactSha256: string;
  cadHandoff?: 'published' | 'failed';
  cadHandoffRequestId?: string;
  /** Unstarted updates of the same Fusion link that this one replaced. */
  cadHandoffSuperseded?: string[];
  /** Set when the add-in Fusion runs is too old to take it yet: the remedy. */
  cadHandoffWaiting?: string;
  cadLaunch?: boolean;
  /** The committed identity; absent when the design has moved on since the export. */
  identity?: DesignIdentity;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string | { message?: string } };
    if (typeof body.detail === 'string') return body.detail;
    if (body.detail?.message) return body.detail.message;
  } catch { /* the status text remains useful */ }
  return `Request failed: ${response.status} ${response.statusText}`.trim();
}

async function postText<T>(path: string, text: string, fetcher: typeof fetch): Promise<T> {
  const response = await fetcher(path, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: text,
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<T>;
}

export function openDesignText(text: string, fetcher: typeof fetch = fetch): Promise<OpenDesignResponse> {
  return postText('/api/design/open', text, fetcher);
}

export function inspectDesignText(text: string, fetcher: typeof fetch = fetch): Promise<InspectDesignResponse> {
  return postText('/api/design/import-report', text, fetcher);
}

/**
 * Serialize a downloadable copy under the design's name.
 *
 * The filename is derived here rather than passed in, so a `.cfg` cannot be
 * written under a name the file's own `Report.Title` disagrees with. This path
 * deliberately sends no CadLink identity: downloading a browser copy is not a
 * durable save and must not create or advance registry state.
 */
export async function serializeDesignDocument(
  design: DesignDocument,
  designName: string,
  fetcher: typeof fetch = fetch,
  polarConfig?: PolarConfig,
  solveSettings: WgSolveSettings | null = wgSolveSettingsFromStore(),
): Promise<SerializeDesignResponse> {
  const response = await fetcher('/api/design/serialize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      design: designWireWithSolveSettings(serializeDesign(design), polarConfig, solveSettings, designName),
      filename: designFilename(designName),
    }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<SerializeDesignResponse>;
}

export async function sendDesignToCad(
  design: DesignDocument,
  designRevision: number,
  baseName: string,
  identity: DesignIdentity | null,
  fetcher: typeof fetch = fetch,
  idempotencyKey: string = globalThis.crypto?.randomUUID?.()
    ?? `wglink-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  fusionTarget: {
    documentId: string;
    instanceId: string;
    returnStateHash: string | null;
  } | null = null,
  polarConfig?: unknown,
  solveSettings: WgSolveSettings | null = wgSolveSettingsFromStore(),
): Promise<WgLinkExportResponse> {
  // No save gate here. The server commits the design on screen into the
  // CAD-link registry as part of the export, so an unsaved or edited design
  // sends like any other; `identity` is the optimistic-concurrency token when
  // there is one, not a precondition.
  const pathResponse = await fetcher('/api/cad-workspace/path');
  if (!pathResponse.ok) throw new Error(await errorMessage(pathResponse));
  const workspace = await pathResponse.json() as { selected?: boolean; path?: string };
  if (!workspace.selected) {
    throw new Error('Choose a WGLink folder in Settings → CAD Link before sending to Fusion.');
  }

  const response = await fetcher('/api/export/wglink', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({
      // `baseName` is the slug the bundle's files are named with; the Title
      // written into the config keeps the name's own spelling, which the
      // wire builder reads from the document.
      design: designWireWithSolveSettings(serializeDesign(design), polarConfig, solveSettings),
      designRevision,
      baseName,
      identity,
      expectedFusionDocumentId: fusionTarget?.documentId ?? null,
      expectedFusionInstanceId: fusionTarget?.instanceId ?? null,
      expectedFusionReturnStateHash: fusionTarget?.returnStateHash ?? null,
    }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  const result = await response.json() as WgLinkExportResponse;
  if (result.cadHandoff === 'failed') {
    throw new Error(`The CAD bundle was exported, but WG could not notify Fusion. Open it from ${result.bundlePath}.`);
  }
  return result;
}

function unwrapExpressions(value: unknown, path = '', expressions: Record<string, ExprNumber> = {}, absent: string[] = []): unknown {
  if (Array.isArray(value)) return value.map((item, index) => unwrapExpressions(item, path ? `${path}.${index}` : String(index), expressions, absent));
  if (value === null) {
    if (path) absent.push(path);
    return value;
  }
  if (typeof value !== 'object') return value;
  const record = value as Record<string, unknown>;
  if ('value' in record && Object.keys(record).every((key) => key === 'value' || key === 'raw')) {
    if (typeof record.raw === 'string') {
      expressions[path] = {
        value: typeof record.value === 'number' ? record.value : null,
        raw: record.raw,
      };
    }
    return record.value ?? null;
  }
  return Object.fromEntries(Object.entries(record).map(([key, item]) => [key, unwrapExpressions(item, path ? `${path}.${key}` : key, expressions, absent)]));
}

function merge<T>(base: T, incoming: unknown): T {
  if (incoming === null || typeof incoming !== 'object' || Array.isArray(incoming)) return incoming as T;
  const output = { ...(base as Record<string, unknown>) };
  Object.entries(incoming as Record<string, unknown>).forEach(([key, value]) => {
    const current = output[key];
    if (value === null && current !== null && current !== undefined) return;
    output[key] = value !== null && typeof value === 'object' && !Array.isArray(value)
      ? merge(current && typeof current === 'object' ? current : {}, value)
      : value;
  });
  return output as T;
}

/** Convert schema-wire Expr objects into the store's evaluated document shape. */
export function hydrateDesignDocument(wire: Record<string, unknown>): DesignDocument {
  const expressions: Record<string, ExprNumber> = {};
  const absent: string[] = [];
  const unwrapped = unwrapExpressions(wire, '', expressions, absent) as Record<string, unknown>;
  const formula = unwrapped.formula as DesignFamily;
  if (!['OSSE', 'R-OSSE', 'ICW', 'FREEFORM'].includes(formula)) {
    throw new Error(`Unsupported design formula: ${String(unwrapped.formula)}`);
  }
  const document = merge(designForFamily(formula), unwrapped);
  document.quadrants = decodeQuadrants(document.mesh.quadrants);
  document.mesh.quadrants = Number(document.quadrants.join(''));
  document.enclosure.baffle_margin = Number(document.enclosure.space_l);
  document.source.contours = document.source.contours ?? '';
  if (document.mesh.z_map_points.trim()) document.mesh.sampling_mode = 'zmap';
  if (Object.keys(expressions).length) document._expressions = expressions;
  if (absent.length) document._absent = [...new Set(absent)];
  return document;
}

/** A converted FREEFORM design, and what the conversion could not carry over. */
export interface FreeformConversion {
  design: DesignDocument;
  /** Set when the converted shape differs from the source, for the user to read. */
  notice?: string;
}

/**
 * Use a future server converter when present. Older servers convert from the
 * profile export instead, and failing that fall back to a two-anchor FREEFORM
 * document that preserves the current endpoints.
 */
export async function convertDesignToFreeform(
  design: DesignDocument,
  fetcher: typeof fetch = fetch,
): Promise<FreeformConversion> {
  const response = await fetcher('/api/design/convert?family=FREEFORM', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: serializeDesign(design) }),
  });
  if (response.ok) {
    const body = await response.json() as { design?: Record<string, unknown> } | Record<string, unknown>;
    return { design: hydrateDesignDocument(('design' in body ? body.design : body) as Record<string, unknown>) };
  }
  if (response.status !== 404 && response.status !== 405) throw new Error(await errorMessage(response));

  const profileText = await exportProfileCsv(design, fetcher);
  if (profileText !== null) {
    let converted: FreeformConversion | null = null;
    try { converted = freeformFromProfileCsv(profileText, design); } catch { /* use endpoint fallback */ }
    if (converted) {
      const stations = await morphStations(design, converted.design, profileText, fetcher);
      if (stations) converted.design.cross_sections = stations;
      return converted;
    }
  }

  const fallback = preserveSharedForFreeform(designForFamily('FREEFORM'), design);
  const length = design.L ?? design.depth ?? 120;
  const mouth = design.R ?? 140;
  const throat = design.r0 ?? 12.7;
  fallback.length = length;
  fallback.profile_h!.points = [{ t: 0, r: throat }, { t: 1, r: mouth }];
  fallback.profile_v!.points = structuredClone(fallback.profile_h!.points);
  fallback.profile_h!.throat_angle_deg = design.a0 ?? 15.5;
  fallback.profile_v!.throat_angle_deg = design.a0 ?? 15.5;
  fallback.profile_h!.mouth_angle_deg = design.a ?? 60;
  fallback.profile_v!.mouth_angle_deg = design.a ?? 60;
  return { design: fallback };
}

async function exportProfileCsv(design: DesignDocument, fetcher: typeof fetch): Promise<string | null> {
  const response = await fetcher('/api/export/profiles?kind=profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: serializeDesign(design), designRevision: 0, baseName: 'freeform-conversion' }),
  });
  return response.ok ? response.text() : null;
}

type MorphMouth =
  | { shape: 'rounded_rectangle'; cornerRadius: number }
  | { shape: 'superellipse'; exponent: number };

/** Where a morph's blend starts and reaches half, as FREEFORM `t` (z / length). */
interface MorphBlend {
  start: number;
  half: number;
}

/**
 * The mouth station an active Rectangle or Superellipse morph needs, or null
 * when the H/V meridians already describe its outline.
 *
 * A Circle target needs no station: the exported meridians both reach its
 * radius, and an ellipse through them is that circle. Nor does a Superellipse
 * at exponent 2, which is an ellipse through the target extents.
 */
function morphMouth(design: DesignDocument): MorphMouth | null {
  const morph = design.morph as DesignDocument['morph'] & { target_exponent?: number | null };
  const fixed = morph.fixed_part;
  // Fixed part 1 disables the morph, as does reaching R-OSSE's truncation
  // limit. An expression is left to the measurement below to decide.
  if (Number.isFinite(fixed) && (fixed >= 1 || (design.formula === 'R-OSSE' && fixed >= (design.tmax ?? 1)))) return null;
  if (morph.target_shape === 1) {
    return { shape: 'rounded_rectangle', cornerRadius: Number.isFinite(morph.corner_radius) ? morph.corner_radius : 0 };
  }
  if (morph.target_shape === 3) {
    const exponent = Math.min(16, Math.max(2, Number.isFinite(morph.target_exponent) ? morph.target_exponent! : 2));
    return exponent > 2 ? { shape: 'superellipse', exponent } : null;
  }
  return null;
}

function withoutMorph(design: DesignDocument): DesignDocument {
  const plain = structuredClone(design);
  plain.morph.target_shape = 0;
  if (plain._expressions) delete plain._expressions['morph.target_shape'];
  if (plain._absent) plain._absent = plain._absent.filter((path) => path !== 'morph.target_shape');
  return plain;
}

/**
 * Cross-section stations that carry an active Rectangle or Superellipse morph
 * into FREEFORM, or null when the converted meridians already describe it.
 *
 * The profile export has the morph baked into every meridian, so the H/V
 * profiles already reach the target's half-width and half-height. What they
 * cannot carry is the outline between them. The converted design keeps morph
 * off, so nothing is applied twice; the stations reproduce the outline.
 */
async function morphStations(
  design: DesignDocument,
  converted: DesignDocument,
  morphedCsv: string,
  fetcher: typeof fetch,
): Promise<CrossSectionStation[] | null> {
  const mouth = morphMouth(design);
  if (!mouth) return null;
  // Without a morph-free export to compare against, blend over the whole length.
  let blend: MorphBlend | null = { start: 0, half: .5 };
  try {
    const plainCsv = await exportProfileCsv(withoutMorph(design), fetcher);
    if (plainCsv !== null) blend = measureMorphBlend(morphedCsv, plainCsv, design, converted.length ?? 0);
  } catch { /* keep the whole-length blend */ }
  return blend ? stationsForMorph(mouth, converted, blend) : null;
}

interface Meridian {
  phi: number;
  z: number[];
  r: number[];
}

function profileMeridians(text: string, scale: number): Meridian[] {
  return profileSections(text).map((rows) => {
    const [x, y] = rows.at(-1)!;
    return {
      phi: modTau(Math.atan2(y, x)),
      z: rows.map(([, , z]) => z * 10 / scale),
      r: rows.map(([rx, ry]) => Math.hypot(rx, ry) * 10 / scale),
    };
  }).sort((left, right) => left.phi - right.phi);
}

function modTau(angle: number): number {
  const tau = 2 * Math.PI;
  return ((angle % tau) + tau) % tau;
}

/** Row `row` of the morph-free surface at azimuth `phi`, between its two nearest meridians. */
function radiusAtAngle(sorted: Meridian[], phi: number, row: number): number {
  const upperIndex = sorted.findIndex((meridian) => meridian.phi >= phi);
  const upper = sorted[upperIndex === -1 ? 0 : upperIndex];
  const lower = sorted[((upperIndex === -1 ? 0 : upperIndex) - 1 + sorted.length) % sorted.length];
  // `lower` precedes `phi` and `upper` is at or after it, so u is in (0, 1];
  // a lone meridian interpolates against itself.
  const span = modTau(upper.phi - lower.phi) || 2 * Math.PI;
  const u = modTau(phi - lower.phi) / span;
  return lower.r[row] + (upper.r[row] - lower.r[row]) * u;
}

/**
 * Measure a morph's blend from the same export with and without it.
 *
 * Reading it back from the surface rather than recomputing it from Fixed part
 * and Morph rate keeps the mesher's own rules -- R-OSSE's truncation limit,
 * OSSE's reserved throat-extension slices, snapping to the axial grid, and
 * the sampling mode -- in one place. Morph angles are corner-aware and differ
 * from the plain export's, so the plain surface is interpolated to them.
 * Returns null when the morph moves no part of the mouth.
 */
export function measureMorphBlend(morphedCsv: string, plainCsv: string, design: DesignDocument, length: number): MorphBlend | null {
  const scale = Number.isFinite(design.scale) && design.scale > 0 ? design.scale : 1;
  const morphed = profileMeridians(morphedCsv, scale);
  const plain = profileMeridians(plainCsv, scale);
  const rows = plain[0]?.r.length ?? 0;
  if (!(length > 0) || rows < 2 || plain.some((meridian) => meridian.r.length !== rows)) return { start: 0, half: .5 };
  let best: { meridian: Meridian; base: number[]; departure: number } | null = null;
  for (const meridian of morphed) {
    if (meridian.r.length !== rows) continue;
    const base = meridian.r.map((_radius, row) => radiusAtAngle(plain, meridian.phi, row));
    const departure = meridian.r.at(-1)! - base.at(-1)!;
    if (!best || Math.abs(departure) > Math.abs(best.departure)) best = { meridian, base, departure };
  }
  if (!best || Math.abs(best.departure) < .05) return null;
  const { meridian, base, departure } = best;
  const t = meridian.z.map((z) => Math.min(1, Math.max(0, z / length)));
  const factor = meridian.r.map((radius, row) => (radius - base[row]) / departure);
  let start = 0;
  for (let row = 0; row < rows && Math.abs(factor[row]) <= 1e-3; row += 1) start = t[row];
  let half = 1;
  for (let row = 1; row < rows; row += 1) {
    if (factor[row] >= .5) {
      const ratio = (.5 - factor[row - 1]) / (factor[row] - factor[row - 1] || 1);
      half = t[row - 1] + ratio * (t[row] - t[row - 1]);
      break;
    }
  }
  return { start, half: Math.max(start, half) };
}

function interpolateRadius(points: { t: number; r: number }[], t: number): number {
  const right = points.findIndex((point) => point.t >= t);
  if (right <= 0) return right === 0 ? points[0].r : points.at(-1)!.r;
  const left = points[right - 1];
  const ratio = (t - left.t) / (points[right].t - left.t || 1);
  return left.r + ratio * (points[right].r - left.r);
}

function smootherstep(u: number): number {
  return u * u * u * (u * (u * 6 - 15) + 10);
}

const roundT = (t: number) => Math.round(t * 10_000) / 10_000;
const floorTenth = (value: number) => Math.floor(value * 10) / 10;
const ceilTenth = (value: number) => Math.ceil(value * 10 - 1e-9) / 10;

/**
 * Build the stations for one measured morph.
 *
 * FREEFORM blends neighbouring stations with a fixed smootherstep, so Morph
 * rate cannot be reproduced exactly. The blend-start ellipse is placed so that
 * the smootherstep reaches half where the morph itself does, and never before
 * the morph starts.
 *
 * The corner rules follow the mesher's FREEFORM checks
 * (`hornlab_mesher/freeform.py`). A rounded-rectangle corner must be at least
 * 2% of the smaller local half-size, and its blend weight times the radius may
 * not exceed the local half-size anywhere in its spans; radii stay under 90%
 * of that limit because the limit is estimated here from the anchors rather
 * than the spline. Every outline must also stay convex. An ellipse blends
 * convexly into a rounded rectangle whose corner is at least half the smaller
 * half-size, and a blend between two rounded rectangles stays convex even to
 * the 2% floor, so a smaller corner is reached through an intermediate station
 * at half the local half-size. Checked against the mesher for aspect ratios 1
 * to 5, mouths of 40 to 250 mm, and blend starts from the throat to t = 0.95.
 */
function stationsForMorph(mouth: MorphMouth, converted: DesignDocument, blend: MorphBlend): CrossSectionStation[] {
  const blendStart = roundT(Math.min(Math.max(2 * blend.half - 1, blend.start), .96));
  const start = blendStart >= 1e-3 ? blendStart : 0;
  const stations: CrossSectionStation[] = [{ t: 0, shape: 'ellipse' }];
  if (start > 0) stations.push({ t: start, shape: 'ellipse' });
  if (mouth.shape === 'superellipse') return [...stations, { t: 1, shape: 'superellipse', exponent: mouth.exponent }];

  const halfSize = (t: number) => Math.min(interpolateRadius(converted.profile_h!.points, t), interpolateRadius(converted.profile_v!.points, t));
  /** The largest corner radius a station keeps under its weight-aware limit over one span. */
  const spanLimit = (from: number, to: number, rising: boolean) => {
    let limit = Infinity;
    for (let step = 1; step <= 200; step += 1) {
      const t = from + (to - from) * step / 200;
      const weight = rising ? smootherstep(step / 200) : 1 - smootherstep(step / 200);
      if (weight > 1e-6) limit = Math.min(limit, halfSize(t) / weight);
    }
    return limit;
  };
  const mouthHalfSize = halfSize(1);
  const floor = ceilTenth(Math.max(1, .02 * mouthHalfSize));
  const wanted = Math.min(Math.max(mouth.cornerRadius, floor), mouthHalfSize);
  const direct = floorTenth(Math.min(wanted, .9 * spanLimit(start, 1, true)));
  if (direct >= .5 * mouthHalfSize) return [...stations, { t: 1, shape: 'rounded_rectangle', corner_radius_mm: direct }];

  const middleT = roundT((start + 1) / 2);
  const middle = floorTenth(Math.min(.5 * halfSize(middleT), .9 * spanLimit(start, middleT, true), .9 * spanLimit(middleT, 1, false)));
  const last = Math.max(floor, floorTenth(Math.min(wanted, .9 * spanLimit(middleT, 1, true))));
  return [
    ...stations,
    { t: middleT, shape: 'rounded_rectangle', corner_radius_mm: middle },
    { t: 1, shape: 'rounded_rectangle', corner_radius_mm: last },
  ];
}

function profileSections(text: string): number[][][] {
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

interface ProfileSample {
  z: number;
  r: number;
}

function meridianSamples(rows: number[][], scale: number): ProfileSample[] {
  return rows.map(([x, y, z]) => ({ z: z * 10 / scale, r: Math.hypot(x, y) * 10 / scale }));
}

/**
 * The part of a meridian a FREEFORM profile can hold: its samples up to the
 * first one that steps back toward the throat.
 *
 * A FREEFORM profile's z rises monotonically -- the mesher refuses one that
 * folds -- but an R-OSSE mouth rolls back: at tmax 1 the profile turns past
 * the vertical and curls toward the throat, so its mouth sits well short of
 * its greatest z. Everything after the fold is the rolled-back lip. A sample
 * that repeats the previous z is skipped rather than read as a fold.
 */
function unfoldedSamples(points: ProfileSample[]): { points: ProfileSample[]; folded: boolean } {
  const kept = points.slice(0, 1);
  for (const point of points.slice(1)) {
    const step = point.z - kept.at(-1)!.z;
    if (step < 0) return { points: kept, folded: true };
    if (step > 0) kept.push(point);
  }
  return { points: kept, folded: false };
}

/**
 * The anchors the FREEFORM editor accepts, both ends kept: at most 62 interior
 * points, each at least 1 mm from either end. Samples crowd a fold, so without
 * the margin a rolled-back conversion opens with an interior point the editor
 * already reports as out of range.
 */
function anchorSamples(points: ProfileSample[]): ProfileSample[] {
  const first = points[0];
  const last = points.at(-1)!;
  const interior = points.slice(1, -1).filter((point) => point.z >= first.z + 1 && point.z <= last.z - 1);
  const anchors = [first, ...interior, last];
  if (anchors.length <= 64) return anchors;
  return Array.from({ length: 64 }, (_unused, index) => anchors[Math.round(index * (anchors.length - 1) / 63)]);
}

function tangentAngle(points: ProfileSample[], mouth = false): number {
  const left = mouth ? points.at(-2)! : points[0];
  const right = mouth ? points.at(-1)! : points[1];
  return Math.atan2(right.r - left.r, right.z - left.z) * 180 / Math.PI;
}

/**
 * Convert the existing server profile-export format into editable H/V anchors.
 *
 * A profile that rolls back is converted up to its fold, where it ends on a
 * vertical tangent, and the notice says what was left out: FREEFORM has no
 * way to hold the lip, and cropping the meridians at the source mouth's z
 * instead reads each radius on the way out, far inside the real mouth.
 */
export function freeformFromProfileCsv(text: string, source: DesignDocument): FreeformConversion {
  const sections = profileSections(text);
  if (sections.length < 2) throw new Error('Profile export did not contain horizontal and vertical meridians.');
  const scale = Number.isFinite(source.scale) && source.scale > 0 ? source.scale : 1;
  const horizontal = meridianSamples(axisSection(sections, 'H'), scale);
  const vertical = meridianSamples(axisSection(sections, 'V'), scale);
  const unfoldedH = unfoldedSamples(horizontal);
  const unfoldedV = unfoldedSamples(vertical);
  if (unfoldedH.points.length < 2 || unfoldedV.points.length < 2) throw new Error('Profile export did not contain usable meridians.');
  const length = Math.min(unfoldedH.points.at(-1)!.z, unfoldedV.points.at(-1)!.z);
  const crop = (points: ProfileSample[]) => {
    const inside = points.filter((point) => point.z < length);
    const right = points.find((point) => point.z >= length) ?? points.at(-1)!;
    const left = inside.at(-1) ?? points[0];
    const ratio = right.z === left.z ? 0 : (length - left.z) / (right.z - left.z);
    return inside.concat({ z: length, r: left.r + ratio * (right.r - left.r) });
  };
  const H = anchorSamples(crop(unfoldedH.points)); const V = anchorSamples(crop(unfoldedV.points));
  // A meridian that ends at its own fold is vertical there. One cropped to the
  // other plane's shorter length is still flaring, so keep its chord angle.
  const mouthAngle = (unfolded: typeof unfoldedH, points: ProfileSample[]) => (
    unfolded.folded && unfolded.points.at(-1)!.z === length ? 90 : tangentAngle(points, true)
  );
  const converted = preserveSharedForFreeform(designForFamily('FREEFORM'), source);
  converted.length = length;
  converted.profile_h!.points = H.map(({ z, ...point }) => ({ t: z / length, ...point }));
  converted.profile_v!.points = V.map(({ z, ...point }) => ({ t: z / length, ...point }));
  converted.profile_h!.throat_angle_deg = tangentAngle(H);
  converted.profile_v!.throat_angle_deg = tangentAngle(V);
  converted.profile_h!.mouth_angle_deg = mouthAngle(unfoldedH, H);
  converted.profile_v!.mouth_angle_deg = mouthAngle(unfoldedV, V);
  if (!unfoldedH.folded && !unfoldedV.folded) return { design: converted };
  const mm = (value: number) => `${value.toFixed(1)} mm`;
  return {
    design: converted,
    notice: `The ${source.formula} profile rolls back: it reaches z = ${mm(length)}, then curls back toward the throat. `
      + 'A FREEFORM profile cannot fold back, so the converted profile ends there and leaves out the rolled-back lip. '
      + `Mouth radius: horizontal ${mm(H.at(-1)!.r)} (source ${mm(horizontal.at(-1)!.r)}), `
      + `vertical ${mm(V.at(-1)!.r)} (source ${mm(vertical.at(-1)!.r)}).`,
  };
}

function preserveSharedForFreeform(converted: DesignDocument, source: DesignDocument): DesignDocument {
  converted.scale = source.scale;
  converted.throat_ext_angle = source.throat_ext_angle;
  converted.throat_ext_length = source.throat_ext_length;
  converted.slot_length = source.slot_length;
  converted.length_mode = source.length_mode;
  converted.coverage_mode = source.coverage_mode;
  converted.morph = { ...structuredClone(source.morph), target_shape: 0 };
  converted.source = structuredClone(source.source);
  converted.quadrants = structuredClone(source.quadrants);
  converted.enclosure = structuredClone(source.enclosure);
  converted.mesh = structuredClone(source.mesh);
  converted.simulation = structuredClone(source.simulation);
  converted.output = structuredClone(source.output);
  converted.extra_keys = structuredClone(source.extra_keys);
  converted.extra_blocks = structuredClone(source.extra_blocks);
  const commonExpression = /^(?:scale|throat_ext_angle|throat_ext_length|slot_length|morph\.|source\.|enclosure\.|mesh\.|simulation\.|output\.)/;
  const expressions = Object.fromEntries(Object.entries(source._expressions ?? {}).filter(([path]) => commonExpression.test(path) && path !== 'morph.target_shape'));
  if (Object.keys(expressions).length) converted._expressions = expressions;
  const absent = (source._absent ?? []).filter((path) => commonExpression.test(path) && path !== 'morph.target_shape');
  if (absent.length) converted._absent = absent;
  return converted;
}

function responseFilename(response: Response, fallback: string): string {
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] ?? fallback;
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function downloadText(text: string, filename: string, type = 'text/plain;charset=utf-8'): void {
  downloadBlob(new Blob([text], { type }), filename);
}

export type StepBody = 'solid' | 'surface';

export interface GeometryExportWrite extends OutputFolderWrite {
  warning?: string;
}

/** One built geometry file, before anything has been written. */
export interface GeometryExportFile {
  filename: string;
  blob: Blob;
  warning?: string;
}

/**
 * Build one geometry export, without writing it.
 *
 * Separate from the write so that an export made of more than one file -- the
 * profile pair -- can be built in full and then written once. Two writes mean
 * two replacement questions for one user action, and the dialog answers only
 * one at a time.
 */
export async function buildGeometryExport(
  kind: 'step' | 'stl' | 'profiles',
  design: DesignDocument,
  designRevision: number,
  baseName: string,
  profileKind?: 'profiles' | 'slices',
  stepBody: StepBody = 'solid',
  fetcher: typeof fetch = fetch,
): Promise<GeometryExportFile> {
  const query = kind === 'profiles'
    ? `?kind=${profileKind ?? 'profiles'}`
    : kind === 'step' ? `?body=${stepBody}` : '';
  const response = await fetcher(`/api/export/${kind}${query}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      design: serializeDesign(design),
      designRevision,
      baseName,
    }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  const warning = response.headers.get('X-Export-Warning')?.trim() || undefined;
  const suffix = kind === 'profiles' ? `_${profileKind ?? 'profiles'}.csv` : `.${kind}`;
  const filename = responseFilename(response, `${baseName}${suffix}`);
  const built: GeometryExportFile = { filename, blob: await response.blob() };
  return warning ? { ...built, warning } : built;
}

/**
 * Build a geometry export and write it into the output folder.
 *
 * This used to hand the blob to `downloadBlob`. An `<a download>` reaches the
 * user in a browser tab and goes nowhere in the desktop window, which is a
 * WebView2 host with no download handler -- the export succeeded and the file
 * silently never arrived. Writing server-side is what the run-result exports
 * already do, and it lets the caller tell the user which folder to look in.
 */
export async function exportGeometryToOutputFolder(
  kind: 'step' | 'stl' | 'profiles',
  design: DesignDocument,
  designRevision: number,
  baseName: string,
  profileKind?: 'profiles' | 'slices',
  stepBody: StepBody = 'solid',
  fetcher: typeof fetch = fetch,
  destination?: string,
  confirmReplacements?: ConfirmReplacements,
): Promise<GeometryExportWrite> {
  const { filename, blob, warning } = await buildGeometryExport(
    kind, design, designRevision, baseName, profileKind, stepBody, fetcher,
  );
  // Overwrite: re-exporting after an edit is the ordinary case, and a stale
  // file of the same name is exactly what the user is replacing.
  const written = await writeToOutputFolder(
    // `confirm` only when there is a folder to collide in: a workspace export
    // keeps replacing its own last output without asking.
    baseName, [{ filename, blob }], fetcher,
    destination ? 'confirm' : 'overwrite', destination, confirmReplacements,
  );
  return warning ? { ...written, warning } : written;
}

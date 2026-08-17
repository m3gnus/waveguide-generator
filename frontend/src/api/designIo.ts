import {
  designForFamily,
  decodeQuadrants,
  serializeDesign,
  type DesignDocument,
  type DesignFamily,
  type ExprNumber,
} from '../stores/design';
import { designWireWithSolveSettings, wgSolveSettingsFromStore } from '../stores/designWire';
import type { WgSolveSettings } from '../stores/wgSolveBlock';
import type { PolarConfig } from '../stores/solveOptions';
import type { CadLinkClassification, DesignIdentity } from '../stores/document';

export interface MigrationApplication {
  name: string;
  note: string;
}

export interface ImportReport {
  dialect: 'mwg' | 'ath';
  migrationsApplied: MigrationApplication[];
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

export interface SaveDesignResponse {
  text: string;
  suggestedFilename: string;
  identity: DesignIdentity;
  forked: boolean;
  from: { designId: string; editVersion: number; exportId: string | null } | null;
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

export async function saveDesignDocument(
  design: DesignDocument,
  filename: string,
  identity: DesignIdentity | null = null,
  fetcher: typeof fetch = fetch,
  polarConfig?: PolarConfig,
  solveSettings: WgSolveSettings | null = wgSolveSettingsFromStore(),
): Promise<SaveDesignResponse> {
  const response = await fetcher('/api/design/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      design: designWireWithSolveSettings(serializeDesign(design), polarConfig, solveSettings),
      filename,
      identity,
    }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<SaveDesignResponse>;
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
      design: designWireWithSolveSettings(serializeDesign(design), polarConfig, solveSettings),
      designRevision,
      baseName,
      identity,
      expectedFusionDocumentId: fusionTarget?.documentId ?? null,
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

/**
 * Use a future server converter when present; older servers fall back to a
 * two-anchor FREEFORM document that preserves the current endpoints.
 */
export async function convertDesignToFreeform(
  design: DesignDocument,
  fetcher: typeof fetch = fetch,
): Promise<DesignDocument> {
  const response = await fetcher('/api/design/convert?family=FREEFORM', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: serializeDesign(design) }),
  });
  if (response.ok) {
    const body = await response.json() as { design?: Record<string, unknown> } | Record<string, unknown>;
    return hydrateDesignDocument(('design' in body ? body.design : body) as Record<string, unknown>);
  }
  if (response.status !== 404 && response.status !== 405) throw new Error(await errorMessage(response));

  const profileResponse = await fetcher('/api/export/profiles?kind=profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: serializeDesign(design), designRevision: 0, baseName: 'freeform-conversion' }),
  });
  if (profileResponse.ok) {
    try { return freeformFromProfileCsv(await profileResponse.text(), design); } catch { /* use endpoint fallback */ }
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
  return fallback;
}

function axisSection(sections: number[][][], axis: 'H' | 'V'): number[][] {
  return sections.reduce<{ rows: number[][]; ratio: number; magnitude: number }>((best, rows) => {
    const magnitude = rows.reduce((sum, [x, y]) => sum + Math.abs(axis === 'H' ? x : y), 0) / rows.length;
    const cross = rows.reduce((sum, [x, y]) => sum + Math.abs(axis === 'H' ? y : x), 0) / rows.length;
    const ratio = cross / Math.max(magnitude, Number.EPSILON);
    return ratio < best.ratio || (ratio === best.ratio && magnitude > best.magnitude) ? { rows, ratio, magnitude } : best;
  }, { rows: [], ratio: Infinity, magnitude: -Infinity }).rows;
}

function sampleProfile(rows: number[][], scale: number): { z: number; r: number }[] {
  const points = rows.map(([x, y, z]) => ({ z: z * 10 / scale, r: Math.hypot(x, y) * 10 / scale }));
  if (points.length <= 64) return points;
  return Array.from({ length: 64 }, (_unused, index) => points[Math.round(index * (points.length - 1) / 63)]);
}

function tangentAngle(points: { z: number; r: number }[], mouth = false): number {
  const left = mouth ? points.at(-2)! : points[0];
  const right = mouth ? points.at(-1)! : points[1];
  return Math.atan2(right.r - left.r, right.z - left.z) * 180 / Math.PI;
}

/** Convert the existing server profile-export format into editable H/V anchors. */
export function freeformFromProfileCsv(text: string, source: DesignDocument): DesignDocument {
  const sections = text.split(/\r?\n\s*\r?\n/).map((section) => section.split(/\r?\n/).flatMap((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return [];
    const values = trimmed.split(';').map(Number);
    return values.length === 3 && values.every(Number.isFinite) ? [values] : [];
  })).filter((rows) => rows.length >= 2);
  if (sections.length < 2) throw new Error('Profile export did not contain horizontal and vertical meridians.');
  const scale = Number.isFinite(source.scale) && source.scale > 0 ? source.scale : 1;
  const horizontal = sampleProfile(axisSection(sections, 'H'), scale);
  const vertical = sampleProfile(axisSection(sections, 'V'), scale);
  if (horizontal.length < 2 || vertical.length < 2) throw new Error('Profile export did not contain usable meridians.');
  const length = Math.min(horizontal.at(-1)!.z, vertical.at(-1)!.z);
  const crop = (points: { z: number; r: number }[]) => {
    const inside = points.filter((point) => point.z < length);
    const right = points.find((point) => point.z >= length) ?? points.at(-1)!;
    const left = inside.at(-1) ?? points[0];
    const ratio = right.z === left.z ? 0 : (length - left.z) / (right.z - left.z);
    return inside.concat({ z: length, r: left.r + ratio * (right.r - left.r) });
  };
  const H = crop(horizontal); const V = crop(vertical);
  const converted = preserveSharedForFreeform(designForFamily('FREEFORM'), source);
  converted.length = length;
  converted.profile_h!.points = H.map(({ z, ...point }) => ({ t: z / length, ...point }));
  converted.profile_v!.points = V.map(({ z, ...point }) => ({ t: z / length, ...point }));
  converted.profile_h!.throat_angle_deg = tangentAngle(H);
  converted.profile_v!.throat_angle_deg = tangentAngle(V);
  converted.profile_h!.mouth_angle_deg = tangentAngle(H, true);
  converted.profile_v!.mouth_angle_deg = tangentAngle(V, true);
  return converted;
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

export async function downloadGeometryExport(
  kind: 'step' | 'stl' | 'profiles',
  design: DesignDocument,
  designRevision: number,
  baseName: string,
  profileKind?: 'profiles' | 'slices',
  stepBody: StepBody = 'solid',
  fetcher: typeof fetch = fetch,
): Promise<void> {
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
  const suffix = kind === 'profiles' ? `_${profileKind ?? 'profiles'}.csv` : `.${kind}`;
  downloadBlob(await response.blob(), responseFilename(response, `${baseName}${suffix}`));
}

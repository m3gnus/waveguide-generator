import { stagedActivity } from '../shell/stagedActivity';
import { activityOperation } from '../shell/activityOperation';
import { usesCurrentAthImport, isAthImportVersion } from '../design/athImportVersion';
import { httpErrorMessage, refusalText, isAbortError } from './httpError';
import { profileSamples } from './profileSamples';
import { deadlineFetch } from './deadlineFetch';
import {
  NATIVE_FAMILIES,
  designForFamily,
  decodeQuadrants,
  normalizeNativeCrossSection,
  refreshAbsentWall,
  serializeDesign,
  type DesignDocument,
  type DesignFamily,
  type ExprNumber,
} from '../stores/design';
import { designWireWithSolveSettings, wgSolveSettingsFromStore } from '../stores/designWire';
import { designFilename } from '../stores/designName';
import type { WgSolveSettings } from '../stores/wgSolveBlock';
import type { PolarConfig } from '../stores/solveOptions';
import { useDocumentStore, type CadLinkClassification, type DesignIdentity } from '../stores/document';
import { writeToOutputFolder, type OutputFolderWrite } from './workspace';
import type { ConfirmReplacements } from './exportDestination';
import { solveAttention } from '../shell/solveAttention';

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
  designUid?: string | null;
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
    const body = await response.json() as { detail?: unknown };
    return refusalText(body.detail, 'design request');
  } catch (error) {
    if (isAbortError(error)) throw error;
    // A body that is not JSON has no words for the user.
  }
  return refusalText(null, 'design request');
}

async function postText<T>(path: string, text: string, fetcher: typeof fetch, label: string): Promise<T> {
  return activityOperation({ kind: 'design-open', label }, fetcher, async fetcher => {
    const response = await fetcher(path, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      body: text,
    });
    if (!response.ok) throw new Error(await errorMessage(response));
    return response.json() as Promise<T>;
  });
}

export interface ChosenDesignFile {
  selected: boolean;
  outcome?: 'chosen' | 'cancelled' | 'unavailable';
  fileKey?: string;
  filename?: string;
  source?: string;
  text?: string;
}

/** Null asks the caller to use the browser input; only explicit cancel stops Open. */
export async function chooseDesignFile(fetcher: typeof fetch = fetch): Promise<ChosenDesignFile | null> {
  return activityOperation({ kind: 'design-open', label: 'Opening design file' }, fetcher, async fetcher => {
    try {
      const response = await fetcher('/api/design/choose', { method: 'POST' });
      if (!response.ok) return null;
      const chosen = await response.json() as ChosenDesignFile;
      if (chosen.outcome === 'cancelled') return chosen;
      if (chosen.selected && chosen.filename && typeof chosen.text === 'string') return chosen;
    } catch { /* unavailable picker, network, or malformed reply: retain browser Open */ }
    return null;
  });
}

export function openDesignText(text: string, fetcher: typeof fetch = fetch): Promise<OpenDesignResponse> {
  return postText('/api/design/open', text, fetcher, 'Opening design file');
}

export function inspectDesignText(text: string, fetcher: typeof fetch = fetch): Promise<InspectDesignResponse> {
  return postText('/api/design/import-report', text, fetcher, 'Inspecting design file');
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
  designUid = useDocumentStore.getState().designUid,
): Promise<SerializeDesignResponse> {
  return activityOperation({ kind: 'export', label: 'Exporting design file' }, fetcher, async (fetcher) => {
    const response = await fetcher('/api/design/serialize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        design: designWireWithSolveSettings(serializeDesign(design), polarConfig, solveSettings, designName),
        filename: designFilename(designName, design.formula),
        designUid,
      }),
    });
    if (!response.ok) throw new Error(await errorMessage(response));
    return response.json() as Promise<SerializeDesignResponse>;
  });
}

/** Ask the geometry owner for constants/defaults; angular expressions return null. */
export async function resolveDesignExpressions(
  design: DesignDocument,
  paths: string[],
  fetcher: typeof fetch = fetch,
): Promise<Record<string, number | null>> {
  const response = await fetcher('/api/design/resolve-expressions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: serializeDesign(design), paths }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<Record<string, number | null>>;
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
  designUid?: string,
  assertCurrent?: () => void,
): Promise<WgLinkExportResponse> {
  return stagedActivity({ kind: 'send-to-cad', label: 'Sending to CAD' }, fetcher, undefined, async (fetcher, signal) => {
    const bindAttention = solveAttention.captureOperationArm();
    // No save gate here. The server commits the design on screen into the
    // CAD-link registry as part of the export, so an unsaved or edited design
    // sends like any other; `identity` is the optimistic-concurrency token when
    // there is one, not a precondition.
    const pathResponse = await deadlineFetch(fetcher, '/api/cad-workspace/path');
    assertCurrent?.();
    if (!pathResponse.ok) throw new Error(await errorMessage(pathResponse));
    const workspace = await pathResponse.json() as { selected?: boolean; path?: string };
    assertCurrent?.();
    if (!workspace.selected) {
      throw new Error('No WGLink folder is selected. Choose one in Settings → CAD Link, then send again.');
    }

    const response = await fetcher('/api/export/wglink', {
      method: 'POST', signal,
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
        designUid,
        expectedFusionDocumentId: fusionTarget?.documentId ?? null,
        expectedFusionInstanceId: fusionTarget?.instanceId ?? null,
        expectedFusionReturnStateHash: fusionTarget?.returnStateHash ?? null,
      }),
    });
    if (!response.ok) throw new Error(await errorMessage(response));
    const result = await response.json() as WgLinkExportResponse;
    if (result.cadHandoffRequestId) bindAttention(result.cadHandoffRequestId);
    if (result.cadHandoff === 'failed') {
      throw new Error(`WG saved the model in ${result.bundlePath}, but could not notify Fusion. Open waveguide.step from that folder in Fusion.`);
    }
    return result;
  });
}

function unwrapExpressions(value: unknown, path = '', expressions: Record<string, ExprNumber> = {}, absent: string[] = []): unknown {
  if (path === 'icw_seed' || path === 'icw_coeffs') return structuredClone(value);
  if (Array.isArray(value)) return value.map((item, index) => unwrapExpressions(item, path ? `${path}.${index}` : String(index), expressions, absent));
  if (value === null) {
    if (path) absent.push(path);
    return value;
  }
  if (typeof value !== 'object') return value;
  const record = value as Record<string, unknown>;
  if ('value' in record && Object.keys(record).every((key) => key === 'value' || key === 'raw' || key === 'dialect')) {
    if (typeof record.raw === 'string') {
      expressions[path] = {
        value: typeof record.value === 'number' ? record.value : null,
        raw: record.raw,
        ...(record.dialect === 'wg-v1' || record.dialect === 'ath' ? { dialect: record.dialect } : {}),
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
  if (wire.text_import_version != null && !isAthImportVersion(wire.text_import_version)) {
    throw new Error('This design was saved by a newer version of WG. Update WG to open it.');
  }
  const expressions: Record<string, ExprNumber> = {};
  const absent: string[] = [];
  const unwrapped = unwrapExpressions(wire, '', expressions, absent) as Record<string, unknown>;
  const formula = unwrapped.formula as DesignFamily;
  if (!['OSSE', 'R-OSSE', 'ICW', 'FREEFORM', ...NATIVE_FAMILIES].includes(formula)) {
    throw new Error(`This file uses a horn family WG does not know (${String(unwrapped.formula)}). Update WG, or open another file.`);
  }
  const document = merge(designForFamily(formula), unwrapped);
  // The accuracy default belongs to newly created designs only.
  if ((wire.mesh as Record<string, unknown> | undefined)?.surface_deviation_mm == null) {
    delete document.mesh.surface_deviation_mm;
  }
  if (formula === 'OSSE-NATIVE') {
    // The composite template has active slots; an omitted wire slot is inactive.
    if (wire.axial_scale == null) delete document.axial_scale;
    if (wire.throat_adapter == null) delete document.throat_adapter;
    if (wire.terminating_arc == null) delete document.terminating_arc;
    if ((wire.mesh as Record<string, unknown> | undefined)?.mouth_roundover_radius_mm == null) {
      delete document.mesh.mouth_roundover_radius_mm;
      if ((wire.mesh as Record<string, unknown> | undefined)?.wall_thickness == null) document.mesh.wall_thickness = 0;
    }
  }
  if (usesCurrentAthImport(document.text_import_version) && (wire.mesh as Record<string, unknown> | undefined)?.wall_thickness == null) absent.push('mesh.wall_thickness');
  normalizeNativeCrossSection(document);
  // R is optional on ICW: an absent rollback R enables r_aperture fallback.
  // Do not substitute the new-design default for a file that deliberately omits it.
  if (formula === 'ICW' && wire.R == null) delete document.R;
  if (formula === 'OSSE' || formula === 'R-OSSE') {
    for (const key of ['s1', 's2']) if (!(key in wire)) absent.push(key);
  }
  document.quadrants = decodeQuadrants(document.mesh.quadrants);
  document.mesh.quadrants = Number(document.quadrants.join(''));
  document.enclosure.baffle_margin = Number(document.enclosure.space_l);
  document.source.contours = document.source.contours ?? '';
  if (document.mesh.z_map_points.trim()) document.mesh.sampling_mode = 'zmap';
  if (Object.keys(expressions).length) document._expressions = expressions;
  if (absent.length) document._absent = [...new Set(absent)];
  return refreshAbsentWall(document);
}

/** Legacy discriminators are accepted only at the server import boundary. */
export function needsDesignImport(design: { formula?: unknown }): boolean {
  return design.formula === 'LOOKUP';
}

export async function importStoredDesign(design: DesignDocument, fetcher: typeof fetch = fetch): Promise<OpenDesignResponse> {
  return openDesignText(JSON.stringify(serializeDesign(design)), fetcher);
}

export function importNotice(report: Pick<ImportReport, 'migrationsApplied'>): string | null {
  return report.migrationsApplied.find((item) => item.name === '007_custom_profile_import')?.note ?? null;
}

/** An HTTP refusal of the profile export, distinct from a failed request. */
export class ProfileExportError extends Error {
  override name = 'ProfileExportError';
}

/** Fetch the generated profile CSV for read-only inspection. */
export async function exportProfileCsv(
  design: DesignDocument, designRevision: number, signal?: AbortSignal,
  baseName = 'waveguide', fetcher: typeof fetch = fetch,
): Promise<string> {
  return activityOperation({ kind: 'export', label: 'Exporting profile CSV' }, fetcher, async (fetcher) => {
    const response = await fetcher('/api/export/profiles?kind=profiles', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
      body: JSON.stringify({ design: serializeDesign(design), designRevision, baseName }),
    });
    if (!response.ok) throw new ProfileExportError(await httpErrorMessage(response, 'WG could not export this profile. Try again.'));
    return response.text();
  });
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

/** Save server bytes under the attachment name supplied by their owner. */
export async function downloadResponse(response: Response, fallback: string): Promise<void> {
  downloadBlob(await response.blob(), responseFilename(response, fallback));
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
export function buildGeometryExport(
  kind: 'profiles', design: DesignDocument, designRevision: number,
  baseName: string, profileKind: 'pair', stepBody: StepBody,
  fetcher: typeof fetch,
): Promise<GeometryExportFile[]>;
export function buildGeometryExport(
  kind: 'step' | 'stl' | 'profiles', design: DesignDocument, designRevision: number,
  baseName: string, profileKind?: 'profiles' | 'slices', stepBody?: StepBody,
  fetcher?: typeof fetch,
): Promise<GeometryExportFile>;
export async function buildGeometryExport(
  kind: 'step' | 'stl' | 'profiles',
  design: DesignDocument,
  designRevision: number,
  baseName: string,
  profileKind?: 'profiles' | 'slices' | 'pair',
  stepBody: StepBody = 'solid',
  fetcher: typeof fetch = fetch,
): Promise<GeometryExportFile | GeometryExportFile[]> {
  const start = { kind: 'export' as const, label: `Exporting ${kind === 'profiles' ? 'profile CSV' : kind.toUpperCase()}` };
  const build = async (fetcher: typeof fetch, signal?: AbortSignal) => {
    const query = kind === 'profiles'
      ? `?kind=${profileKind ?? 'profiles'}`
      : kind === 'step' ? `?body=${stepBody}` : '';
    const response = await fetcher(`/api/export/${kind}${query}`, {
      method: 'POST', signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        design: serializeDesign(design),
        designRevision,
        baseName,
      }),
    });
    if (!response.ok) throw new Error(await errorMessage(response));
    const warning = response.headers.get('X-Export-Warning')?.trim() || undefined;
    if (kind === 'profiles' && profileKind === 'pair') {
      const pair = await response.json() as { files?: Array<{ filename?: string; text?: string; warning?: string }> };
      if (!Array.isArray(pair.files) || pair.files.length !== 2
        || pair.files.some(file => !file.filename || typeof file.text !== 'string')) {
        throw new Error('WG could not prepare both profile files. Export the profiles again.');
      }
      return pair.files.map(file => ({ filename: file.filename!, blob: new Blob([file.text!], { type: 'text/csv' }),
        ...(file.warning ? { warning: file.warning } : {}) }));
    }
    const suffix = kind === 'profiles' ? `_${profileKind ?? 'profiles'}.csv` : `.${kind}`;
    const filename = responseFilename(response, `${baseName}${suffix}`);
    const built: GeometryExportFile = { filename, blob: await response.blob() };
    return warning ? { ...built, warning } : built;
  };
  return kind === 'profiles' && profileKind !== 'pair' ? activityOperation(start, fetcher, fetcher => build(fetcher))
    : stagedActivity(start, fetcher, undefined, build);
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
  return activityOperation({ kind: 'export', label: `Exporting ${kind === 'profiles' ? 'profile CSV' : kind.toUpperCase()}` }, fetcher, async (fetcher) => {
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
  });
}

/** Read the same generated bore samples as the profile file export, without writing a file. */
export async function inspectProfileSamples(
  design: DesignDocument, designRevision: number, signal: AbortSignal, fetcher: typeof fetch = fetch,
) {
  return profileSamples(await exportProfileCsv(design, designRevision, signal, undefined, fetcher));
}

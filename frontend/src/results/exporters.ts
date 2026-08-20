import { downloadBlob, downloadText } from '../api/designIo';
import type { JobItem } from '../api/jobsSocket';
import { fetchJobResults } from '../api/results';
import { archiveFolderForJob, exportStemForJob, exportSubdirectoryForJob } from '../jobs/exportNaming';
import { buildDesignRecord, buildRunRecord } from '../jobs/runArchive';
import { resultExportSnapshot } from './exportContext';
import { serializeDesign, type DesignDocument } from '../stores/design';
import { designWireWithSolveSettings } from '../stores/designWire';
import type { WgSolveSettings } from '../stores/wgSolveBlock';
import { resolveChartTheme, type ExportFormat, type Preferences } from '../prefs/preferences';
import { applySmoothing, type SmoothingValue } from './smoothing';
import { buildOnAxisFrd, buildPolarFrdSet } from './frd';
import { complexToDb, impedanceUnits } from './mappers';
import { propagationReference } from './phaseConvention';
import { resultChannelFileSuffix, resultChannels, scopeResultChannel, type ResultPayload } from './types';
import { buildVituixCadProjectFiles, buildZma, hasElectricalImpedance } from './vituixcad';

export interface ExportContext {
  result?: ResultPayload;
  channelId?: string;
  design?: DesignDocument;
  designRevision?: number;
  /** The selected run's immutable directivity settings for ATH `.cfg` export. */
  polarConfig?: unknown;
  solveSettings?: WgSolveSettings | null;
  jobStem: string;
  /**
   * Where the bundle lands inside the workspace, `<design>/<run>` by default.
   * File names still use `jobStem`, so only the folder shape moved.
   */
  workspaceSubdirectory?: string;
  /** The run's own name, written into the `.cfg` this run exports. */
  designName?: string;
  preferences: Preferences;
  fetcher?: typeof fetch;
  saveBlob?: (blob: Blob, filename: string) => void;
  saveText?: (text: string, filename: string, type?: string) => void;
  now?: Date;
  destination?: 'manual' | 'workspace';
}

export interface ExportFailure { format: ExportFormat; reason: string }
export interface ExportBundleResult {
  files: string[];
  failures: ExportFailure[];
  /** Absolute backend destination for Workspace exports. */
  directory?: string;
}

function finite(value: unknown): number | null {
  const numeric = Number(value);
  return value !== null && value !== '' && Number.isFinite(numeric) ? numeric : null;
}

function csvCell(value: unknown): string {
  return finite(value) === null ? '' : String(value);
}

function resultFrequencies(result: ResultPayload): number[] {
  return result.spl_on_axis?.frequencies?.length ? result.spl_on_axis.frequencies : result.frequencies;
}

function flatDi(result: ResultPayload): Array<number | null> {
  const value = result.di?.di;
  if (Array.isArray(value)) return value;
  if (!value) return [];
  return value.horizontal ?? Object.values(value)[0] ?? [];
}

function smoothedSeries(result: ResultPayload, preferences: Preferences) {
  const frequencies = resultFrequencies(result);
  const impedanceFrequencies = result.impedance?.frequencies?.length ? result.impedance.frequencies : frequencies;
  const diFrequencies = result.di?.frequencies?.length ? result.di.frequencies : frequencies;
  return {
    frequencies,
    spl: applySmoothing(frequencies, result.spl_on_axis?.spl ?? [], preferences.smoothing),
    phase: result.spl_on_axis?.phase_degrees ?? [],
    diFrequencies,
    di: applySmoothing(diFrequencies, flatDi(result), preferences.smoothing),
    impedanceFrequencies,
    impedanceReal: applySmoothing(impedanceFrequencies, result.impedance?.real ?? [], preferences.smoothing),
    impedanceImaginary: applySmoothing(impedanceFrequencies, result.impedance?.imaginary ?? [], preferences.smoothing),
  };
}

function indexByFrequency(frequencies: number[]): Map<number, number> {
  const positions = new Map<number, number>();
  frequencies.forEach((frequency, position) => { if (!positions.has(frequency)) positions.set(frequency, position); });
  return positions;
}

interface JoinedRow {
  frequency: number;
  spl: SmoothingValue;
  di: SmoothingValue;
  impedanceReal: SmoothingValue;
  impedanceImaginary: SmoothingValue;
}

/**
 * Join SPL, DI, and impedance onto one frequency axis.
 *
 * The three quantities can come back on their own grids, which is why smoothedSeries
 * keeps them separate. Indexing them all by the SPL row position would mislabel the DI
 * and impedance columns with nothing downstream able to detect it, so rows are the
 * sorted union of every grid and a cell is filled only where that series has a sample
 * at that exact frequency. A series that does not reach a frequency leaves the cell
 * empty rather than interpolating a value the solver never produced.
 *
 * When the grids agree the union is the SPL grid and the output is unchanged.
 */
function joinSeries(series: ReturnType<typeof smoothedSeries>): JoinedRow[] {
  const grids = [series.frequencies, series.diFrequencies, series.impedanceFrequencies];
  const [splAt, diAt, impedanceAt] = grids.map(indexByFrequency);
  const union: number[] = [];
  const seen = new Set<number>();
  grids.forEach((grid) => grid.forEach((frequency) => { if (!seen.has(frequency)) { seen.add(frequency); union.push(frequency); } }));
  // A non-finite frequency cannot be ordered against the rest; park those at the end in
  // the order they arrived instead of letting NaN comparisons scramble the real rows.
  const rank = (frequency: number) => (Number.isFinite(frequency) ? 0 : 1);
  union.sort((a, b) => rank(a) - rank(b) || (rank(a) ? 0 : a - b));
  const sample = (values: SmoothingValue[], positions: Map<number, number>, frequency: number): SmoothingValue => {
    const position = positions.get(frequency);
    return position === undefined ? null : values[position] ?? null;
  };
  return union.map((frequency) => ({
    frequency,
    spl: sample(series.spl, splAt, frequency),
    di: sample(series.di, diAt, frequency),
    impedanceReal: sample(series.impedanceReal, impedanceAt, frequency),
    impedanceImaginary: sample(series.impedanceImaginary, impedanceAt, frequency),
  }));
}

export function buildFrequencyCsv(result: ResultPayload, preferences: Preferences): string {
  const unit = impedanceUnits(result).electrical ? 'ohms' : 'Z/(rho*c)';
  const rows = [`Frequency (Hz),SPL (dB),DI (dB),Impedance Real (${unit}),Impedance Imag (${unit})`];
  joinSeries(smoothedSeries(result, preferences)).forEach((row) => rows.push([
    row.frequency, csvCell(row.spl), csvCell(row.di), csvCell(row.impedanceReal), csvCell(row.impedanceImaginary),
  ].join(',')));
  return `${preferences.smoothing === 'none' ? '' : `# Smoothing: ${preferences.smoothing}\n`}${rows.join('\n')}\n`;
}

export function buildFullResultsJson(result: ResultPayload, preferences: Preferences, now = new Date()): string {
  return JSON.stringify({ timestamp: now.toISOString(), smoothing: preferences.smoothing, results: result }, null, 2);
}

function stats(values: Array<number | null>): { min: number; max: number; average: number } | null {
  const valid = values.map(finite).filter((value): value is number => value !== null);
  if (!valid.length) return null;
  return { min: Math.min(...valid), max: Math.max(...valid), average: valid.reduce((sum, value) => sum + value, 0) / valid.length };
}

export function buildSummaryText(result: ResultPayload, preferences: Preferences, now = new Date()): string {
  const series = smoothedSeries(result, preferences);
  const spl = stats(series.spl);
  const di = stats(series.di);
  const impedance = stats(series.impedanceReal);
  const rows = joinSeries(series);
  const frequencies = rows.map((row) => row.frequency);
  const electricalImpedance = impedanceUnits(result).electrical;
  const impedanceUnit = electricalImpedance ? 'ohms' : 'Z/(rho*c)';
  const impedanceColumns = electricalImpedance
    ? 'Freq(Hz)  SPL(dB)  DI(dB)  Z_Re(ohms)  Z_Im(ohms)'
    : 'Freq(Hz)  SPL(dB)  DI(dB)  Z_Re/(rho*c)  Z_Im/(rho*c)';
  const lines = ['BEM SIMULATION RESULTS', '=====================', '', `Generated: ${now.toISOString()}`, `Smoothing: ${preferences.smoothing}`];
  lines.push(frequencies.length ? `Frequency range: ${Math.min(...frequencies).toFixed(0)} - ${Math.max(...frequencies).toFixed(0)} Hz` : 'Frequency range: n/a');
  lines.push(`Number of points: ${frequencies.length}`, '');
  if (spl) lines.push('FREQUENCY RESPONSE SUMMARY', '--------------------------', `Average SPL: ${spl.average.toFixed(2)} dB`, `SPL Range: ${spl.min.toFixed(2)} to ${spl.max.toFixed(2)} dB`, `Variation: ${(spl.max - spl.min).toFixed(2)} dB`, '');
  if (di) lines.push('DIRECTIVITY INDEX SUMMARY', '-------------------------', `Average DI: ${di.average.toFixed(2)} dB`, `DI Range: ${di.min.toFixed(2)} to ${di.max.toFixed(2)} dB`, '');
  if (impedance) lines.push('IMPEDANCE SUMMARY', '-----------------', `Average Real Part ${impedanceUnit}: ${impedance.average.toFixed(2)}`, '');
  lines.push('DETAILED DATA', '=============', impedanceColumns);
  rows.forEach((row) => lines.push([row.frequency, row.spl, row.di, row.impedanceReal, row.impedanceImaginary].map((value) => finite(value)?.toFixed(2) ?? 'n/a').join('  ')));
  return `${lines.join('\n')}\n`;
}

function patternDb(value: unknown): number | null {
  if (Array.isArray(value)) return complexToDb(Number(value[0]), Number(value[1]));
  return finite(value);
}

export function buildPolarCsv(result: ResultPayload): string {
  const rows = ['Frequency_Hz,Plane,Theta_deg,SPL_norm_dB'];
  const frequencies = resultFrequencies(result);
  const directivity = (result.directivity ?? {}) as Record<string, NonNullable<ResultPayload['directivity']>['horizontal']>;
  const planeOrder = ['horizontal', 'vertical', 'diagonal'];
  const rank = (plane: string) => { const index = planeOrder.indexOf(plane); return index < 0 ? planeOrder.length : index; };
  const order = Object.keys(directivity).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  order.forEach((plane) => (directivity[plane] ?? []).forEach((pattern, frequencyIndex) => {
    pattern.forEach(([angle, value]) => rows.push(`${frequencies[Math.min(frequencyIndex, Math.max(0, frequencies.length - 1))] ?? ''},${plane},${angle},${patternDb(value) ?? ''}`));
  }));
  return `${rows.join('\n')}\n`;
}

export function buildImpedanceCsv(result: ResultPayload): string {
  const frequencies = result.impedance?.frequencies ?? result.frequencies;
  const suffix = impedanceUnits(result).electrical ? 'Ohm' : 'Z_over_rho_c';
  const rows = [`Freq_Hz,Z_Real_${suffix},Z_Imag_${suffix}`];
  frequencies.forEach((frequency, index) => rows.push(`${frequency},${csvCell(result.impedance?.real?.[index])},${csvCell(result.impedance?.imaginary?.[index])}`));
  return `${rows.join('\n')}\n`;
}

export function buildVacs(result: ResultPayload, now = new Date()): string {
  const impedance = result.impedance;
  const plane = result.directivity?.horizontal ? 'horizontal' : Object.keys(result.directivity ?? {})[0];
  const patterns = plane ? result.directivity?.[plane as keyof NonNullable<ResultPayload['directivity']>] ?? [] : [];
  const lines = ['// Waveguide Generator Spectrum Data', `// ${now.toISOString()}`, 'SourceDesc=VACS_Data_Text', 'Version=1.1.0'];
  if (impedance?.frequencies?.length) {
    lines.push('Data_Format=Complex', 'Data_LevelType=Impedance10', 'Data_Domain=Frequency', 'Data_AbscUnit=Hz', 'Data');
    impedance.frequencies.forEach((frequency, index) => lines.push(`${frequency}   ${finite(impedance.real?.[index]) ?? 0} ${finite(impedance.imaginary?.[index]) ?? 0}`));
    lines.push('Data_End');
  }
  if (patterns.length) {
    lines.push('Data_Format=Complex', 'Data_LevelType=Peak', 'Data_Domain=Frequency', `Data_Legend="Polar, Pressure, ${plane}"`, 'Data');
    patterns.forEach((pattern, index) => lines.push(`${resultFrequencies(result)[Math.min(index, resultFrequencies(result).length - 1)] ?? ''}${pattern.map(([, value]) => `   ${Math.pow(10, (patternDb(value) ?? -Infinity) / 20) || 0} 0`).join('')}`));
    lines.push('Data_End');
  }
  return `${lines.join('\n')}\n`;
}

function filenameFromResponse(response: Response, fallback: string): string {
  return response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
}

async function responseError(response: Response): Promise<Error> {
  let detail = `${response.status} ${response.statusText}`.trim();
  try { const body = await response.json() as { detail?: string }; if (body.detail) detail = body.detail; } catch { /* status remains */ }
  return new Error(detail);
}

interface GeometryDownload {
  blob: Blob;
  filename: string;
}

export interface WorkspaceFile {
  filename: string;
  blob: Blob;
}

export interface WorkspaceWriteResponse {
  directory: string;
  files: string[];
}

async function blobBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const chunks: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 0x8000)));
  }
  return btoa(chunks.join(''));
}

/**
 * What to do about files a previous export already wrote.
 *
 * Automatic post-run export is a background write nobody asked for twice, so
 * it merges and refuses to change anything: `merge_identical`. A user choosing
 * an export a second time is asking for the file again, so it replaces:
 * `overwrite`. Merging a repeat manual export cannot work -- the JSON, summary,
 * and VACS builders stamp the current time into their output, and smoothing or
 * chart preferences change the bytes too, so every repeat differed and the
 * whole bundle was rejected with a 409.
 */
export type ExistingFilePolicy = 'merge_identical' | 'overwrite';

/** The folder a bundle is written to, which callers may group by design. */
export function workspaceSubdirectory(context: ExportContext): string {
  return context.workspaceSubdirectory?.trim() || context.jobStem;
}

export async function writeWorkspaceFiles(
  subdirectory: string,
  members: WorkspaceFile[],
  fetcher: typeof fetch = fetch,
  existing: ExistingFilePolicy = 'merge_identical',
): Promise<WorkspaceWriteResponse> {
  const encoded = await Promise.all(members.map(async ({ filename, blob }) => ({
    relative_path: filename,
    content_base64: await blobBase64(blob),
  })));
  const response = await fetcher('/api/workspace/write-export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subdirectory, members: encoded, existing }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<WorkspaceWriteResponse>;
}

async function fetchGeometry(context: ExportContext, kind: 'step' | 'stl' | 'profiles', filename: string, profileKind?: 'profiles' | 'slices'): Promise<GeometryDownload> {
  if (!context.design) throw new Error('This export requires a saved design snapshot.');
  const fetcher = context.fetcher ?? fetch;
  const baseName = context.jobStem;
  const query = kind === 'profiles' ? `?kind=${profileKind ?? 'profiles'}` : '';
  const response = await fetcher(`/api/export/${kind}${query}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ design: serializeDesign(context.design), designRevision: context.designRevision ?? 0, baseName }) });
  if (!response.ok) throw await responseError(response);
  return { blob: await response.blob(), filename: filenameFromResponse(response, filename) };
}

async function postGeometry(context: ExportContext, kind: 'step' | 'stl' | 'profiles', filename: string, profileKind?: 'profiles' | 'slices'): Promise<string> {
  const output = await fetchGeometry(context, kind, filename, profileKind);
  (context.saveBlob ?? downloadBlob)(output.blob, output.filename);
  return output.filename;
}

function requireResult(context: ExportContext): ResultPayload {
  if (!context.result) throw new Error('This export requires completed result data.');
  return scopeResultChannel(context.result, context.channelId);
}

function exportContextBaseName(context: ExportContext): string {
  return `${context.jobStem}${context.result ? resultChannelFileSuffix(context.result, context.channelId) : ''}`;
}

async function chartPng(context: ExportContext): Promise<string[]> {
  const result = requireResult(context);
  const fetcher = context.fetcher ?? fetch;
  // Validate both canonical renderer responses before saving the first image.
  // This keeps PNG a single retryable format rather than leaving half a chart
  // set behind when the directivity renderer fails.
  const directivityResponse = await fetcher('/api/render-directivity', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(buildDirectivityRenderPayload(result, context.preferences)),
  });
  if (!directivityResponse.ok) throw await responseError(directivityResponse);
  const directivityBody = await directivityResponse.json() as { image?: string };
  if (!directivityBody.image) throw new Error('HornLab plots returned no directivity image.');
  const response = await fetcher('/api/render-charts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildChartRenderPayload(result, context.preferences)) });
  if (!response.ok) throw await responseError(response);
  const body = await response.json() as { charts?: Record<string, string> };
  const entries = Object.entries(body.charts ?? {});
  if (!entries.length) throw new Error('Chart renderer returned no images.');
  const baseName = exportContextBaseName(context);
  const files = entries.map(([chart, data]) => {
    const encoded = data.includes(',') ? data.slice(data.indexOf(',') + 1) : data;
    const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
    const filename = `${baseName}_${chart}.png`;
    (context.saveBlob ?? downloadBlob)(new Blob([bytes], { type: 'image/png' }), filename);
    return filename;
  });
  const directivityFilename = `${baseName}_directivity_map.png`;
  const directivityEncoded = directivityBody.image.includes(',')
    ? directivityBody.image.slice(directivityBody.image.indexOf(',') + 1)
    : directivityBody.image;
  const directivityBytes = Uint8Array.from(atob(directivityEncoded), (character) => character.charCodeAt(0));
  (context.saveBlob ?? downloadBlob)(new Blob([directivityBytes], { type: 'image/png' }), directivityFilename);
  return [...files, directivityFilename];
}

export interface ChartReference {
  result: ResultPayload;
  label?: string;
}

/**
 * The impedance tags the renderer gets, in hornlab-plots' own vocabulary.
 *
 * Both were hardcoded to the normalized pair, so a driver-coupled run drew an Ω
 * axis on screen and shipped a Z/ρc label in the PNG — the exact screen-against-
 * file disagreement the on-screen unit fix exists to remove. The predicate is
 * `impedanceUnits`, deliberately the same one the chart axis reads, because two
 * predicates are how they drifted apart in the first place.
 *
 * `impedance_normalization` is the more consequential of the two: hornlab-plots
 * compares it between primary and reference and *skips the overlay* on a
 * mismatch. With both pinned to `rho_c` an ohms primary silently accepted a
 * Z/ρc reference curve onto its own scale, where a 0..2 acoustic trace
 * flatlines along the bottom of a 4..60 Ω axis and reads as a dead run.
 *
 * Known gap: `_impedance_ylabel` in hornlab-plots 0.1.1 has no ohms branch — it
 * returns "Z / ρc" for anything mentioning rho and "Z [Pa·s/m]" otherwise — so
 * the exported electrical PNG still carries an acoustic axis label, now the
 * generic one rather than a positive claim of normalization. Closing that needs
 * an Ω branch in hornlab-plots and a pin bump; it cannot be done from here.
 */
function impedanceRenderTags(result: ResultPayload): Record<string, string> {
  return impedanceUnits(result).electrical
    ? { impedance_units: 'ohms', impedance_normalization: 'absolute' }
    : { impedance_units: 'Z/(rho*c)', impedance_normalization: 'rho_c' };
}

function buildChartReferencePayload(reference: ChartReference, preferences: Preferences): Record<string, unknown> {
  const series = smoothedSeries(reference.result, preferences);
  const observation = reference.result.metadata?.observation;
  const observationRecord = observation && typeof observation === 'object' ? observation as Record<string, unknown> : {};
  return {
    label: reference.label ?? null,
    frequencies: series.frequencies,
    spl: series.spl,
    sound_speed_m_per_s: finite(observationRecord.sound_speed_m_per_s),
    di: series.di,
    di_frequencies: series.diFrequencies,
    impedance_frequencies: series.impedanceFrequencies,
    impedance_real: series.impedanceReal,
    impedance_imaginary: series.impedanceImaginary,
    ...impedanceRenderTags(reference.result),
    beam_shape: reference.result.beam_shape ?? null,
  };
}

export function buildChartRenderPayload(
  result: ResultPayload,
  preferences: Preferences,
  reference?: ChartReference,
): Record<string, unknown> {
  const series = smoothedSeries(result, preferences);
  // Resolve these as one validity unit. Sending a distance with no sound speed
  // makes hornlab-plots assume 343 m/s while the browser correctly declines to
  // de-embed, so partially valid legacy metadata would produce two phase plots.
  const phaseReference = propagationReference(result);
  const convention = result.metadata?.phase_time_convention ?? result.metadata?.time_convention ?? result.metadata?.spatial_phase_convention;
  return {
    frequencies: series.frequencies,
    spl: series.spl,
    phase_degrees: preferences.splPhase ? series.phase : [],
    phase_reference_distance_m: phaseReference?.distanceM ?? null,
    phase_time_convention: phaseReference && convention != null ? String(convention) : null,
    sound_speed_m_per_s: phaseReference?.speedOfSoundMps ?? null,
    di: series.di,
    di_frequencies: series.diFrequencies,
    impedance_frequencies: series.impedanceFrequencies,
    impedance_real: series.impedanceReal,
    impedance_imaginary: series.impedanceImaginary,
    ...impedanceRenderTags(result),
    directivity: result.directivity ?? {},
    beam_shape: result.beam_shape ?? null,
    reference: reference ? buildChartReferencePayload(reference, preferences) : null,
    theme: resolveChartTheme(preferences.chartTheme),
  };
}

export function buildDirectivityRenderPayload(
  result: ResultPayload,
  preferences: Preferences,
  plane?: string,
  reference?: ChartReference,
): Record<string, unknown> {
  const directivityFor = (source: ResultPayload): Record<string, unknown> => {
    if (!plane) return source.directivity ?? {};
    const patterns = (source.directivity as Record<string, unknown> | undefined)?.[plane];
    return patterns ? { [plane]: patterns } : {};
  };
  return {
    frequencies: result.frequencies,
    directivity: directivityFor(result),
    reference_frequencies: reference?.result.frequencies ?? null,
    reference_directivity: reference ? directivityFor(reference.result) : null,
    reference_label: reference?.label ?? null,
    reference_level: preferences.mapReference,
    angle_guide_step: preferences.directivityGuideInterval,
    theme: resolveChartTheme(preferences.chartTheme),
  };
}

async function writeManualPolarFrd(
  context: ExportContext,
  files: ReturnType<typeof buildPolarFrdSet>,
): Promise<string[]> {
  const fetcher = context.fetcher ?? fetch;
  const pathResponse = await fetcher('/api/workspace/path');
  if (!pathResponse.ok) throw await responseError(pathResponse);
  let workspace = await pathResponse.json() as { selected?: boolean };
  if (!workspace.selected) {
    const selectResponse = await fetcher('/api/workspace/select', { method: 'POST' });
    if (!selectResponse.ok) throw await responseError(selectResponse);
    workspace = await selectResponse.json() as { selected?: boolean };
    if (!workspace.selected) throw new Error('Workspace selection was cancelled. No files were written.');
  }
  const response = await fetcher('/api/workspace/write-export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      subdirectory: workspaceSubdirectory(context),
      members: files.map(({ filename, text }) => ({ relative_path: filename, text })),
    }),
  });
  if (!response.ok) throw await responseError(response);
  return ((await response.json()) as WorkspaceWriteResponse).files;
}

export async function runExportFormat(format: ExportFormat, context: ExportContext): Promise<string[]> {
  const baseName = exportContextBaseName(context);
  const saveText = context.saveText ?? downloadText;
  const now = context.now ?? new Date();
  if (format === 'mwg_config') {
    if (!context.design) throw new Error('This export requires a saved design snapshot.');
    const response = await (context.fetcher ?? fetch)('/api/design/serialize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        design: designWireWithSolveSettings(
          serializeDesign(context.design),
          context.polarConfig,
          context.solveSettings ?? null,
          // A config exported from run #42 is titled for run #42, not for
          // whatever design happens to be on screen now.
          context.designName ?? context.jobStem,
        ),
        filename: `${baseName}_config.cfg`,
      }),
    });
    if (!response.ok) throw await responseError(response);
    const body = await response.json() as { text: string; suggestedFilename?: string };
    const filename = body.suggestedFilename ?? `${baseName}_config.cfg`;
    saveText(body.text, filename);
    return [filename];
  }
  if (format === 'step') return [await postGeometry(context, 'step', `${baseName}.step`)];
  if (format === 'stl') return [await postGeometry(context, 'stl', `${baseName}.stl`)];
  if (format === 'fusion_csv') {
    // Fetch both halves before triggering either browser download. Otherwise a
    // failed slices request leaves an untracked profiles file that is duplicated
    // when the format is retried in a later app session.
    const outputs = await Promise.all([
      fetchGeometry(context, 'profiles', `${baseName}_profiles.csv`, 'profiles'),
      fetchGeometry(context, 'profiles', `${baseName}_slices.csv`, 'slices'),
    ]);
    const saveBlob = context.saveBlob ?? downloadBlob;
    outputs.forEach((output) => saveBlob(output.blob, output.filename));
    return outputs.map((output) => output.filename);
  }
  if (format === 'png') return chartPng(context);
  if (format === 'on_axis_frd') {
    const result = requireResult(context);
    const filename = `${baseName}.frd`;
    saveText(buildOnAxisFrd(result, context.preferences), filename, 'text/plain;charset=utf-8');
    return [filename];
  }
  if (format === 'polar_frd') {
    if (!context.result) throw new Error('This export requires completed result data.');
    const channels = resultChannels(context.result);
    const channelIds = context.channelId
      ? [context.channelId]
      : channels.length > 1 ? channels.map(({ id }) => id) : [undefined];
    const files = channelIds.flatMap((channelId) => (
      buildPolarFrdSet(context.result!, context.preferences, context.jobStem, channelId)
    ));
    if (!files.length) throw new Error('This run has no directivity data for a polar FRD set.');
    if (context.destination !== 'workspace') return writeManualPolarFrd(context, files);
    files.forEach(({ text, filename }) => saveText(text, filename, 'text/plain;charset=utf-8'));
    return files.map(({ filename }) => filename);
  }
  if (format === 'vxp') {
    if (!context.result) throw new Error('This export requires completed result data.');
    // Build every member before saving the first. A malformed project must not
    // leave orphaned support files in Downloads or in the automatic staging set.
    const files = buildVituixCadProjectFiles(context.result, context.preferences, context.jobStem);
    files.forEach(({ text, filename, type }) => saveText(text, filename, type));
    return files.map(({ filename }) => filename);
  }
  const result = requireResult(context);
  const builders: Record<Exclude<ExportFormat, 'mwg_config' | 'step' | 'stl' | 'fusion_csv' | 'png' | 'on_axis_frd' | 'polar_frd' | 'vxp'>, () => [string, string, string]> = {
    csv: () => [buildFrequencyCsv(result, context.preferences), `${baseName}.csv`, 'text/csv;charset=utf-8'],
    json: () => [buildFullResultsJson(result, context.preferences, now), `${baseName}.json`, 'application/json;charset=utf-8'],
    txt: () => [buildSummaryText(result, context.preferences, now), `${baseName}_summary.txt`, 'text/plain;charset=utf-8'],
    polar_csv: () => [buildPolarCsv(result), `${baseName}_polar.csv`, 'text/csv;charset=utf-8'],
    impedance_csv: () => [buildImpedanceCsv(result), `${baseName}_impedance.csv`, 'text/csv;charset=utf-8'],
    zma: () => [buildZma(result), `${baseName}.zma`, 'text/plain;charset=utf-8'],
    vacs: () => [buildVacs(result, now), `${baseName}_spectrum.txt`, 'text/plain;charset=utf-8'],
  };
  const [content, filename, type] = builders[format]();
  saveText(content, filename, type);
  return [filename];
}

export async function runExportBundle(context: ExportContext, formats = context.preferences.exportFormats): Promise<ExportBundleResult> {
  const files: string[] = [];
  const failures: ExportFailure[] = [];
  const emitted = new Set<string>();
  const saveBlob = context.saveBlob ?? downloadBlob;
  const saveText = context.saveText ?? downloadText;
  // Polar FRD owns every channel as one set so its manual Workspace write stays
  // one request. The other result formats dispatch independently per channel.
  const resultFormats = new Set<ExportFormat>(['png', 'on_axis_frd', 'csv', 'json', 'txt', 'polar_csv', 'impedance_csv', 'zma', 'vacs']);
  for (const format of formats) {
    const allChannels = context.result && !context.channelId && resultFormats.has(format)
      ? resultChannels(context.result) : [];
    let channels = allChannels;
    if (format === 'zma' && channels.some(({ result }) => hasElectricalImpedance(result))) {
      // A combined or unit-drive channel is not a failed electrical export; it
      // is deliberately outside the ZMA artifact family. If none are eligible,
      // retain every variant so the caller receives the explicit refusal.
      channels = channels.filter(({ result }) => hasElectricalImpedance(result));
    }
    const variants = allChannels.length > 1 && channels.length
      ? channels.map(({ id }) => ({ ...context, channelId: id }))
      : [context];
    for (const variant of variants) {
      try {
        // A VXP owns its support FRD/ZMA files, while users may also select ZMA
        // explicitly. Suppress a second write/download by portable relative
        // name; the builders are shared, so the duplicate is byte-identical.
        const produced = await runExportFormat(format, {
          ...variant,
          saveBlob: (blob, filename) => {
            if (emitted.has(filename)) return;
            emitted.add(filename);
            saveBlob(blob, filename);
          },
          saveText: (content, filename, type) => {
            if (emitted.has(filename)) return;
            emitted.add(filename);
            saveText(content, filename, type);
          },
        });
        produced.forEach((filename) => { if (!files.includes(filename)) files.push(filename); });
      }
      catch (error) { failures.push({ format, reason: error instanceof Error ? error.message : String(error) }); }
    }
  }
  return { files, failures };
}

/** Prepare exports in memory, then write them through the backend Workspace.
 * Automatic exports and every run-result export entry point use this path so
 * browser download permissions cannot silently redirect files to Downloads. */
export async function runWorkspaceExportBundle(
  context: ExportContext,
  formats = context.preferences.autoExportFormats,
  existing: ExistingFilePolicy = 'merge_identical',
): Promise<ExportBundleResult> {
  const prepared = new Map<string, WorkspaceFile>();
  const bundle = await runExportBundle({
    ...context,
    destination: 'workspace',
    // VXP is a self-contained bundle and can share a ZMA dependency with an
    // explicitly selected ZMA format. Stage by relative name so automatic
    // export sends one backend member instead of a duplicate-path request.
    saveBlob: (blob, filename) => prepared.set(filename, { blob, filename }),
    saveText: (content, filename, type = 'text/plain;charset=utf-8') => {
      prepared.set(filename, { filename, blob: new Blob([content], { type }) });
    },
  }, formats);
  if (!prepared.size) return { ...bundle, files: [] };
  const written = await writeWorkspaceFiles(
    workspaceSubdirectory(context),
    [...prepared.values()],
    context.fetcher ?? fetch,
    existing,
  );
  return { ...bundle, directory: written.directory, files: written.files };
}

export async function downloadMeshArtifact(
  job: Pick<JobItem, 'id' | 'run_number' | 'label' | 'config_summary'>,
  fetcher: typeof fetch = fetch,
  saveBlob: (blob: Blob, filename: string) => void = downloadBlob,
): Promise<string> {
  const response = await fetcher(`/api/mesh-artifact/${encodeURIComponent(job.id)}`);
  if (!response.ok) throw await responseError(response);
  const filename = `${exportStemForJob(job)}.msh`;
  saveBlob(await response.blob(), filename);
  return filename;
}

export async function saveMeshArtifactToWorkspace(
  job: Pick<JobItem, 'id' | 'run_number' | 'label' | 'config_summary' | 'cad_source'>,
  fetcher: typeof fetch = fetch,
): Promise<string> {
  let prepared: WorkspaceFile | null = null;
  await downloadMeshArtifact(job, fetcher, (blob, filename) => { prepared = { blob, filename }; });
  if (!prepared) throw new Error('Mesh artifact returned no file.');
  const written = await writeWorkspaceFiles(exportSubdirectoryForJob(job), [prepared], fetcher);
  if (!written.files[0]) throw new Error('Workspace returned no saved mesh path.');
  return written.files[0];
}


/**
 * The formats a run archive always contains, whatever the user exports.
 *
 * JSON carries the full result payload, so an archived run can be reread long
 * after the job database has pruned it. CSV is there because a spreadsheet is
 * how most people actually open a curve again.
 */
export const ARCHIVE_FORMATS: ExportFormat[] = ['json', 'csv'];

function textFile({ filename, text }: { filename: string; text: string }): WorkspaceFile {
  return { filename, blob: new Blob([text], { type: 'application/json' }) };
}

/**
 * Write one completed run to its design's archive folder.
 *
 * The run record is written with `merge_identical` because it is evidence: it
 * is derived only from the stored job, so re-archiving the same run is a
 * byte-identical no-op rather than a rewrite. The design pointer file is
 * derived state and follows a rename, so it is the one file allowed to
 * replace itself.
 */
export async function archiveRunToWorkspace(
  job: JobItem,
  preferences: Preferences,
  fetcher: typeof fetch = fetch,
): Promise<string[]> {
  const subdirectory = exportSubdirectoryForJob(job);
  const bundle = await runWorkspaceExportBundle({
    result: await fetchJobResults(job.id) as ResultPayload,
    ...resultExportSnapshot(job),
    jobStem: exportStemForJob(job),
    workspaceSubdirectory: subdirectory,
    designName: job.label ?? undefined,
    preferences,
    fetcher,
  }, ARCHIVE_FORMATS);
  if (bundle.failures.length) {
    throw new Error(bundle.failures.map(({ format, reason }) => `${format} (${reason})`).join(', '));
  }
  const record = await writeWorkspaceFiles(
    subdirectory, [textFile(buildRunRecord(job))], fetcher, 'merge_identical',
  );
  const pointer = await writeWorkspaceFiles(
    archiveFolderForJob(job), [textFile(buildDesignRecord(job))], fetcher, 'overwrite',
  );
  return [...bundle.files, ...record.files, ...pointer.files];
}

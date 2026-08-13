import { downloadBlob, downloadText } from '../api/designIo';
import type { JobItem } from '../api/jobsSocket';
import { exportStemForJob } from '../jobs/exportNaming';
import { serializeDesign, type DesignDocument } from '../stores/design';
import { resolveChartTheme, type ExportFormat, type Preferences } from '../prefs/preferences';
import { applySmoothing, type SmoothingValue } from './smoothing';
import { complexToDb } from './mappers';
import { resultChannelFileSuffix, resultChannels, scopeResultChannel, type ResultPayload } from './types';

export interface ExportContext {
  result?: ResultPayload;
  channelId?: string;
  design?: DesignDocument;
  designRevision?: number;
  jobStem: string;
  preferences: Preferences;
  fetcher?: typeof fetch;
  saveBlob?: (blob: Blob, filename: string) => void;
  saveText?: (text: string, filename: string, type?: string) => void;
  now?: Date;
}

export interface ExportFailure { format: ExportFormat; reason: string }
export interface ExportBundleResult { files: string[]; failures: ExportFailure[] }

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
  const rows = ['Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Z/(rho*c)),Impedance Imag (Z/(rho*c))'];
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
  const lines = ['BEM SIMULATION RESULTS', '=====================', '', `Generated: ${now.toISOString()}`, `Smoothing: ${preferences.smoothing}`];
  lines.push(frequencies.length ? `Frequency range: ${Math.min(...frequencies).toFixed(0)} - ${Math.max(...frequencies).toFixed(0)} Hz` : 'Frequency range: n/a');
  lines.push(`Number of points: ${frequencies.length}`, '');
  if (spl) lines.push('FREQUENCY RESPONSE SUMMARY', '--------------------------', `Average SPL: ${spl.average.toFixed(2)} dB`, `SPL Range: ${spl.min.toFixed(2)} to ${spl.max.toFixed(2)} dB`, `Variation: ${(spl.max - spl.min).toFixed(2)} dB`, '');
  if (di) lines.push('DIRECTIVITY INDEX SUMMARY', '-------------------------', `Average DI: ${di.average.toFixed(2)} dB`, `DI Range: ${di.min.toFixed(2)} to ${di.max.toFixed(2)} dB`, '');
  if (impedance) lines.push('IMPEDANCE SUMMARY', '-----------------', `Average Real Part Z/(rho*c): ${impedance.average.toFixed(2)}`, '');
  lines.push('DETAILED DATA', '=============', 'Freq(Hz)  SPL(dB)  DI(dB)  Z_Re/(rho*c)  Z_Im/(rho*c)');
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
  const rows = ['Freq_Hz,Z_Real_Z_over_rho_c,Z_Imag_Z_over_rho_c'];
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

interface WorkspaceFile {
  filename: string;
  blob: Blob;
}

interface WorkspaceWriteResponse {
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

export async function writeWorkspaceFiles(
  subdirectory: string,
  members: WorkspaceFile[],
  fetcher: typeof fetch = fetch,
): Promise<WorkspaceWriteResponse> {
  const encoded = await Promise.all(members.map(async ({ filename, blob }) => ({
    relative_path: filename,
    content_base64: await blobBase64(blob),
  })));
  const response = await fetcher('/api/workspace/write-export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subdirectory, members: encoded, existing: 'merge_identical' }),
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
  const response = await fetcher('/api/render-charts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildChartRenderPayload(result, context.preferences)) });
  if (!response.ok) throw await responseError(response);
  const body = await response.json() as { charts?: Record<string, string> };
  const entries = Object.entries(body.charts ?? {});
  if (!entries.length) throw new Error('Chart renderer returned no images.');
  const baseName = exportContextBaseName(context);
  return entries.map(([chart, data]) => {
    const encoded = data.includes(',') ? data.slice(data.indexOf(',') + 1) : data;
    const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
    const filename = `${baseName}_${chart}.png`;
    (context.saveBlob ?? downloadBlob)(new Blob([bytes], { type: 'image/png' }), filename);
    return filename;
  });
}

export interface ChartReference {
  result: ResultPayload;
  label?: string;
}

function buildChartReferencePayload(reference: ChartReference, preferences: Preferences): Record<string, unknown> {
  const series = smoothedSeries(reference.result, preferences);
  return {
    label: reference.label ?? null,
    frequencies: series.frequencies,
    spl: series.spl,
    di: series.di,
    di_frequencies: series.diFrequencies,
    impedance_frequencies: series.impedanceFrequencies,
    impedance_real: series.impedanceReal,
    impedance_imaginary: series.impedanceImaginary,
    impedance_units: 'Z/(rho*c)',
    impedance_normalization: 'rho_c',
    beam_shape: reference.result.beam_shape ?? null,
  };
}

export function buildChartRenderPayload(
  result: ResultPayload,
  preferences: Preferences,
  reference?: ChartReference,
): Record<string, unknown> {
  const series = smoothedSeries(result, preferences);
  const observation = result.metadata?.observation;
  const observationRecord = observation && typeof observation === 'object' ? observation as Record<string, unknown> : {};
  const distance = finite(observationRecord.effective_distance_m ?? observationRecord.requested_distance_m);
  const convention = result.metadata?.phase_time_convention ?? result.metadata?.time_convention ?? result.metadata?.spatial_phase_convention;
  return {
    frequencies: series.frequencies,
    spl: series.spl,
    phase_degrees: series.phase,
    phase_reference_distance_m: distance,
    phase_time_convention: convention == null ? null : String(convention),
    di: series.di,
    di_frequencies: series.diFrequencies,
    impedance_frequencies: series.impedanceFrequencies,
    impedance_real: series.impedanceReal,
    impedance_imaginary: series.impedanceImaginary,
    impedance_units: 'Z/(rho*c)',
    impedance_normalization: 'rho_c',
    directivity: result.directivity ?? {},
    beam_shape: result.beam_shape ?? null,
    reference: reference ? buildChartReferencePayload(reference, preferences) : null,
    theme: resolveChartTheme(preferences.chartTheme),
  };
}

export async function runExportFormat(format: ExportFormat, context: ExportContext): Promise<string[]> {
  const baseName = exportContextBaseName(context);
  const saveText = context.saveText ?? downloadText;
  const now = context.now ?? new Date();
  if (format === 'mwg_config') {
    if (!context.design) throw new Error('This export requires a saved design snapshot.');
    const response = await (context.fetcher ?? fetch)('/api/design/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ design: serializeDesign(context.design), filename: `${baseName}_config.cfg` }) });
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
  const result = requireResult(context);
  const builders: Record<Exclude<ExportFormat, 'mwg_config' | 'step' | 'stl' | 'fusion_csv' | 'png'>, () => [string, string, string]> = {
    csv: () => [buildFrequencyCsv(result, context.preferences), `${baseName}.csv`, 'text/csv;charset=utf-8'],
    json: () => [buildFullResultsJson(result, context.preferences, now), `${baseName}.json`, 'application/json;charset=utf-8'],
    txt: () => [buildSummaryText(result, context.preferences, now), `${baseName}_summary.txt`, 'text/plain;charset=utf-8'],
    polar_csv: () => [buildPolarCsv(result), `${baseName}_polar.csv`, 'text/csv;charset=utf-8'],
    impedance_csv: () => [buildImpedanceCsv(result), `${baseName}_impedance.csv`, 'text/csv;charset=utf-8'],
    vacs: () => [buildVacs(result, now), `${baseName}_spectrum.txt`, 'text/plain;charset=utf-8'],
  };
  const [content, filename, type] = builders[format]();
  saveText(content, filename, type);
  return [filename];
}

export async function runExportBundle(context: ExportContext, formats = context.preferences.exportFormats): Promise<ExportBundleResult> {
  const files: string[] = [];
  const failures: ExportFailure[] = [];
  const resultFormats = new Set<ExportFormat>(['png', 'csv', 'json', 'txt', 'polar_csv', 'impedance_csv', 'vacs']);
  for (const format of formats) {
    const channels = context.result && !context.channelId && resultFormats.has(format)
      ? resultChannels(context.result)
      : [];
    const variants = channels.length > 1
      ? channels.map(({ id }) => ({ ...context, channelId: id }))
      : [context];
    for (const variant of variants) {
      try { files.push(...await runExportFormat(format, variant)); }
      catch (error) { failures.push({ format, reason: error instanceof Error ? error.message : String(error) }); }
    }
  }
  return { files, failures };
}

/** Prepare automatic exports in memory, then write them through the backend.
 * Browser downloads require repeated user permission and are therefore kept
 * for explicit manual exports only. */
export async function runWorkspaceExportBundle(
  context: ExportContext,
  formats = context.preferences.autoExportFormats,
): Promise<ExportBundleResult> {
  const prepared: WorkspaceFile[] = [];
  const bundle = await runExportBundle({
    ...context,
    saveBlob: (blob, filename) => prepared.push({ blob, filename }),
    saveText: (content, filename, type = 'text/plain;charset=utf-8') => {
      prepared.push({ filename, blob: new Blob([content], { type }) });
    },
  }, formats);
  if (!prepared.length) return { ...bundle, files: [] };
  const written = await writeWorkspaceFiles(
    context.jobStem,
    prepared,
    context.fetcher ?? fetch,
  );
  return { ...bundle, files: written.files };
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
  job: Pick<JobItem, 'id' | 'run_number' | 'label' | 'config_summary'>,
  fetcher: typeof fetch = fetch,
): Promise<string> {
  let prepared: WorkspaceFile | null = null;
  await downloadMeshArtifact(job, fetcher, (blob, filename) => { prepared = { blob, filename }; });
  if (!prepared) throw new Error('Mesh artifact returned no file.');
  const written = await writeWorkspaceFiles(exportStemForJob(job), [prepared], fetcher);
  if (!written.files[0]) throw new Error('Workspace returned no saved mesh path.');
  return written.files[0];
}

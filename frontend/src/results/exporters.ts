import { downloadBlob, downloadText } from '../api/designIo';
import { serializeDesign, type DesignDocument } from '../stores/design';
import type { ExportFormat, Preferences } from '../prefs/preferences';
import { exportBaseName } from '../prefs/preferences';
import { applySmoothing } from './smoothing';
import { complexToDb } from './mappers';
import type { ResultPayload } from './types';

export interface ExportContext {
  result?: ResultPayload;
  design?: DesignDocument;
  designRevision?: number;
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

export function buildFrequencyCsv(result: ResultPayload, preferences: Preferences): string {
  const series = smoothedSeries(result, preferences);
  const rows = ['Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Z/(rho*c)),Impedance Imag (Z/(rho*c))'];
  series.frequencies.forEach((frequency, index) => rows.push([
    frequency, csvCell(series.spl[index]), csvCell(series.di[index]), csvCell(series.impedanceReal[index]), csvCell(series.impedanceImaginary[index]),
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
  const lines = ['BEM SIMULATION RESULTS', '=====================', '', `Generated: ${now.toISOString()}`, `Smoothing: ${preferences.smoothing}`];
  lines.push(series.frequencies.length ? `Frequency range: ${Math.min(...series.frequencies).toFixed(0)} - ${Math.max(...series.frequencies).toFixed(0)} Hz` : 'Frequency range: n/a');
  lines.push(`Number of points: ${series.frequencies.length}`, '');
  if (spl) lines.push('FREQUENCY RESPONSE SUMMARY', '--------------------------', `Average SPL: ${spl.average.toFixed(2)} dB`, `SPL Range: ${spl.min.toFixed(2)} to ${spl.max.toFixed(2)} dB`, `Variation: ${(spl.max - spl.min).toFixed(2)} dB`, '');
  if (di) lines.push('DIRECTIVITY INDEX SUMMARY', '-------------------------', `Average DI: ${di.average.toFixed(2)} dB`, `DI Range: ${di.min.toFixed(2)} to ${di.max.toFixed(2)} dB`, '');
  if (impedance) lines.push('IMPEDANCE SUMMARY', '-----------------', `Average Real Part Z/(rho*c): ${impedance.average.toFixed(2)}`, '');
  lines.push('DETAILED DATA', '=============', 'Freq(Hz)  SPL(dB)  DI(dB)  Z_Re/(rho*c)  Z_Im/(rho*c)');
  series.frequencies.forEach((frequency, index) => lines.push([frequency, series.spl[index], series.di[index], series.impedanceReal[index], series.impedanceImaginary[index]].map((value) => finite(value)?.toFixed(2) ?? 'n/a').join('  ')));
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

async function postGeometry(context: ExportContext, kind: 'step' | 'stl' | 'profiles', filename: string, profileKind?: 'profiles' | 'slices'): Promise<string> {
  if (!context.design) throw new Error('This export requires a saved design snapshot.');
  const fetcher = context.fetcher ?? fetch;
  const baseName = exportBaseName(context.preferences);
  const query = kind === 'profiles' ? `?kind=${profileKind ?? 'profiles'}` : '';
  const response = await fetcher(`/api/export/${kind}${query}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ design: serializeDesign(context.design), designRevision: context.designRevision ?? 0, baseName }) });
  if (!response.ok) throw await responseError(response);
  const output = filenameFromResponse(response, filename);
  (context.saveBlob ?? downloadBlob)(await response.blob(), output);
  return output;
}

function requireResult(context: ExportContext): ResultPayload {
  if (!context.result) throw new Error('This export requires completed result data.');
  return context.result;
}

async function chartPng(context: ExportContext): Promise<string[]> {
  const result = requireResult(context);
  const fetcher = context.fetcher ?? fetch;
  const response = await fetcher('/api/render-charts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildChartRenderPayload(result, context.preferences)) });
  if (!response.ok) throw await responseError(response);
  const body = await response.json() as { charts?: Record<string, string> };
  const entries = Object.entries(body.charts ?? {});
  if (!entries.length) throw new Error('Chart renderer returned no images.');
  const baseName = exportBaseName(context.preferences);
  return entries.map(([chart, data]) => {
    const encoded = data.includes(',') ? data.slice(data.indexOf(',') + 1) : data;
    const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
    const filename = `${baseName}_${chart}.png`;
    (context.saveBlob ?? downloadBlob)(new Blob([bytes], { type: 'image/png' }), filename);
    return filename;
  });
}

export function buildChartRenderPayload(result: ResultPayload, preferences: Preferences): Record<string, unknown> {
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
    theme: preferences.chartTheme,
  };
}

export async function runExportFormat(format: ExportFormat, context: ExportContext): Promise<string[]> {
  const baseName = exportBaseName(context.preferences);
  const saveText = context.saveText ?? downloadText;
  const now = context.now ?? new Date();
  if (format === 'mwg_config') {
    if (!context.design) throw new Error('This export requires a saved design snapshot.');
    const response = await (context.fetcher ?? fetch)('/api/design/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ design: serializeDesign(context.design), filename: `${baseName}.txt` }) });
    if (!response.ok) throw await responseError(response);
    const body = await response.json() as { text: string; suggestedFilename?: string };
    const filename = body.suggestedFilename ?? `${baseName}.txt`;
    saveText(body.text, filename);
    return [filename];
  }
  if (format === 'step') return [await postGeometry(context, 'step', `${baseName}.step`)];
  if (format === 'stl') return [await postGeometry(context, 'stl', `${baseName}.stl`)];
  if (format === 'fusion_csv') return [await postGeometry(context, 'profiles', `${baseName}_profiles.csv`, 'profiles'), await postGeometry(context, 'profiles', `${baseName}_slices.csv`, 'slices')];
  if (format === 'png') return chartPng(context);
  const result = requireResult(context);
  const builders: Record<Exclude<ExportFormat, 'mwg_config' | 'step' | 'stl' | 'fusion_csv' | 'png'>, [string, string, string]> = {
    csv: [buildFrequencyCsv(result, context.preferences), `${baseName}.csv`, 'text/csv;charset=utf-8'],
    json: [buildFullResultsJson(result, context.preferences, now), `${baseName}.json`, 'application/json;charset=utf-8'],
    txt: [buildSummaryText(result, context.preferences, now), `${baseName}.txt`, 'text/plain;charset=utf-8'],
    polar_csv: [buildPolarCsv(result), `${baseName}_polar.csv`, 'text/csv;charset=utf-8'],
    impedance_csv: [buildImpedanceCsv(result), `${baseName}_impedance.csv`, 'text/csv;charset=utf-8'],
    vacs: [buildVacs(result, now), `${baseName}_spectrum.txt`, 'text/plain;charset=utf-8'],
  };
  const [content, filename, type] = builders[format];
  saveText(content, filename, type);
  return [filename];
}

export async function runExportBundle(context: ExportContext, formats = context.preferences.exportFormats): Promise<ExportBundleResult> {
  const files: string[] = [];
  const failures: ExportFailure[] = [];
  for (const format of formats) {
    try { files.push(...await runExportFormat(format, context)); }
    catch (error) { failures.push({ format, reason: error instanceof Error ? error.message : String(error) }); }
  }
  return { files, failures };
}

export async function downloadMeshArtifact(jobId: string, fetcher: typeof fetch = fetch, saveBlob: (blob: Blob, filename: string) => void = downloadBlob): Promise<string> {
  const response = await fetcher(`/api/mesh-artifact/${encodeURIComponent(jobId)}`);
  if (!response.ok) throw await responseError(response);
  const filename = filenameFromResponse(response, `simulation_mesh_${jobId}.msh`);
  saveBlob(await response.blob(), filename);
  return filename;
}

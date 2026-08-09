import { useSyncExternalStore } from 'react';
import type { JobItem } from '../api/jobsSocket';
import type { SmoothingMode } from '../results/smoothing';

export const CHART_TYPES = [
  { id: 'directivity_map_h', label: 'Directivity Map (H)' },
  { id: 'directivity_map_v', label: 'Directivity Map (V)' },
  { id: 'directivity_map', label: 'Directivity Map (All planes)' },
  { id: 'frequency_response', label: 'Frequency Response (SPL On-Axis)' },
  { id: 'directivity_index', label: 'Directivity Index' },
  { id: 'beam_shape', label: 'Forward Beam Shape' },
  { id: 'beam_map', label: 'Forward Beam Map' },
  { id: 'balloon', label: '3D Balloon' },
  { id: 'impedance', label: 'Acoustic Impedance' },
  { id: 'summary', label: 'Simulation Summary' },
] as const;
export type ChartType = typeof CHART_TYPES[number]['id'];

export const EXPORT_FORMATS = [
  { id: 'mwg_config', label: 'Parameter Config (.txt)' },
  { id: 'step', label: 'Waveguide STEP (solid)' },
  { id: 'png', label: 'Chart Images (PNG)' },
  { id: 'csv', label: 'Frequency Data CSV' },
  { id: 'json', label: 'Full Results JSON' },
  { id: 'txt', label: 'Summary Text Report' },
  { id: 'polar_csv', label: 'Polar Directivity CSV' },
  { id: 'impedance_csv', label: 'Impedance CSV' },
  { id: 'vacs', label: 'ABEC Spectrum (VACS)' },
  { id: 'stl', label: 'Waveguide STL' },
  { id: 'fusion_csv', label: 'Fusion 360 CSV Curves' },
] as const;
export type ExportFormat = typeof EXPORT_FORMATS[number]['id'];
export type MapReference = -3 | -6 | -9 | -12;
export const MAP_REFERENCES: MapReference[] = [-3, -6, -9, -12];
export const RESULT_PANEL_COUNTS = [1, 2, 3, 4, 6] as const;
export type ResultPanelCount = typeof RESULT_PANEL_COUNTS[number];
export const MAX_RESULT_PANELS = 6;
export type JobSort = 'completed_desc' | 'created_desc' | 'rating_desc' | 'name_asc';

export interface Preferences {
  smoothing: SmoothingMode;
  mapReference: MapReference;
  chartTypes: ChartType[];
  chartTheme: string;
  exportFormats: ExportFormat[];
  autoExportOnComplete: boolean;
  autoDownloadMesh: boolean;
  outputName: string;
  counter: number;
  jobVersion: number;
  datePrefix: boolean;
  jobSort: JobSort;
  minRating: number;
}

const STORAGE_KEY = 'waveguide-v2-g3-preferences';
const defaults: Preferences = {
  smoothing: 'none',
  mapReference: -6,
  // Every default panel must populate from a default solve. 3D Balloon and
  // Forward Beam Map both need spherical sampling, which is off by default, so
  // defaulting to them left two of six panels permanently showing their stub.
  chartTypes: ['frequency_response', 'directivity_map_h', 'directivity_map_v', 'directivity_index', 'impedance', 'summary'],
  chartTheme: 'hornlab',
  exportFormats: [],
  autoExportOnComplete: false,
  autoDownloadMesh: false,
  outputName: 'horn',
  counter: 1,
  jobVersion: 1,
  datePrefix: true,
  jobSort: 'completed_desc',
  minRating: 0,
};

const chartIds = new Set(CHART_TYPES.map(({ id }) => id));
const exportIds = new Set(EXPORT_FORMATS.map(({ id }) => id));
const smoothingIds = new Set(['none', '1/1', '1/2', '1/3', '1/6', '1/12', '1/24', '1/48', 'variable', 'psychoacoustic', 'erb']);
const jobSortIds = new Set<JobSort>(['completed_desc', 'created_desc', 'rating_desc', 'name_asc']);

export function normalizeOutputName(value: unknown): string {
  const normalized = String(value ?? '').trim().replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^\.+|\.+$/g, '');
  return normalized || defaults.outputName;
}

export function exportBaseName(preferences: Pick<Preferences, 'outputName' | 'counter'>): string {
  return `${normalizeOutputName(preferences.outputName)}_${Math.max(1, Math.min(999_999, Math.floor(preferences.counter)))}`;
}

export function normalize(raw: Partial<Preferences> = {}): Preferences {
  const charts = Array.isArray(raw.chartTypes)
    ? raw.chartTypes.filter((id): id is ChartType => chartIds.has(id)).slice(0, MAX_RESULT_PANELS)
    : [...defaults.chartTypes];
  const formats = Array.isArray(raw.exportFormats) ? [...new Set(raw.exportFormats.filter((id): id is ExportFormat => exportIds.has(id)))] : [];
  const mapReference = MAP_REFERENCES.includes(Number(raw.mapReference) as MapReference) ? Number(raw.mapReference) as MapReference : defaults.mapReference;
  return {
    ...defaults,
    smoothing: smoothingIds.has(String(raw.smoothing)) ? raw.smoothing as SmoothingMode : defaults.smoothing,
    mapReference,
    chartTypes: charts,
    chartTheme: String(raw.chartTheme || defaults.chartTheme),
    exportFormats: formats,
    autoExportOnComplete: raw.autoExportOnComplete === true,
    autoDownloadMesh: raw.autoDownloadMesh === true,
    outputName: normalizeOutputName(raw.outputName),
    counter: Number.isFinite(Number(raw.counter)) ? Math.max(1, Math.min(999_999, Math.floor(Number(raw.counter)))) : defaults.counter,
    jobVersion: Number.isFinite(Number(raw.jobVersion)) ? Math.max(1, Math.min(999_999, Math.floor(Number(raw.jobVersion)))) : defaults.jobVersion,
    datePrefix: raw.datePrefix === undefined ? defaults.datePrefix : raw.datePrefix === true,
    jobSort: jobSortIds.has(raw.jobSort as JobSort) ? raw.jobSort as JobSort : defaults.jobSort,
    minRating: Number.isFinite(Number(raw.minRating)) ? Math.max(0, Math.min(5, Math.floor(Number(raw.minRating)))) : defaults.minRating,
  };
}

export const STORAGE_VERSION = 5;

function migrateV1ToV2(preferences: Partial<Preferences>): Partial<Preferences> {
  const { chartTypes: _replaced, ...carried } = preferences;
  return carried;
}

function migrateV2ToV3(preferences: Partial<Preferences>): Partial<Preferences> {
  return {
    ...preferences,
    chartTypes: Array.isArray(preferences.chartTypes)
      ? preferences.chartTypes.slice(0, MAX_RESULT_PANELS)
      : undefined,
  };
}

function migrateV3ToV4(preferences: Partial<Preferences>): Partial<Preferences> {
  return preferences;
}

function migrateV4ToV5(preferences: Partial<Preferences>): Partial<Preferences> {
  // New profiles adopt the default, but migration must not overwrite an
  // explicit preference that an existing profile already stored.
  return preferences;
}

/**
 * `260808_horn_v14` — YYMMDD, so a plain A–Z sort of the stored labels is also
 * a chronological one, in the runs list and in every export folder.
 */
export function datePrefixFor(now = new Date()): string {
  return [now.getFullYear() % 100, now.getMonth() + 1, now.getDate()]
    .map((part) => String(part).padStart(2, '0'))
    .join('');
}

export function jobBaseName(
  preferences: Pick<Preferences, 'outputName' | 'jobVersion' | 'datePrefix'>,
  now = new Date(),
): string {
  const prefix = preferences.datePrefix ? `${datePrefixFor(now)}_` : '';
  const version = Math.max(1, Math.min(999_999, Math.floor(preferences.jobVersion)));
  return `${prefix}${normalizeOutputName(preferences.outputName)}_v${String(version).padStart(2, '0')}`;
}

/**
 * Split a stored run label back into the name the user typed and its version.
 *
 * Tolerates both date prefixes this app has written (`260808_` and the earlier
 * `2026-08-08_`) and returns null for anything that is not a versioned name --
 * a hand-edited label, or one of the `osse_1a2b3c4d` fallbacks a run with no
 * label at all displays under.
 */
export function parseJobName(label: string | null | undefined): { name: string; version: number } | null {
  const match = /^(?:\d{6}_|\d{4}-\d{2}-\d{2}_)?(.+)_v(\d{1,6})$/.exec(String(label ?? '').trim());
  if (!match) return null;
  return { name: normalizeOutputName(match[1]), version: Number(match[2]) };
}

/**
 * The naming a run of `label`'s design should get: same name, next free number.
 *
 * Reopening an old config and solving it again should read as another take on
 * that design rather than as an unrelated run, so it inherits the name -- but
 * it must not collide with the run it came from, or with anything solved since,
 * so the version clears every version already used under that name.
 */
export function nextJobNaming(
  label: string | null | undefined,
  existingLabels: readonly (string | null)[] = [],
): { outputName: string; jobVersion: number } | null {
  const parsed = parseJobName(label);
  if (!parsed) return null;
  return { outputName: parsed.name, jobVersion: nextVersionFor(parsed.name, existingLabels) };
}

/** Derive the next run name after successfully opening a standalone config. */
export function nextFileJobNaming(
  filename: string,
  existingLabels: readonly (string | null)[] = [],
): { outputName: string; jobVersion: number } {
  const stem = String(filename).replace(/^.*[\\/]/, '').replace(/\.(cfg|txt|mwg)$/i, '') || 'waveguide';
  const parsed = parseJobName(stem);
  if (parsed) {
    return {
      outputName: parsed.name,
      jobVersion: Math.max(parsed.version + 1, nextVersionFor(parsed.name, existingLabels)),
    };
  }
  const outputName = normalizeOutputName(stem);
  return { outputName, jobVersion: nextVersionFor(outputName, existingLabels) };
}

/** One past the highest version any stored run already uses under `name`. */
export function nextVersionFor(name: string, existingLabels: readonly (string | null)[] = []): number {
  const wanted = normalizeOutputName(name);
  const used = existingLabels
    .map((label) => parseJobName(label))
    .filter((parsed): parsed is { name: string; version: number } => parsed?.name === wanted)
    .map((parsed) => parsed.version);
  return Math.min(999_999, Math.max(0, ...used) + 1);
}

/**
 * Migrations are intentionally sequential. v1→v2 replaced two unusable seeded
 * panels while preserving unrelated settings; v2→v3 makes the chart list's
 * stored length authoritative; v3→v4 adds independent job-version naming;
 * v4→v5 puts the date prefix on by default. Each stored version runs every
 * step from its own onwards -- v3 used to run only its first step, so a v3
 * profile would have skipped v4→v5 entirely.
 */
const MIGRATIONS: Record<number, (preferences: Partial<Preferences>) => Partial<Preferences>> = {
  1: migrateV1ToV2,
  2: migrateV2ToV3,
  3: migrateV3ToV4,
  4: migrateV4ToV5,
};

export function readPreferences(raw: string | null): { value: Preferences; migrated: boolean } {
  try {
    const parsed = JSON.parse(raw ?? '{}') as { version?: number; preferences?: Partial<Preferences> };
    if (parsed.version === STORAGE_VERSION) return { value: normalize(parsed.preferences), migrated: false };
    const from = Number(parsed.version);
    if (Number.isInteger(from) && from >= 1 && from < STORAGE_VERSION) {
      let carried = parsed.preferences ?? {};
      for (let version = from; version < STORAGE_VERSION; version += 1) {
        carried = MIGRATIONS[version](carried);
      }
      return { value: normalize(carried), migrated: true };
    }
    return { value: { ...defaults }, migrated: raw !== null };
  } catch {
    return { value: { ...defaults }, migrated: raw !== null };
  }
}

export function loadPreferences(raw: string | null): Preferences {
  return readPreferences(raw).value;
}

function load(): Preferences {
  if (typeof localStorage === 'undefined') return { ...defaults };
  const { value, migrated } = readPreferences(localStorage.getItem(STORAGE_KEY));
  // Persist a migrated layout straight away, so resetting the panel selection
  // happens once rather than on every reload.
  if (migrated) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: STORAGE_VERSION, preferences: value })); } catch { /* persistence is best effort */ }
  }
  return value;
}

class PreferenceStore {
  private value = load();
  private readonly listeners = new Set<() => void>();
  getSnapshot = (): Preferences => this.value;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  update(patch: Partial<Preferences>): void {
    this.value = normalize({ ...this.value, ...patch });
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: STORAGE_VERSION, preferences: this.value })); } catch { /* persistence is best effort */ }
    this.listeners.forEach((listener) => listener());
  }
  setChartType(index: number, chartType: ChartType): void {
    if (index < 0 || index >= this.value.chartTypes.length) return;
    const chartTypes = [...this.value.chartTypes];
    chartTypes[index] = chartType;
    this.update({ chartTypes });
  }
  setChartCount(count: number): void {
    const nextCount = Math.max(0, Math.min(MAX_RESULT_PANELS, Math.floor(count)));
    const chartTypes = this.value.chartTypes.slice(0, nextCount);
    while (chartTypes.length < nextCount) {
      chartTypes.push(defaults.chartTypes[chartTypes.length] ?? defaults.chartTypes[0]);
    }
    this.update({ chartTypes });
  }
  closeChart(index: number): void {
    if (index < 0 || index >= this.value.chartTypes.length) return;
    this.update({ chartTypes: this.value.chartTypes.filter((_chart, itemIndex) => itemIndex !== index) });
  }
  addChart(): void {
    if (this.value.chartTypes.length >= MAX_RESULT_PANELS) return;
    const index = this.value.chartTypes.length;
    this.update({ chartTypes: [...this.value.chartTypes, defaults.chartTypes[index] ?? defaults.chartTypes[0]] });
  }
  toggleFormat(format: ExportFormat): void {
    const exportFormats = this.value.exportFormats.includes(format)
      ? this.value.exportFormats.filter((item) => item !== format)
      : [...this.value.exportFormats, format];
    this.update({ exportFormats });
  }
  resetForTests(): void { this.value = { ...defaults, chartTypes: [...defaults.chartTypes], exportFormats: [] }; }
}

export const preferencesStore = new PreferenceStore();
export function usePreferences(): Preferences {
  return useSyncExternalStore(preferencesStore.subscribe, preferencesStore.getSnapshot, preferencesStore.getSnapshot);
}

export type RunDisplayVariant = 'full' | 'short';

/** The one user-facing identity for a run, shared by lists, charts, and search. */
export function runDisplayName(job: Pick<JobItem, 'id' | 'run_number' | 'label'>, variant: RunDisplayVariant = 'full'): string {
  const title = job.label || `osse-${job.id.slice(0, 6)}`;
  return variant === 'short' ? title : `#${job.run_number} · ${title}`;
}

const naturalNameCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

export function applyJobPreferences(jobs: JobItem[], sort: JobSort, minimumRating: number): JobItem[] {
  const filtered = jobs.filter((job) => (job.rating ?? 0) >= minimumRating);
  return [...filtered].sort((a, b) => {
    let order = 0;
    if (sort === 'rating_desc') order = (b.rating ?? 0) - (a.rating ?? 0) || Date.parse(b.created_at) - Date.parse(a.created_at);
    else if (sort === 'name_asc') order = naturalNameCollator.compare(runDisplayName(a, 'short'), runDisplayName(b, 'short'));
    else if (sort === 'created_desc') order = Date.parse(b.created_at) - Date.parse(a.created_at);
    else order = Date.parse(b.completed_at ?? b.created_at) - Date.parse(a.completed_at ?? a.created_at);
    return order || a.run_number - b.run_number;
  });
}

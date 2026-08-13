import { useSyncExternalStore } from 'react';
import type { JobItem } from '../api/jobsSocket';
import { isSubmittedDesignProjection } from '../jobs/submittedProjection';
import { normalizeRunName, UNBOUND_RUN_NAME_SOURCE, type RunNameDateFormat, type RunNameDatePosition, type RunNameNumberFormat, type RunNameNumberPosition, type RunNameSourceProjection } from '../jobs/runNaming';
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

/**
 * Export theme that tracks the interface instead of naming a theme.
 *
 * The stored default used to be a fixed theme name, which meant a figure
 * exported from a Vellum window came back on a dark ground -- and, since the
 * live charts follow the interface, that the chart on screen and the chart in
 * the file were never the same picture. This resolves at export time, and the
 * two application themes are the ones the interface is actually built from.
 */
export const MATCH_INTERFACE_THEME = 'auto';

export function resolveChartTheme(chartTheme: string): string {
  if (chartTheme !== MATCH_INTERFACE_THEME) return chartTheme;
  const light = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light';
  return light ? 'vellum' : 'console';
}

export const EXPORT_FORMATS = [
  { id: 'mwg_config', label: 'Parameter Config (.cfg)' },
  { id: 'step', label: 'Waveguide STEP (solid)' },
  { id: 'png', label: 'Chart Images (PNG)' },
  { id: 'csv', label: 'Frequency Data CSV' },
  { id: 'json', label: 'Full Results JSON' },
  { id: 'txt', label: 'Summary Text Report' },
  { id: 'polar_csv', label: 'Polar Directivity CSV' },
  { id: 'impedance_csv', label: 'Impedance CSV' },
  { id: 'zma', label: 'VituixCAD Impedance (ZMA)' },
  { id: 'vxp', label: 'VituixCAD Project (VXP)' },
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
export type CadApplication = 'fusion360' | 'onshape';

export interface Preferences {
  cadApplication: CadApplication;
  smoothing: SmoothingMode;
  mapReference: MapReference;
  chartTypes: ChartType[];
  chartTheme: string;
  exportFormats: ExportFormat[];
  autoExportFormats: ExportFormat[];
  autoExportOnComplete: boolean;
  autoDownloadMesh: boolean;
  outputName: string;
  nameSourceProjection: RunNameSourceProjection;
  runNameDatePosition: RunNameDatePosition;
  runNameDateFormat: RunNameDateFormat;
  runNameNumberPosition: RunNameNumberPosition;
  runNameNumberFormat: RunNameNumberFormat;
  counter: number;
  jobSort: JobSort;
  minRating: number;
}

const STORAGE_KEY = 'waveguide-v2-g3-preferences';
const defaults: Preferences = {
  cadApplication: 'fusion360',
  smoothing: 'none',
  mapReference: -6,
  // Every default panel must populate from a default solve. 3D Balloon and
  // Forward Beam Map both need spherical sampling, which is off by default, so
  // defaulting to them left two of six panels permanently showing their stub.
  chartTypes: ['frequency_response', 'directivity_map_h', 'directivity_map_v', 'directivity_index', 'impedance', 'summary'],
  chartTheme: MATCH_INTERFACE_THEME,
  exportFormats: ['csv', 'png'],
  autoExportFormats: [],
  autoExportOnComplete: false,
  autoDownloadMesh: false,
  outputName: 'horn',
  nameSourceProjection: null,
  runNameDatePosition: 'off',
  runNameDateFormat: 'yymmdd',
  runNameNumberPosition: 'suffix',
  runNameNumberFormat: 'natural',
  counter: 1,
  jobSort: 'completed_desc',
  minRating: 0,
};

const chartIds = new Set(CHART_TYPES.map(({ id }) => id));
const exportIds = new Set(EXPORT_FORMATS.map(({ id }) => id));
const smoothingIds = new Set(['none', '1/1', '1/2', '1/3', '1/6', '1/12', '1/24', '1/48', 'variable', 'psychoacoustic', 'erb']);
const jobSortIds = new Set<JobSort>(['completed_desc', 'created_desc', 'rating_desc', 'name_asc']);
const cadApplicationIds = new Set<CadApplication>(['fusion360', 'onshape']);
const runNameDatePositions = new Set<RunNameDatePosition>(['off', 'prefix', 'suffix']);
const runNameDateFormats = new Set<RunNameDateFormat>(['yymmdd', 'yyyy-mm-dd']);
const runNameNumberPositions = new Set<RunNameNumberPosition>(['off', 'suffix']);
const runNameNumberFormats = new Set<RunNameNumberFormat>(['natural', '2-digit', '3-digit']);

export function normalize(raw: Partial<Preferences> = {}): Preferences {
  const charts = Array.isArray(raw.chartTypes)
    ? raw.chartTypes.filter((id): id is ChartType => chartIds.has(id)).slice(0, MAX_RESULT_PANELS)
    : [...defaults.chartTypes];
  const formats = Array.isArray(raw.exportFormats)
    ? [...new Set(raw.exportFormats.filter((id): id is ExportFormat => exportIds.has(id)))]
    : [...defaults.exportFormats];
  const autoFormats = Array.isArray(raw.autoExportFormats)
    ? [...new Set(raw.autoExportFormats.filter((id): id is ExportFormat => exportIds.has(id)))]
    : [...defaults.autoExportFormats];
  const mapReference = MAP_REFERENCES.includes(Number(raw.mapReference) as MapReference) ? Number(raw.mapReference) as MapReference : defaults.mapReference;
  return {
    ...defaults,
    cadApplication: cadApplicationIds.has(raw.cadApplication as CadApplication)
      ? raw.cadApplication as CadApplication
      : defaults.cadApplication,
    smoothing: smoothingIds.has(String(raw.smoothing)) ? raw.smoothing as SmoothingMode : defaults.smoothing,
    mapReference,
    chartTypes: charts,
    chartTheme: String(raw.chartTheme || defaults.chartTheme),
    exportFormats: formats,
    autoExportFormats: autoFormats,
    autoExportOnComplete: raw.autoExportOnComplete === true,
    autoDownloadMesh: raw.autoDownloadMesh === true,
    outputName: normalizeRunName(raw.outputName),
    nameSourceProjection: raw.nameSourceProjection === UNBOUND_RUN_NAME_SOURCE
      ? UNBOUND_RUN_NAME_SOURCE
      : isSubmittedDesignProjection(raw.nameSourceProjection)
        ? structuredClone(raw.nameSourceProjection)
        : null,
    runNameDatePosition: runNameDatePositions.has(raw.runNameDatePosition as RunNameDatePosition)
      ? raw.runNameDatePosition as RunNameDatePosition
      : defaults.runNameDatePosition,
    runNameDateFormat: runNameDateFormats.has(raw.runNameDateFormat as RunNameDateFormat)
      ? raw.runNameDateFormat as RunNameDateFormat
      : defaults.runNameDateFormat,
    runNameNumberPosition: runNameNumberPositions.has(raw.runNameNumberPosition as RunNameNumberPosition)
      ? raw.runNameNumberPosition as RunNameNumberPosition
      : defaults.runNameNumberPosition,
    runNameNumberFormat: runNameNumberFormats.has(raw.runNameNumberFormat as RunNameNumberFormat)
      ? raw.runNameNumberFormat as RunNameNumberFormat
      : defaults.runNameNumberFormat,
    counter: Number.isFinite(Number(raw.counter)) ? Math.max(1, Math.min(999_999, Math.floor(Number(raw.counter)))) : defaults.counter,
    jobSort: jobSortIds.has(raw.jobSort as JobSort) ? raw.jobSort as JobSort : defaults.jobSort,
    minRating: Number.isFinite(Number(raw.minRating)) ? Math.max(0, Math.min(5, Math.floor(Number(raw.minRating)))) : defaults.minRating,
  };
}

export const STORAGE_VERSION = 11;

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

function migrateV5ToV6(preferences: Partial<Preferences>): Partial<Preferences> {
  // Inspect the raw object before normalize runs: an own [] means the user
  // deliberately selected no formats, while an absent key should adopt the
  // new manual default. Spreading preserves that distinction.
  if (!Object.prototype.hasOwnProperty.call(preferences, 'exportFormats')) return preferences;
  return {
    ...preferences,
    autoExportFormats: preferences.autoExportOnComplete === true
      ? preferences.exportFormats
      : [],
  };
}

/**
 * The export theme used to default to a fixed theme name, so a stored
 * 'hornlab' cannot be told apart from a deliberate choice by its value alone
 * -- except that it was the default, and the overwhelming majority of profiles
 * never touched the setting. Move exactly that value onto the interface-
 * following default and leave every other choice alone.
 */
function migrateV8ToV9(preferences: Partial<Preferences>): Partial<Preferences> {
  if (preferences.chartTheme !== 'hornlab') return preferences;
  return { ...preferences, chartTheme: MATCH_INTERFACE_THEME };
}

/**
 * Migrations are intentionally sequential. v1→v2 replaced two unusable seeded
 * panels while preserving unrelated settings; v2→v3 makes the chart list's
 * stored length authoritative; v3→v4 adds independent job-version naming;
 * v4→v5 puts the date prefix on by default; v5→v6 separates manual and
 * automatic export selections; v6→v7 adds design-tracking names; v7→v8 adds
 * opt-in date decoration outside those names; v8→v9 moves the untouched
 * export-theme default onto the interface; v9→v10 exposes the existing
 * design-change number as a configurable suffix; v10→v11 adds the CAD
 * application choice. Each stored version runs every
 * step from its own onwards -- v3 used to run only its first step, so a v3
 * profile would have skipped v4→v5 entirely.
 */
const MIGRATIONS: Record<number, (preferences: Partial<Preferences>) => Partial<Preferences>> = {
  1: migrateV1ToV2,
  2: migrateV2ToV3,
  3: migrateV3ToV4,
  4: migrateV4ToV5,
  5: migrateV5ToV6,
  6: (preferences) => preferences,
  7: (preferences) => preferences,
  8: migrateV8ToV9,
  9: (preferences) => preferences,
  10: (preferences) => preferences,
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
  toggleAutoExportFormat(format: ExportFormat): void {
    const autoExportFormats = this.value.autoExportFormats.includes(format)
      ? this.value.autoExportFormats.filter((item) => item !== format)
      : [...this.value.autoExportFormats, format];
    this.update({ autoExportFormats });
  }
  resetForTests(): void {
    this.value = {
      ...defaults,
      chartTypes: [...defaults.chartTypes],
      exportFormats: [...defaults.exportFormats],
      autoExportFormats: [...defaults.autoExportFormats],
    };
  }
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
  const filtered = jobs.filter((job) => job.status === 'queued' || job.status === 'running' || (job.rating ?? 0) >= minimumRating);
  return [...filtered].sort((a, b) => {
    let order = 0;
    if (sort === 'rating_desc') order = (b.rating ?? 0) - (a.rating ?? 0) || Date.parse(b.created_at) - Date.parse(a.created_at);
    else if (sort === 'name_asc') order = naturalNameCollator.compare(runDisplayName(a, 'short'), runDisplayName(b, 'short'));
    else if (sort === 'created_desc') order = Date.parse(b.created_at) - Date.parse(a.created_at);
    else order = Date.parse(b.completed_at ?? b.created_at) - Date.parse(a.completed_at ?? a.created_at);
    return order || (sort === 'name_asc' ? a.run_number - b.run_number : b.run_number - a.run_number);
  });
}

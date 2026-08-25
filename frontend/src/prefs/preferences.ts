import { useSyncExternalStore } from 'react';
import type { JobItem } from '../api/jobsSocket';
import type { RunNameDateFormat, RunNameDatePosition, RunNameNumberFormat, RunNameNumberPosition } from '../jobs/runNaming';
import type { SmoothingMode } from '../results/smoothing';
import { durableSettings } from '../stores/durableSettings';

export const CHART_TYPES = [
  { id: 'directivity_map_h', label: 'Directivity Map (H)' },
  { id: 'directivity_map_v', label: 'Directivity Map (V)' },
  { id: 'directivity_map_d', label: 'Directivity Map (Diagonal)' },
  { id: 'directivity_map', label: 'Directivity Map (All planes)' },
  { id: 'frequency_response', label: 'Frequency Response (SPL On-Axis)' },
  { id: 'directivity_index', label: 'Directivity Index' },
  { id: 'power_response', label: 'Power Response (Spatial Average)' },
  { id: 'beam_shape', label: 'Forward Beam Shape' },
  { id: 'beam_fit', label: 'Beam Shape Fit (aspect / exponent)' },
  { id: 'beam_map', label: 'Forward Beam Map' },
  { id: 'balloon', label: '3D Balloon' },
  { id: 'polar_response', label: 'Polar Response' },
  { id: 'phase_response', label: 'On-Axis Phase' },
  { id: 'group_delay', label: 'Group Delay' },
  { id: 'impedance', label: 'Impedance' },
  { id: 'radiation_impedance', label: 'Radiation Matrix Load' },
  { id: 'drive_power', label: 'Power & Current Draw' },
  { id: 'excursion', label: 'Cone Excursion' },
  { id: 'max_output', label: 'Maximum Output' },
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
  { id: 'on_axis_frd', label: 'On-axis Response (FRD)' },
  { id: 'polar_frd', label: 'Polar Set (VituixCAD FRD)' },
  { id: 'csv', label: 'Frequency Data CSV' },
  { id: 'json', label: 'Full Results JSON' },
  { id: 'pressure_basis', label: 'Complex Pressure Basis (NPZ)' },
  { id: 'derived_acoustics', label: 'Derived Acoustics (CSV + JSON)' },
  { id: 'html_report', label: 'Static Run Report (HTML)' },
  { id: 'txt', label: 'Summary Text Report' },
  { id: 'polar_csv', label: 'Polar Directivity CSV' },
  { id: 'impedance_csv', label: 'Impedance CSV' },
  { id: 'radiation_impedance_csv', label: 'Radiation Matrix CSV' },
  { id: 'radiation_impedance_npz', label: 'Radiation Matrix (NPZ)' },
  { id: 'zma', label: 'VituixCAD Impedance (ZMA)' },
  { id: 'vxp', label: 'VituixCAD Project (VXP)' },
  { id: 'vacs', label: 'ABEC Spectrum (VACS)' },
  { id: 'stl', label: 'Waveguide STL' },
  { id: 'fusion_csv', label: 'Fusion 360 CSV Curves' },
] as const;
export type ExportFormat = typeof EXPORT_FORMATS[number]['id'];
export type MapReference = -3 | -6 | -9 | -12;
/** Re/Im is conventional for horn throat impedance; |Z| and phase for a driver. */
export type ImpedanceDisplay = 'real_imaginary' | 'magnitude_phase';
export const IMPEDANCE_DISPLAYS: Array<[ImpedanceDisplay, string]> = [
  ['real_imaginary', 'Real / Imaginary'],
  ['magnitude_phase', 'Magnitude / Phase'],
];
/**
 * Group delay read as a time, or as a count of periods of the frequency it
 * occurs at: `cycles(f) = tau(f) * f`, tau in seconds.
 *
 * Milliseconds is the default because it is the number the FRD export and the
 * report already state, and because a delay is a time. Cycles is the
 * frequency-proportional reading of exactly the same curve -- 0.3 ms is a third
 * of a period at 1 kHz and three periods at 10 kHz -- which is how a group
 * delay is judged against audibility rather than against a stopwatch. It is a
 * presentation of one tau, never a second estimate of it.
 */
export type GroupDelayUnit = 'ms' | 'cycles';
export const GROUP_DELAY_UNITS: Array<[GroupDelayUnit, string]> = [
  ['ms', 'Milliseconds'],
  ['cycles', 'Cycles (tau x f)'],
];
/** Planes a polar plot can be cut in. */
export const POLAR_PLANES = ['horizontal', 'vertical'] as const;
export type PolarPlane = typeof POLAR_PLANES[number];
export const MAP_REFERENCES: MapReference[] = [-3, -6, -9, -12];
export const RESULT_PANEL_COUNTS = [1, 2, 3, 4, 6] as const;
export type ResultPanelCount = typeof RESULT_PANEL_COUNTS[number];
export const MAX_RESULT_PANELS = 6;
export type JobSort = 'completed_desc' | 'created_desc' | 'rating_desc' | 'name_asc';
export type CadApplication = 'fusion360' | 'onshape';

/**
 * How a CAD application is named in prose.
 *
 * The workspace mode itself is always "CAD Link" -- it is one workflow, and
 * which application sits on the far end of it is a preference, not a different
 * mode. Name the vendor only where it tells the user which application WG is
 * about to talk to; naming it in the chrome is how the mode came to be labelled
 * "Fusion CAD" for Onshape users.
 */
export function cadApplicationName(application: CadApplication, form: 'short' | 'full' = 'short'): string {
  if (application === 'onshape') return 'Onshape';
  return form === 'full' ? 'Autodesk Fusion 360' : 'Fusion 360';
}

export interface Preferences {
  cadApplication: CadApplication;
  smoothing: SmoothingMode;
  mapReference: MapReference;
  /** Angular spacing of the horizontal graticule on directivity maps. */
  directivityGuideInterval: number;
  chartTypes: ChartType[];
  chartTheme: string;
  /**
   * Draw the on-axis phase beside the SPL trace.
   *
   * On by default because the exported PNG has always drawn it: with this off
   * the chart on screen and the chart in the file are not the same picture.
   */
  splPhase: boolean;
  /**
   * Draw the members beneath the combined sum on the SPL chart.
   *
   * On by default: the sum of an LR4 crossover is read against the branches
   * that make it, and without them the one thing the Combined view exists to
   * show — where the drivers hand over — is invisible.
   */
  showMembersUnderCombined: boolean;
  /**
   * Draw the reverse null under the combined sum: the same sum with one
   * member's polarity flipped.
   *
   * Off by default. It is the bench check on a crossover rather than part of
   * the response, and a fourth trace under the sum and its branches is noise
   * for everyone who is not currently tuning one.
   */
  showReverseNull: boolean;
  impedanceDisplay: ImpedanceDisplay;
  /**
   * The unit the Group Delay chart is read in. The underlying curve is the
   * same excess delay either way; only the axis it is projected onto changes.
   */
  groupDelayUnit: GroupDelayUnit;
  exportFormats: ExportFormat[];
  autoExportFormats: ExportFormat[];
  autoExportOnComplete: boolean;
  /**
   * Write a run record and its result curves to the workspace when a solve
   * finishes. On by default: the job database prunes results after 30 days, so
   * without this a solve leaves nothing behind that outlives that.
   */
  archiveRunsOnComplete: boolean;
  autoDownloadMesh: boolean;
  runNameDatePosition: RunNameDatePosition;
  runNameDateFormat: RunNameDateFormat;
  runNameNumberPosition: RunNameNumberPosition;
  runNameNumberFormat: RunNameNumberFormat;
  /**
   * The design name the run counter belongs to, and the number the next run
   * of it takes. The name itself is the document's, not a preference: these
   * two only decide the digits appended to it.
   */
  runSequenceName: string;
  runSequenceNext: number;
  jobSort: JobSort;
  minRating: number;
}

/**
 * The legal range of every numeric preference, next to the defaults it bounds.
 *
 * These were spelled inline in `normalize` as bare numbers. Stating them here
 * is not a new constraint -- the values are exactly the ones that were already
 * enforced -- but it makes the range part of the preference's definition
 * rather than an implementation detail of one function.
 */
/** Degrees between graticule lines on a directivity map. */
/** 0 turns the graticule off; the map is read by hovering for values. */
const GUIDE_INTERVAL_RANGE_DEG = [0, 180] as const;
/** The run-number counter; six digits is what the naming formats can render. */
const RUN_SEQUENCE_RANGE = [1, 999_999] as const;
/** Stars, matching the rating control in the run list. */
const MIN_RATING_RANGE = [0, 5] as const;

function clampInteger(value: unknown, [minimum, maximum]: readonly [number, number], fallback: number): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(minimum, Math.min(maximum, Math.floor(numeric))) : fallback;
}

const defaults: Preferences = {
  cadApplication: 'fusion360',
  smoothing: 'none',
  mapReference: -6,
  directivityGuideInterval: 0,
  // Every default panel must populate from a default solve. 3D Balloon and
  // Forward Beam Map both need spherical sampling, which is off by default, so
  // defaulting to them left two of six panels permanently showing their stub.
  chartTypes: ['frequency_response', 'directivity_map_h', 'directivity_map_v', 'directivity_index', 'impedance', 'summary'],
  chartTheme: MATCH_INTERFACE_THEME,
  splPhase: true,
  showMembersUnderCombined: true,
  showReverseNull: false,
  impedanceDisplay: 'real_imaginary',
  groupDelayUnit: 'ms',
  exportFormats: ['csv', 'png'],
  autoExportFormats: [],
  autoExportOnComplete: false,
  archiveRunsOnComplete: true,
  autoDownloadMesh: false,
  runNameDatePosition: 'off',
  runNameDateFormat: 'yymmdd',
  runNameNumberPosition: 'suffix',
  runNameNumberFormat: 'natural',
  runSequenceName: '',
  runSequenceNext: 1,
  jobSort: 'completed_desc',
  minRating: 0,
};

const chartIds = new Set(CHART_TYPES.map(({ id }) => id));
const exportIds = new Set(EXPORT_FORMATS.map(({ id }) => id));
const smoothingIds = new Set(['none', '1/1', '1/2', '1/3', '1/6', '1/12', '1/24', '1/48', 'variable', 'psychoacoustic', 'erb']);
const impedanceDisplayIds = new Set<ImpedanceDisplay>(['real_imaginary', 'magnitude_phase']);
const groupDelayUnitIds = new Set<GroupDelayUnit>(['ms', 'cycles']);
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
  const requestedGuideInterval = Number(raw.directivityGuideInterval);
  const directivityGuideInterval = Number.isFinite(requestedGuideInterval)
    ? Math.max(GUIDE_INTERVAL_RANGE_DEG[0], Math.min(GUIDE_INTERVAL_RANGE_DEG[1], requestedGuideInterval))
    : defaults.directivityGuideInterval;
  return {
    ...defaults,
    cadApplication: cadApplicationIds.has(raw.cadApplication as CadApplication)
      ? raw.cadApplication as CadApplication
      : defaults.cadApplication,
    smoothing: smoothingIds.has(String(raw.smoothing)) ? raw.smoothing as SmoothingMode : defaults.smoothing,
    mapReference,
    directivityGuideInterval,
    chartTypes: charts,
    // The theme list is open -- the backend supplies it and a profile may hold
    // one this build has never heard of -- so only the type is checked. It used
    // to be coerced with `String`, which turned a stored object into the
    // literal "[object Object]" and asked the exporter to render in it.
    chartTheme: typeof raw.chartTheme === 'string' && raw.chartTheme ? raw.chartTheme : defaults.chartTheme,
    splPhase: raw.splPhase !== false,
    showMembersUnderCombined: raw.showMembersUnderCombined !== false,
    showReverseNull: raw.showReverseNull === true,
    impedanceDisplay: impedanceDisplayIds.has(raw.impedanceDisplay as ImpedanceDisplay)
      ? raw.impedanceDisplay as ImpedanceDisplay
      : defaults.impedanceDisplay,
    groupDelayUnit: groupDelayUnitIds.has(raw.groupDelayUnit as GroupDelayUnit)
      ? raw.groupDelayUnit as GroupDelayUnit
      : defaults.groupDelayUnit,
    exportFormats: formats,
    autoExportFormats: autoFormats,
    autoExportOnComplete: raw.autoExportOnComplete === true,
    archiveRunsOnComplete: raw.archiveRunsOnComplete !== false,
    autoDownloadMesh: raw.autoDownloadMesh === true,
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
    runSequenceName: typeof raw.runSequenceName === 'string' ? raw.runSequenceName.trim() : defaults.runSequenceName,
    runSequenceNext: clampInteger(raw.runSequenceNext, RUN_SEQUENCE_RANGE, defaults.runSequenceNext),
    jobSort: jobSortIds.has(raw.jobSort as JobSort) ? raw.jobSort as JobSort : defaults.jobSort,
    minRating: clampInteger(raw.minRating, MIN_RATING_RANGE, defaults.minRating),
  };
}

export const STORAGE_VERSION = 14;

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
 * The run name is now the document's design name, so the preference that held
 * it -- and the submitted-design projection that decided when to bump its
 * trailing digits -- are dropped. The stored name is not carried onto any
 * document: it was global, so it belonged to whichever design was open last
 * and claiming it for the next one would reintroduce exactly the drift this
 * replaces. `counter` went with them; nothing had read it since the server
 * took over run numbers.
 */
function migrateV12ToV13(preferences: Partial<Preferences>): Partial<Preferences> {
  const carried = { ...preferences } as Record<string, unknown>;
  delete carried.outputName;
  delete carried.nameSourceProjection;
  delete carried.counter;
  return carried as Partial<Preferences>;
}

/**
 * Retire the angular graticule that shipped on.
 *
 * The lines were drawn every 10 degrees straight across the on-axis band the
 * map is read in, duplicating a fainter set the PNG renderer already draws.
 * 10 cannot be told apart from a deliberate choice by its value alone -- except
 * that it was the shipped default, and 0 was not even accepted until now, so a
 * profile holding 10 is one that never had the option to turn them off. Move
 * exactly that value and leave every other interval alone.
 */
function migrateV13ToV14(preferences: Partial<Preferences>): Partial<Preferences> {
  if (preferences.directivityGuideInterval !== 10) return preferences;
  return { ...preferences, directivityGuideInterval: 0 };
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
 * application choice; v11→v12 persists the user-definable directivity
 * guide interval; v12->v13 retires the standalone run name for the document's
 * design name; v13->v14 turns the untouched angular graticule off. Each stored version runs every
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
  11: (preferences) => preferences,
  12: migrateV12ToV13,
  13: migrateV13ToV14,
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

function serialize(value: Preferences): string {
  return JSON.stringify({ version: STORAGE_VERSION, preferences: value });
}

function load(): Preferences {
  const { value, migrated } = readPreferences(durableSettings.get('preferences'));
  // Persist a migrated layout straight away, so resetting the panel selection
  // happens once rather than on every reload.
  if (migrated) durableSettings.set('preferences', serialize(value));
  return value;
}

class PreferenceStore {
  private value = load();
  private readonly listeners = new Set<() => void>();
  getSnapshot = (): Preferences => this.value;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  update(patch: Partial<Preferences>): void {
    this.value = normalize({ ...this.value, ...patch });
    durableSettings.set('preferences', serialize(this.value));
    this.listeners.forEach((listener) => listener());
  }
  /**
   * Take the durable copy the server just supplied.
   *
   * This never writes back: the value already is what the server holds, and
   * echoing it would race a change the user made while the request was out.
   */
  adoptDurable(raw: string | null): void {
    this.value = readPreferences(raw).value;
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
durableSettings.subscribe('preferences', (raw) => preferencesStore.adoptDurable(raw));
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

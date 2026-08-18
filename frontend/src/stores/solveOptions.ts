import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { DEFAULT_ATH_POLAR_UI, athPolarOverrides } from './athPolars';
import { durableSettings } from './durableSettings';
import { MAX_FREQUENCY_POINTS, parseFrequencyList, type FrequencyListParse } from './frequencyList';
import { wgSolveOverrides } from './wgSolveBlock';

export type MeshValidationMode = 'warn' | 'strict' | 'off';
export type FrequencySpacing = 'log' | 'linear';
export type PolarAxis = 'horizontal' | 'vertical' | 'diagonal';
export type ObservationOrigin = 'mouth' | 'throat';
export type SymmetryMode = 'auto' | 'full' | 'half_xz' | 'half_yz' | 'quarter';
export type FrequencyMode = 'range' | 'list';

export { MAX_FREQUENCY_POINTS, parseFrequencyList };
export type { FrequencyListParse };

/** Mirrors the polar_config request contract introduced by remediation G1. */
export interface PolarConfig {
  angle_range: [number, number, number];
  angle_step: number;
  distance: number;
  norm_angle: number;
  inclination: number;
  enabled_axes: PolarAxis[];
  observation_origin: ObservationOrigin;
  spherical_sampling: boolean;
  field_plane: boolean;
}

export interface SolveOptions {
  engine: string;
  symmetry: SymmetryMode;
  mesh_validation_mode: MeshValidationMode;
  verbose: boolean;
  frequency_spacing: FrequencySpacing;
  /** Present only in list mode; the server then ignores spacing and solves these verbatim. */
  frequencies_hz?: number[];
  polar_config: PolarConfig;
}

export interface PolarUiState {
  angleStart: number;
  angleEnd: number;
  angleStep: number;
  distance: number;
  normAngle: number;
  diagonalAngle: number;
  enabledAxes: PolarAxis[];
  observationOrigin: ObservationOrigin;
  sphericalSampling: boolean;
  fieldPlane: boolean;
}

export const defaultPolarUi: PolarUiState = structuredClone(DEFAULT_ATH_POLAR_UI);

export function polarConfigFromUi(ui: PolarUiState): PolarConfig {
  const numeric = [ui.angleStart, ui.angleEnd, ui.angleStep, ui.distance, ui.normAngle, ui.diagonalAngle];
  if (!numeric.every(Number.isFinite)) throw new Error('Directivity settings must be finite numbers.');
  if (ui.angleEnd <= ui.angleStart) throw new Error('Directivity sweep end must be greater than its start.');
  if (ui.angleStep <= 0) throw new Error('Directivity angular step must be greater than 0 degrees.');
  if (ui.distance < 0.1) throw new Error('Directivity measurement distance must be at least 0.1 m.');
  if (ui.enabledAxes.length === 0) throw new Error('Select at least one directivity plane.');
  const span = ui.angleEnd - ui.angleStart;
  const intervals = span / ui.angleStep;
  const nearestIntervals = Math.round(intervals);
  // Decimal inputs such as 0.3 / 0.1 can land one ULP below an integer. Treat
  // only that floating-point fringe as divisible; genuinely non-divisible
  // spans still floor so their resolved step remains observable in metadata.
  const resolvedIntervals = Math.abs(intervals - nearestIntervals) <= Math.max(1e-9, Math.abs(intervals) * 1e-12)
    ? nearestIntervals
    : Math.floor(intervals);
  const count = Math.max(2, resolvedIntervals + 1);
  if (count > 721) throw new Error(`Directivity sweep supports at most 721 angle samples (got ${count}).`);
  return {
    angle_range: [ui.angleStart, ui.angleEnd, count],
    angle_step: ui.angleStep,
    distance: ui.distance,
    norm_angle: ui.normAngle,
    inclination: ui.diagonalAngle,
    enabled_axes: [...ui.enabledAxes],
    observation_origin: ui.observationOrigin,
    spherical_sampling: ui.sphericalSampling,
    field_plane: ui.fieldPlane !== false,
  };
}

/**
 * Read a recorded solve's directivity settings back into UI form.
 *
 * The inverse of `polarConfigFromUi`, used to show a finished run the
 * measurement it was actually made with. Returns `null` for a run whose
 * options are missing or unreadable, so nothing is ever presented as a run's
 * settings unless the run really recorded them.
 */
export function polarUiFromConfig(config: unknown): PolarUiState | null {
  if (config === null || typeof config !== 'object' || Array.isArray(config)) return null;
  const source = config as Record<string, unknown>;
  const range = source.angle_range;
  if (!Array.isArray(range) || range.length !== 3) return null;
  const [start, end, count] = range.map(Number);
  const distance = Number(source.distance);
  if (![start, end, count, distance].every(Number.isFinite) || count < 2 || end <= start) return null;
  const normAngle = Number(source.norm_angle);
  const inclination = Number(source.inclination);
  const axes = Array.isArray(source.enabled_axes)
    ? source.enabled_axes.filter((axis): axis is PolarAxis => (
      axis === 'horizontal' || axis === 'vertical' || axis === 'diagonal'
    ))
    : [];
  return {
    angleStart: start,
    angleEnd: end,
    // A finished run is described by the grid it resolved to, so the step comes
    // from the sample count rather than from a requested value the solver may
    // have adjusted on the way in.
    angleStep: (end - start) / (count - 1),
    distance,
    normAngle: Number.isFinite(normAngle) ? normAngle : defaultPolarUi.normAngle,
    diagonalAngle: Number.isFinite(inclination) ? inclination : defaultPolarUi.diagonalAngle,
    enabledAxes: axes.length ? axes : [...defaultPolarUi.enabledAxes],
    observationOrigin: source.observation_origin === 'throat' ? 'throat' : 'mouth',
    sphericalSampling: source.spherical_sampling === true,
    fieldPlane: source.field_plane !== false,
  };
}

interface SolveOptionsStore {
  engine: string;
  symmetry: SymmetryMode;
  meshValidationMode: MeshValidationMode;
  verbose: boolean;
  frequencySpacing: FrequencySpacing;
  frequencyMode: FrequencyMode;
  frequencyListText: string;
  polar: PolarUiState;
  setEngine: (engine: string) => void;
  setSymmetry: (symmetry: SymmetryMode) => void;
  setMeshValidationMode: (mode: MeshValidationMode) => void;
  setVerbose: (verbose: boolean) => void;
  setFrequencySpacing: (spacing: FrequencySpacing) => void;
  setFrequencyMode: (mode: FrequencyMode) => void;
  setFrequencyListText: (text: string) => void;
  frequencyListParse: () => FrequencyListParse;
  updatePolar: (update: Partial<PolarUiState>) => void;
  toggleAxis: (axis: PolarAxis) => void;
  options: () => SolveOptions;
}

export const useSolveOptionsStore = create<SolveOptionsStore>()(persist((set, get) => ({
  engine: 'auto',
  symmetry: 'auto',
  meshValidationMode: 'warn',
  verbose: false,
  frequencySpacing: 'log',
  frequencyMode: 'range',
  frequencyListText: '',
  polar: structuredClone(defaultPolarUi),
  setEngine: (engine) => set({ engine }),
  setSymmetry: (symmetry) => set({ symmetry }),
  setMeshValidationMode: (meshValidationMode) => set({ meshValidationMode }),
  setVerbose: (verbose) => set({ verbose }),
  setFrequencySpacing: (frequencySpacing) => set({ frequencySpacing }),
  setFrequencyMode: (frequencyMode) => set({ frequencyMode }),
  setFrequencyListText: (frequencyListText) => set({ frequencyListText }),
  frequencyListParse: () => parseFrequencyList(get().frequencyListText),
  updatePolar: (update) => set((state) => ({ polar: { ...state.polar, ...update } })),
  toggleAxis: (axis) => set((state) => {
    const enabled = state.polar.enabledAxes.includes(axis);
    if (enabled && state.polar.enabledAxes.length === 1) return state;
    return {
      polar: {
        ...state.polar,
        enabledAxes: enabled
          ? state.polar.enabledAxes.filter((item) => item !== axis)
          : [...state.polar.enabledAxes, axis],
      },
    };
  }),
  options: () => {
    const base: SolveOptions = {
      engine: get().engine,
      symmetry: get().symmetry,
      mesh_validation_mode: get().meshValidationMode,
      verbose: get().verbose,
      frequency_spacing: get().frequencySpacing,
      polar_config: polarConfigFromUi(get().polar),
    };
    if (get().frequencyMode !== 'list') return base;
    const { frequencies, error } = parseFrequencyList(get().frequencyListText);
    // Fail loudly. Falling back to the generated grid here would run a sweep
    // the user did not ask for and label it as theirs.
    if (frequencies === null) throw new Error(`Frequency list is not usable: ${error}`);
    return { ...base, frequencies_hz: frequencies };
  },
}), {
  name: 'solveOptions',
  // Reads and writes stay synchronous against the durable cache, so the store
  // still initialises during module evaluation; the server's copy arrives
  // later and triggers the explicit rehydrate below.
  storage: createJSONStorage(() => ({
    getItem: (name) => durableSettings.get(name as 'solveOptions'),
    setItem: (name, value) => durableSettings.set(name as 'solveOptions', value),
    removeItem: (name) => durableSettings.set(name as 'solveOptions', null),
  })),
  partialize: (state) => ({
    engine: state.engine,
    symmetry: state.symmetry,
    meshValidationMode: state.meshValidationMode,
    verbose: state.verbose,
    frequencySpacing: state.frequencySpacing,
    frequencyMode: state.frequencyMode,
    frequencyListText: state.frequencyListText,
    polar: state.polar,
  }),
  merge: (persisted, current) => {
    const stored = persisted as Partial<SolveOptionsStore> | undefined;
    return {
      ...current,
      ...stored,
      polar: { ...current.polar, ...(stored?.polar ?? {}) },
    };
  },
}));

export function resetSolveOptionsStore(): void {
  useSolveOptionsStore.setState({
    engine: 'auto',
    symmetry: 'auto',
    meshValidationMode: 'warn',
    verbose: false,
    frequencySpacing: 'log',
    frequencyMode: 'range',
    frequencyListText: '',
    polar: structuredClone(defaultPolarUi),
  });
}

// The server's copy is authoritative, so re-read once it has replaced the cache.
durableSettings.subscribe('solveOptions', () => { void useSolveOptionsStore.persist.rehydrate(); });

/**
 * Apply the directivity settings an opened `.cfg` actually specifies.
 *
 * Only the values present in the file are applied. This used to replace the
 * whole polar state with `polarUiFromAthBlocks`, which returns defaults for a
 * file that carries no `ABEC.Polars` blocks -- so opening an ATH file, or any
 * design saved before WG wrote those blocks, silently reset measurement
 * distance, angular step, normalization, planes, and origin, and overwrote the
 * stored copy on the way out. A file now overrides what it describes and
 * nothing else.
 */
export function restorePolarUiFromAthBlocks(blocks: unknown): void {
  const overrides = athPolarOverrides(blocks);
  if (!overrides) return;
  useSolveOptionsStore.setState((state) => ({ polar: { ...state.polar, ...overrides } }));
}

/**
 * Apply everything an opened design states about its solve.
 *
 * Covers ATH's directivity blocks and WG's own `WG.Solve` block in one call,
 * so a design carries the sweep, mesh policy, and measurement origin it was
 * saved with. Settings the file is silent about are left as they are.
 */
export function restoreSolveSettingsFromBlocks(blocks: unknown): void {
  restorePolarUiFromAthBlocks(blocks);
  const solve = wgSolveOverrides(blocks);
  if (!solve) return;
  const { observationOrigin, sphericalSampling, fieldPlane, ...flat } = solve;
  useSolveOptionsStore.setState((state) => ({
    ...flat,
    polar: {
      ...state.polar,
      ...(observationOrigin !== undefined ? { observationOrigin } : {}),
      ...(sphericalSampling !== undefined ? { sphericalSampling } : {}),
      ...(fieldPlane !== undefined ? { fieldPlane } : {}),
    },
  }));
}

import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { DEFAULT_ATH_POLAR_UI, MIN_POLAR_DISTANCE_M, athPolarOverrides } from './athPolars';
import { durableSettings } from './durableSettings';
import { MAX_FREQUENCY_POINTS, parseFrequencyList, type FrequencyListParse } from './frequencyList';
import {
  ENGINE_PATTERN,
  FREQUENCY_MODES,
  FREQUENCY_SPACINGS,
  MESH_VALIDATION_MODES,
  OBSERVATION_ORIGINS,
  SYMMETRY_MODES,
  wgSolveOverrides,
} from './wgSolveBlock';

export type MeshValidationMode = 'warn' | 'strict' | 'off';
export type FrequencySpacing = 'log' | 'linear';
export type PolarAxis = 'horizontal' | 'vertical' | 'diagonal';
export type ObservationOrigin = 'mouth' | 'throat';
export type SymmetryMode = 'auto' | 'full' | 'half_xz' | 'half_yz' | 'quarter';
export type SolverMode = 'auto' | 'full_3d' | 'circsym';
export type FrequencyMode = 'range' | 'list';

export const SOLVER_MODES: SolverMode[] = ['auto', 'full_3d', 'circsym'];

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
  solver_mode?: SolverMode;
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

export const POLAR_AXES: PolarAxis[] = ['horizontal', 'vertical', 'diagonal'];

/**
 * The legal range of each numeric directivity setting, stated once.
 *
 * These are not new limits: they are the invariants `polarConfigFromUi` has
 * always refused to build a request outside of, written down next to the
 * defaults so `normalizePolarUi` can enforce the same ones on a stored value.
 * The angular positions are unbounded on purpose -- a sweep may legitimately
 * run through negative angles -- so only finiteness is checked for those.
 *
 * `MIN_POLAR_DISTANCE_M` lives in `athPolars` (the `.cfg` reader clamps to it
 * too) and is re-exported here beside the limits it belongs with.
 */
export { MIN_POLAR_DISTANCE_M };
/**
 * Degrees, exclusive. The step has no clampable floor -- the panel offers
 * whole degrees and a `.cfg` may legitimately state a finer one -- so a stored
 * step that is not strictly above this falls back to the default instead.
 */
export const MIN_ANGLE_STEP_EXCLUSIVE_DEG = 0;

/** A render-safe verdict for the directivity grid as a whole. */
export function polarValidationError(ui: PolarUiState): string | null {
  const numeric = [ui.angleStart, ui.angleEnd, ui.angleStep, ui.distance, ui.normAngle, ui.diagonalAngle];
  if (!numeric.every(Number.isFinite)) return 'Directivity settings must be finite numbers.';
  if (ui.angleEnd <= ui.angleStart) return 'Directivity sweep end must be greater than its start.';
  if (ui.angleStep <= 0) return 'Directivity angular step must be greater than 0 degrees.';
  if (ui.distance < 0.1) return 'Directivity measurement distance must be at least 0.1 m.';
  if (ui.enabledAxes.length === 0) return 'Select at least one directivity plane.';
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
  if (count > 721) return `Directivity sweep supports at most 721 angle samples (got ${count}).`;
  return null;
}

export function polarConfigFromUi(ui: PolarUiState): PolarConfig {
  const validationError = polarValidationError(ui);
  if (validationError) throw new Error(validationError);
  const span = ui.angleEnd - ui.angleStart;
  const intervals = span / ui.angleStep;
  const nearestIntervals = Math.round(intervals);
  const resolvedIntervals = Math.abs(intervals - nearestIntervals) <= Math.max(1e-9, Math.abs(intervals) * 1e-12)
    ? nearestIntervals
    : Math.floor(intervals);
  const count = Math.max(2, resolvedIntervals + 1);
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

/** Exactly the solve settings that are written to durable storage. */
export interface PersistedSolveOptions {
  engine: string;
  solverMode: SolverMode;
  symmetry: SymmetryMode;
  meshValidationMode: MeshValidationMode;
  verbose: boolean;
  frequencySpacing: FrequencySpacing;
  frequencyMode: FrequencyMode;
  frequencyListText: string;
  polar: PolarUiState;
}

export const DEFAULT_SOLVE_OPTIONS: Readonly<PersistedSolveOptions> = Object.freeze({
  engine: 'auto',
  solverMode: 'auto',
  symmetry: 'auto',
  meshValidationMode: 'warn',
  verbose: false,
  frequencySpacing: 'log',
  frequencyMode: 'range',
  frequencyListText: '',
  polar: defaultPolarUi,
});

export function defaultSolveOptions(): PersistedSolveOptions {
  return { ...DEFAULT_SOLVE_OPTIONS, polar: structuredClone(defaultPolarUi) };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function oneOf<T extends string>(value: unknown, allowed: T[], fallback: T): T {
  return typeof value === 'string' && (allowed as string[]).includes(value) ? value as T : fallback;
}

function finite(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

/**
 * Bring a stored directivity rig back inside the range the solve path accepts.
 *
 * The persisted copy is a plain JSON file in the application data directory, so
 * it can arrive hand-edited, truncated, or written by a different version. It
 * used to be spread into the store unexamined, which is enough for a
 * non-array `enabledAxes` to throw out of the Simulation panel's render on the
 * first `.includes` -- the whole rail, not just the field that was wrong.
 *
 * `fallback` is the state this value is replacing, so a rehydrate that arrives
 * with one unreadable field keeps the user's own value for it rather than
 * resetting the field to the shipped default.
 */
export function normalizePolarUi(raw: unknown, fallback: PolarUiState = defaultPolarUi): PolarUiState {
  const stored = isRecord(raw) ? raw : {};
  const axes = Array.isArray(stored.enabledAxes)
    ? [...new Set(stored.enabledAxes.filter((axis): axis is PolarAxis => (POLAR_AXES as string[]).includes(axis as string)))]
    : [];
  const angleStep = finite(stored.angleStep, fallback.angleStep);
  // The fallback is itself a stored value on a rehydrate, so it gets the same
  // check before it is trusted as a replacement.
  const fallbackStep = fallback.angleStep > MIN_ANGLE_STEP_EXCLUSIVE_DEG ? fallback.angleStep : defaultPolarUi.angleStep;
  // A degenerate sweep (end at or below start) has no clampable repair either:
  // neither endpoint alone is the wrong one, so the pair falls back together.
  // Left in place, it would throw out of `polarConfigFromUi` on the render
  // path at the next launch -- durable settings must never hold one.
  const angleStart = finite(stored.angleStart, fallback.angleStart);
  const angleEnd = finite(stored.angleEnd, fallback.angleEnd);
  const fallbackPair: [number, number] = fallback.angleEnd > fallback.angleStart
    ? [fallback.angleStart, fallback.angleEnd]
    : [defaultPolarUi.angleStart, defaultPolarUi.angleEnd];
  const [rangeStart, rangeEnd] = angleEnd > angleStart ? [angleStart, angleEnd] : fallbackPair;
  return {
    angleStart: rangeStart,
    angleEnd: rangeEnd,
    angleStep: angleStep > MIN_ANGLE_STEP_EXCLUSIVE_DEG ? angleStep : fallbackStep,
    distance: Math.max(MIN_POLAR_DISTANCE_M, finite(stored.distance, fallback.distance)),
    normAngle: finite(stored.normAngle, fallback.normAngle),
    diagonalAngle: finite(stored.diagonalAngle, fallback.diagonalAngle),
    // A rig with no planes cannot be solved and the panel refuses to empty the
    // set, so an empty stored list is corruption rather than a choice.
    enabledAxes: axes.length ? axes : [...fallback.enabledAxes],
    observationOrigin: oneOf(stored.observationOrigin, OBSERVATION_ORIGINS, fallback.observationOrigin),
    sphericalSampling: typeof stored.sphericalSampling === 'boolean' ? stored.sphericalSampling : fallback.sphericalSampling,
    fieldPlane: typeof stored.fieldPlane === 'boolean' ? stored.fieldPlane : fallback.fieldPlane,
  };
}

/**
 * The same treatment for the flat solver settings around the rig.
 *
 * Applied on the way in and on the way out, so neither a corrupt stored payload
 * nor an unvalidated in-memory value can outlive one load: `frequencyListText`
 * held anything but a string used to throw from `String.prototype.split` the
 * moment the sweep section rendered.
 */
export function normalizePersistedSolveOptions(
  raw: unknown,
  fallback: PersistedSolveOptions = DEFAULT_SOLVE_OPTIONS,
): PersistedSolveOptions {
  const stored = isRecord(raw) ? raw : {};
  return {
    engine: typeof stored.engine === 'string' && ENGINE_PATTERN.test(stored.engine) ? stored.engine : fallback.engine,
    solverMode: oneOf(stored.solverMode, SOLVER_MODES, fallback.solverMode),
    symmetry: oneOf(stored.symmetry, SYMMETRY_MODES, fallback.symmetry),
    meshValidationMode: oneOf(stored.meshValidationMode, MESH_VALIDATION_MODES, fallback.meshValidationMode),
    verbose: typeof stored.verbose === 'boolean' ? stored.verbose : fallback.verbose,
    frequencySpacing: oneOf(stored.frequencySpacing, FREQUENCY_SPACINGS, fallback.frequencySpacing),
    frequencyMode: oneOf(stored.frequencyMode, FREQUENCY_MODES, fallback.frequencyMode),
    frequencyListText: typeof stored.frequencyListText === 'string' ? stored.frequencyListText : fallback.frequencyListText,
    polar: normalizePolarUi(stored.polar, fallback.polar),
  };
}

interface SolveOptionsStore extends PersistedSolveOptions {
  setEngine: (engine: string) => void;
  setSolverMode: (solverMode: SolverMode) => void;
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
  ...defaultSolveOptions(),
  setEngine: (engine) => set({ engine }),
  setSolverMode: (solverMode) => set({ solverMode }),
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
      solver_mode: get().solverMode,
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
  // Normalized on the way out as well as in. The store's setters take their
  // values straight from number fields and from `.cfg` blocks, so what reaches
  // storage has not necessarily been through the submit-time validator; a value
  // outside the legal range is kept on screen while it is being typed but is
  // not what the next session loads.
  partialize: (state) => normalizePersistedSolveOptions(state),
  merge: (persisted, current) => ({ ...current, ...normalizePersistedSolveOptions(persisted, current) }),
}));

export function resetSolveOptionsStore(): void {
  useSolveOptionsStore.setState(defaultSolveOptions());
}

// The server's copy is authoritative, so re-read once it has replaced the cache.
durableSettings.subscribe('solveOptions', () => { void useSolveOptionsStore.persist.rehydrate(); });

/**
 * Apply the directivity settings an opened `.cfg` actually specifies.
 *
 * Only the values present in the file are applied. This used to replace the
 * whole polar state with `polarUiFromAthBlocks`, which returns defaults for a
 * file that carries no `ABEC.Polars` blocks -- so opening an ATH file, or any
 * design serialized before WG wrote those blocks, silently reset measurement
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

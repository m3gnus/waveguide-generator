import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { DEFAULT_ATH_POLAR_UI, polarUiFromAthBlocks } from './athPolars';

export type MeshValidationMode = 'warn' | 'strict' | 'off';
export type FrequencySpacing = 'log' | 'linear';
export type PolarAxis = 'horizontal' | 'vertical' | 'diagonal';
export type ObservationOrigin = 'mouth' | 'throat';
export type SymmetryMode = 'auto' | 'full' | 'half_xz' | 'half_yz' | 'quarter';
export type FrequencyMode = 'range' | 'list';

/** Server-side ceiling in ``SolveOptions.frequencies_hz``; mirrored for fast feedback. */
export const MAX_FREQUENCY_POINTS = 401;

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

export interface FrequencyListParse {
  frequencies: number[] | null;
  error: string | null;
}

/**
 * Parse a free-typed sweep into the ``frequencies_hz`` contract.
 *
 * The rules mirror the server's validator exactly, so the dialog can reject a
 * bad list before a solve is queued. Nothing here repairs input: a list that
 * cannot be read as written is an error, never a quietly sorted or truncated
 * sweep the user did not ask for.
 */
export function parseFrequencyList(text: string): FrequencyListParse {
  const tokens = text.split(/[\s,;]+/).filter((token) => token.length > 0);
  if (tokens.length === 0) return { frequencies: null, error: 'Enter at least one frequency.' };
  if (tokens.length > MAX_FREQUENCY_POINTS) {
    return { frequencies: null, error: `At most ${MAX_FREQUENCY_POINTS} frequencies (got ${tokens.length}).` };
  }
  const frequencies: number[] = [];
  for (const token of tokens) {
    const value = Number(token);
    if (!Number.isFinite(value)) return { frequencies: null, error: `"${token}" is not a number.` };
    if (value <= 0) return { frequencies: null, error: `Frequencies must be above 0 Hz ("${token}").` };
    frequencies.push(value);
  }
  for (let index = 1; index < frequencies.length; index += 1) {
    if (frequencies[index] <= frequencies[index - 1]) {
      return {
        frequencies: null,
        error: `Frequencies must ascend: ${frequencies[index]} follows ${frequencies[index - 1]}.`,
      };
    }
  }
  return { frequencies, error: null };
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
  name: 'waveguide-v2-solve-options',
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

/** Restore v1/ATH polar blocks when a `.cfg` becomes the active document. */
export function restorePolarUiFromAthBlocks(blocks: unknown): void {
  useSolveOptionsStore.setState({ polar: polarUiFromAthBlocks(blocks) });
}

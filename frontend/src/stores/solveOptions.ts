import { create } from 'zustand';

export type MeshValidationMode = 'warn' | 'strict' | 'off';
export type FrequencySpacing = 'log' | 'linear';
export type PolarAxis = 'horizontal' | 'vertical' | 'diagonal';
export type ObservationOrigin = 'mouth' | 'throat';

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
  mesh_validation_mode: MeshValidationMode;
  verbose: boolean;
  frequency_spacing: FrequencySpacing;
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
}

export const defaultPolarUi: PolarUiState = {
  angleStart: 0,
  angleEnd: 180,
  angleStep: 5,
  distance: 2,
  normAngle: 5,
  diagonalAngle: 45,
  enabledAxes: ['horizontal', 'vertical', 'diagonal'],
  observationOrigin: 'mouth',
  sphericalSampling: false,
};

export function polarConfigFromUi(ui: PolarUiState): PolarConfig {
  const span = ui.angleEnd - ui.angleStart;
  const count = Math.max(2, Math.floor(span / Math.max(1e-9, ui.angleStep)) + 1);
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
  meshValidationMode: MeshValidationMode;
  verbose: boolean;
  frequencySpacing: FrequencySpacing;
  polar: PolarUiState;
  setEngine: (engine: string) => void;
  setMeshValidationMode: (mode: MeshValidationMode) => void;
  setVerbose: (verbose: boolean) => void;
  setFrequencySpacing: (spacing: FrequencySpacing) => void;
  updatePolar: (update: Partial<PolarUiState>) => void;
  toggleAxis: (axis: PolarAxis) => void;
  options: () => SolveOptions;
}

export const useSolveOptionsStore = create<SolveOptionsStore>((set, get) => ({
  engine: 'auto',
  meshValidationMode: 'warn',
  verbose: false,
  frequencySpacing: 'log',
  polar: structuredClone(defaultPolarUi),
  setEngine: (engine) => set({ engine }),
  setMeshValidationMode: (meshValidationMode) => set({ meshValidationMode }),
  setVerbose: (verbose) => set({ verbose }),
  setFrequencySpacing: (frequencySpacing) => set({ frequencySpacing }),
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
  options: () => ({
    engine: get().engine,
    mesh_validation_mode: get().meshValidationMode,
    verbose: get().verbose,
    frequency_spacing: get().frequencySpacing,
    polar_config: polarConfigFromUi(get().polar),
  }),
}));

export function resetSolveOptionsStore(): void {
  useSolveOptionsStore.setState({
    engine: 'auto',
    meshValidationMode: 'warn',
    verbose: false,
    frequencySpacing: 'log',
    polar: structuredClone(defaultPolarUi),
  });
}

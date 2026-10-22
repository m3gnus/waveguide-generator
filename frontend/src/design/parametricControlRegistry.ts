import type { DesignFamily } from '../stores/design';
import { NATIVE_FAMILIES } from './nativeFamilies';
import { osseFeatures } from './osseFeatures';
import type { ParameterSection, ParameterTab } from './parameterRegistry';

export interface ParametricControlDescriptor {
  /** Stable palette identity; custom solve controls are not design paths. */
  id: string;
  label: string;
  section: ParameterSection;
  tab: ParameterTab;
  keywords: readonly string[];
  reveal: { id: string; target: 'control' };
}

/**
 * Search metadata for semantic parametric controls, including formula choices
 * and values that live outside the design document. Like the CAD registry, it
 * owns semantic UI controls; persisted ATH fields remain in PARAMETER_REGISTRY.
 */
export const PARAMETRIC_CONTROLS = {
  osseFeatures: {
    id: 'osse-features', label: 'OSSE features', section: 'Model Type', tab: 'geometry',
    keywords: ['throat', 'adapter authored', 'adapter controls', 'mouth end', 'roundover', 'terminating arc', 'axial scale', 'OSSE-ADAPTER', 'OSSE-ADAPTER-CONTROLS', 'OSSE-ADAPTER-AXIAL', 'OSSE-ROUNDOVER', 'OSSE-AXIAL', 'OSSE-ARC'],
    reveal: { id: 'parametric.osse-features', target: 'control' },
  },
  solveDomain: {
    id: 'solve-domain',
    label: 'Solve domain',
    section: 'Mesh',
    tab: 'simulation',
    keywords: ['symmetry', 'full domain', 'half domain', 'quarter domain', 'solveOptions.symmetry'],
    reveal: { id: 'parametric.solve-domain', target: 'control' },
  },
  mounting: {
    id: 'mounting', label: 'Mounting', section: 'Mounting', tab: 'geometry',
    keywords: ['outer body', 'simulation.sim_type', 'simType', 'bare shell', 'thickened waveguide', 'enclosure', 'infinite baffle'],
    reveal: { id: 'parametric.mounting', target: 'control' },
  },
} as const satisfies Record<string, ParametricControlDescriptor>;

export const PARAMETRIC_CONTROL_DESCRIPTORS: readonly ParametricControlDescriptor[] = [
  ...Object.values(PARAMETRIC_CONTROLS),
  ...([
    ['frequency-points', 'Sweep points', 'Frequency sweep', 'simulation', 'design-solve-frequency-mode'],
    ['frequency-spacing', 'Sweep spacing', 'Frequency sweep', 'simulation', 'design-solve-frequency-spacing'],
    ['frequency-list', 'Solver frequencies in Hz', 'Frequency sweep', 'simulation', 'design-solve-frequency-list'],
    ['adaptive-sampling', 'Adaptive frequency sampling', 'Frequency sweep', 'simulation', 'adaptive-frequency-sampling'],
    ['accuracy', 'Solve accuracy', 'Solver', 'simulation', 'solve-accuracy'],
    ['backend', 'Advanced backend', 'Solver', 'simulation', 'solve-engine'],
    ['ground-plane', 'Ground plane', 'Solver', 'simulation', 'solve-ground-plane'],
    ['verbose', 'Verbose backend logging', 'Solver', 'simulation', 'solve-verbose'],
    ['ground-axis', 'Reflecting surface', 'Solver', 'simulation', 'ground-plane-axis'],
    ['ground-height', 'Height above surface', 'Solver', 'simulation', 'ground-plane-height'],
    ['angle-start', 'Start angle', 'Directivity map', 'simulation', 'polar-angle-start'],
    ['angle-end', 'End angle', 'Directivity map', 'simulation', 'polar-angle-end'],
    ['angle-step', 'Angular step', 'Directivity map', 'simulation', 'polar-angle-step'],
    ['distance', 'Measurement distance', 'Directivity map', 'simulation', 'polar-distance'],
    ['normalization', 'Normalization angle', 'Directivity map', 'simulation', 'polar-norm-angle'],
    ['planes', 'Directivity planes', 'Directivity map', 'simulation', 'polar-planes'],
    ['diagonal', 'Diagonal plane angle', 'Directivity map', 'simulation', 'polar-diagonal-angle'],
    ['origin', 'Measurement origin', 'Directivity map', 'simulation', 'polar-observation-origin'],
    ['balloon', 'Keep 3D balloon result', 'Directivity map', 'simulation', 'polar-spherical-sampling'],
    ['field-plane', 'Keep field plane data', 'Directivity map', 'simulation', 'polar-field-plane'],
    ['validation', 'Mesh validation policy', 'Mesh', 'simulation', 'mesh-validation-mode'],
    ['directivity', 'Directivity map', 'Directivity map', 'simulation', 'parametric.section.Directivity map'],
  ] as const).map(([id, label, section, tab, revealId]) => ({ id, label, section, tab, keywords: [], reveal: { id: revealId, target: 'control' as const } })),
];

export function parametricControlMatchesQuery(descriptor: ParametricControlDescriptor, query: string): boolean {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return true;
  const searchable = [descriptor.label, descriptor.section, descriptor.id, ...descriptor.keywords]
    .join(' ').toLocaleLowerCase();
  return normalized.split(/\s+/).every((token) => searchable.includes(token));
}

/** Keep palette entries and rail filtering subject to the same family gates. */
export function parametricControlIsAvailable(descriptor: ParametricControlDescriptor, family: DesignFamily): boolean {
  if (descriptor.id === 'osse-features') return Boolean(osseFeatures(family));
  if (descriptor.id === 'mounting') return !(NATIVE_FAMILIES as readonly string[]).includes(family);
  return true;
}

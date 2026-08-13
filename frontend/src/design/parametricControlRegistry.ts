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
 * Search metadata for parametric controls whose values live outside the design
 * document. This mirrors the CAD registry boundary: semantic UI controls belong
 * here, while persisted ATH fields remain exclusively in PARAMETER_REGISTRY.
 */
export const PARAMETRIC_CONTROLS = {
  solveDomain: {
    id: 'solve-domain',
    label: 'Solve domain',
    section: 'Solve & export mesh',
    tab: 'simulation',
    keywords: ['symmetry', 'full domain', 'half domain', 'quarter domain', 'solveOptions.symmetry'],
    reveal: { id: 'parametric.solve-domain', target: 'control' },
  },
} as const satisfies Record<string, ParametricControlDescriptor>;

export const PARAMETRIC_CONTROL_DESCRIPTORS: readonly ParametricControlDescriptor[] = Object.values(PARAMETRIC_CONTROLS);

export function parametricControlMatchesQuery(descriptor: ParametricControlDescriptor, query: string): boolean {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return true;
  const searchable = [descriptor.label, descriptor.section, descriptor.id, ...descriptor.keywords]
    .join(' ').toLocaleLowerCase();
  return normalized.split(/\s+/).every((token) => searchable.includes(token));
}

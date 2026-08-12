import type { DesignDocument } from '../stores/design';
import type { SolveOptions } from '../stores/solveOptions';

type CanonicalScalar = string | number | boolean | null;
type CanonicalValue = CanonicalScalar | CanonicalValue[] | { [key: string]: CanonicalValue };

export interface SubmittedDesignProjection {
  version: 1;
  kind: 'design';
  design: { [key: string]: CanonicalValue };
  solveOptions: { [key: string]: CanonicalValue };
}

/**
 * Every family-owned scalar which is submitted at the root of a design.
 * Composite FREEFORM data is deliberately outside the Stage 4 projection;
 * the named shared blocks below are the only nested design structures in it.
 */
const FAMILY_SCALARS = [
  'R', 'L', 'a', 'a0', 'r0', 'k', 's', 'n', 'q', 'h', 'b', 'm', 'r',
  'tmax', 'coverage_angle', 'hold_start', 'hold_end', 'n_coeff',
  'termination', 'theta1_deg', 'depth', 'curl', 'length', 'throat_profile',
  'rotation', 'circ_arc_radius', 'circ_arc_term_angle', 'inflection_policy',
] as const satisfies readonly (keyof DesignDocument)[];

const COMMON_SCALARS = [
  'scale', 'throat_ext_angle', 'throat_ext_length', 'slot_length', 'length_mode',
] as const satisfies readonly (keyof DesignDocument)[];

const SUBMITTED_BLOCKS = [
  'guiding_curve', 'morph', 'mesh', 'source', 'enclosure', 'simulation', 'output',
] as const satisfies readonly (keyof DesignDocument)[];

function canonicalize(value: unknown): CanonicalValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') return value;
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, item]) => item !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)]),
    );
  }
  throw new TypeError(`Unsupported submitted projection value: ${String(value)}`);
}

/**
 * Build the stable, reusable view used to decide whether a submitted design
 * changed. Evaluated numeric fields are copied from DesignDocument directly;
 * `_expressions` and every other UI sidecar are intentionally ignored.
 */
export function projectSubmittedDesign(
  design: DesignDocument,
  solveOptions: SolveOptions,
): SubmittedDesignProjection {
  const projectedDesign: Record<string, CanonicalValue> = { formula: design.formula };
  for (const key of [...FAMILY_SCALARS, ...COMMON_SCALARS]) {
    const value = design[key];
    if (value !== undefined) projectedDesign[key] = canonicalize(value);
  }
  for (const key of SUBMITTED_BLOCKS) projectedDesign[key] = canonicalize(design[key]);
  return {
    version: 1,
    kind: 'design',
    design: projectedDesign,
    solveOptions: canonicalize(solveOptions) as SubmittedDesignProjection['solveOptions'],
  };
}

export function submittedProjectionsEqual(
  left: SubmittedDesignProjection | null | undefined,
  right: SubmittedDesignProjection | null | undefined,
): boolean {
  if (left === right) return true;
  if (!left || !right) return false;
  return JSON.stringify(left) === JSON.stringify(right);
}

export function isSubmittedDesignProjection(value: unknown): value is SubmittedDesignProjection {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<SubmittedDesignProjection>;
  return candidate.version === 1
    && candidate.kind === 'design'
    && Boolean(candidate.design && typeof candidate.design === 'object')
    && Boolean(candidate.solveOptions && typeof candidate.solveOptions === 'object');
}

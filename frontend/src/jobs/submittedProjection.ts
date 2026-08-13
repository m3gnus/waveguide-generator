import type { DesignDocument } from '../stores/design';
import type { SolveOptions } from '../stores/solveOptions';
import type { ImportedSolveSubmission } from './actions';

type CanonicalScalar = string | number | boolean | null;
type CanonicalValue = CanonicalScalar | CanonicalValue[] | { [key: string]: CanonicalValue };

interface SubmittedProjectionBase {
  version: 1;
  solveOptions: { [key: string]: CanonicalValue };
}

export interface SubmittedParametricProjection extends SubmittedProjectionBase {
  kind: 'design';
  design: { [key: string]: CanonicalValue };
}

export interface SubmittedImportedProjection extends SubmittedProjectionBase {
  kind: 'imported';
  geometry: { [key: string]: CanonicalValue };
}

export type SubmittedDesignProjection = SubmittedParametricProjection | SubmittedImportedProjection;

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
): SubmittedParametricProjection {
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
    solveOptions: canonicalize(solveOptions) as SubmittedParametricProjection['solveOptions'],
  };
}

/**
 * Project only the imported geometry and electrical/solve choices that reach
 * the solver. In particular, the parametric design on screen is deliberately
 * absent: it describes a future CAD rebuild, not the already-ingested artifact
 * this submission will solve.
 */
export function projectSubmittedImport(
  submission: ImportedSolveSubmission,
): SubmittedImportedProjection {
  const { geometry, options } = submission;
  const projectedGeometry: Record<string, unknown> = {
    ingest_id: geometry.ingest_id,
    manifest_sha256: geometry.manifest_sha256,
    artifact_sha256: geometry.artifact_sha256,
    drive_channels: geometry.drive_channels,
    mesh: geometry.mesh,
    exterior_only: geometry.exterior_only ?? false,
  };
  // These fields are conditional on the wire, but when present they alter the
  // solved levels just as surely as a driver spec does. Keeping them beside
  // drive_channels prevents a voltage-only edit from reusing the old run name.
  if (geometry.drive_voltage_v !== undefined) projectedGeometry.drive_voltage_v = geometry.drive_voltage_v;
  if (geometry.rg_ohm !== undefined) projectedGeometry.rg_ohm = geometry.rg_ohm;
  if (geometry.combine !== undefined) projectedGeometry.combine = geometry.combine;
  return {
    version: 1,
    kind: 'imported',
    geometry: canonicalize(projectedGeometry) as SubmittedImportedProjection['geometry'],
    solveOptions: canonicalize(options) as SubmittedImportedProjection['solveOptions'],
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
  if (candidate.version !== 1 || !candidate.solveOptions || typeof candidate.solveOptions !== 'object') return false;
  if (candidate.kind === 'design') return Boolean(candidate.design && typeof candidate.design === 'object');
  if (candidate.kind === 'imported') return Boolean(candidate.geometry && typeof candidate.geometry === 'object');
  return false;
}

import type { CadSolveSetup } from '../api/cadOperations';
import type { ImportedGeometrySubmission, ImportedSolveSubmission } from './actions';

export interface PreparedMeshIdentity {
  ingest_id: string;
  manifest_sha256: string;
  artifact_sha256: string;
}

/** Resolved physics belongs to the setup; identity belongs to preparation. */
export interface ImportedSetup {
  geometry: Omit<ImportedGeometrySubmission,
    'type' | 'ingest_id' | 'manifest_sha256' | 'artifact_sha256' | 'acknowledged_findings'>;
  options: ImportedSolveSubmission['options'];
  preparation?: CadSolveSetup['preparation'];
  driver_references?: CadSolveSetup['driver_references'];
}

/** Apply only changes in the controls, preserving physics those controls cannot express. */
export function applySetupEdits(retained: unknown, before: unknown, after: unknown): unknown {
  if (JSON.stringify(before) === JSON.stringify(after)) return structuredClone(retained);
  const object = (value: unknown): value is Record<string, unknown> =>
    value !== null && typeof value === 'object' && !Array.isArray(value);
  // Channel edits keep the untouched fields of each surviving channel. Other
  // arrays (frequency lists, source assignments) are one control's whole value.
  if (Array.isArray(before) && Array.isArray(after) && Array.isArray(retained)
    && [...before, ...after].every(item => object(item) && typeof item.id === 'string')) {
    return after.map(item => applySetupEdits(
      retained.find(old => old.id === item.id), before.find(old => old.id === item.id), item));
  }
  if (object(before) && object(after)) {
    const result = object(retained) ? structuredClone(retained) : {};
    for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
      if (JSON.stringify(before[key]) === JSON.stringify(after[key])) continue;
      if (!(key in after)) delete result[key];
      else result[key] = applySetupEdits(result[key], before[key], after[key]);
    }
    return result;
  }
  return structuredClone(after);
}

/** Both producers bind a frozen setup to one retained mesh, never live buffers. */
export function bindImportedSetup(
  record: PreparedMeshIdentity,
  setup: ImportedSetup,
  acknowledgedFindings: string[] = [],
): ImportedSolveSubmission {
  // Preserve the existing CAD wire order as well as its values. Findings are
  // snapshot evidence, inserted before the setup's source exclusions.
  const geometry = Object.fromEntries(Object.entries(setup.geometry).flatMap(([key, value]) =>
    key === 'skipped_source_ids'
      ? [['acknowledged_findings', acknowledgedFindings], [key, value]]
      : [[key, value]]));
  return structuredClone({
    geometry: { type: 'imported', ingest_id: record.ingest_id,
      manifest_sha256: record.manifest_sha256, artifact_sha256: record.artifact_sha256,
      ...geometry } as ImportedGeometrySubmission,
    options: setup.options,
  });
}

/** A bound submission without the prepared mesh's identity: the setup it was made of. */
export function importedSetupOf(submission: ImportedSolveSubmission): ImportedSetup {
  const { type: _type, ingest_id: _ingest, manifest_sha256: _manifest, artifact_sha256: _artifact,
    acknowledged_findings: _findings, ...geometry } = submission.geometry;
  return structuredClone({ geometry, options: submission.options });
}

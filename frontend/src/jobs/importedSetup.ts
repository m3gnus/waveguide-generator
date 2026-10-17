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

/** The setup a bound submission was made of: everything but the prepared mesh's identity. */
export function setupOfSubmission(submission: ImportedSolveSubmission): CadSolveSetup {
  const { type: _type, ingest_id: _ingest, manifest_sha256: _manifest, artifact_sha256: _artifact,
    acknowledged_findings: _findings, ...geometry } = submission.geometry;
  return structuredClone({ schema_version: 1, geometry, options: submission.options }) as unknown as CadSolveSetup;
}

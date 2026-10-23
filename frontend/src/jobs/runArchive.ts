import type { JobItem } from '../api/jobsSocket';
import { exportStemForJob, runSourceForJob } from './exportNaming';

/**
 * The permanent record of a solve.
 *
 * With automatic cleanup on (`autoCleanupResults`, off by default), the job
 * database prunes result payloads after 30 days, or sooner when the
 * terminal-run cap is passed, and only a rated run is exempt; and a user can
 * remove a run at any time. Nothing else on disk said a run had ever
 * happened, so a CAD solve that was not manually exported could simply
 * disappear. A run record beside the exported curves preserves that evidence. The server owns the project pointer.
 *
 * It is derived entirely from the job item, with no clock reading, so
 * archiving the same run twice produces the same bytes and the backend's
 * `merge_identical` write stays a no-op rather than a 409.
 */

export const RUN_RECORD_FILENAME = 'run.json';
export const RUN_RECORD_SCHEMA_VERSION = 1;

export interface WorkspaceTextFile {
  filename: string;
  text: string;
}

function stableJson(value: unknown): string {
  return `${JSON.stringify(value, null, 2)}\n`;
}

/**
 * One run's record: what was solved, from what, with which settings.
 *
 * `nameOf` maps an artifact's usual filename to the one it was written under,
 * for the rare archive that had to move a file aside (`ARCHIVE_DISPLACED_FOLDER`).
 * `members` lists every file the archive wrote beside this record, by its usual
 * filename, and the record names each one at `nameOf` -- so a result file moved
 * aside shows as `archive/<name>`, not only the mesh and impedance files.
 */
export function buildRunRecord(
  job: JobItem,
  nameOf: (filename: string) => string = (filename) => filename,
  members: readonly string[] = [],
  meshReplay?: { filename: string | null; reason?: string },
  modelFile?: string,
): WorkspaceTextFile {
  if (job.run_number === null) throw new Error('A run number is required to archive a solve');
  const cad = job.cad_source ?? null;
  return {
    filename: RUN_RECORD_FILENAME,
    text: stableJson({
      schemaVersion: RUN_RECORD_SCHEMA_VERSION,
      run: {
        name: exportStemForJob(job),
        number: job.run_number,
        jobId: job.id,
        label: job.label ?? null,
        rating: job.rating ?? null,
      },
      // The folder groups by design, so provenance lives here instead.
      source: runSourceForJob(job),
      ...(job.config_summary.import_origin === 'mesh-file' ? {
        importedMesh: { ...(job.config_summary.mesh_import as Record<string, unknown> ?? {}), setup: job.cad_setup },
      } : {}),
      design: {
        name: (job.archive_key ? job.design_name : null) ?? job.label ?? null,
        ...(job.archive_key && job.design_uid ? { uid: job.design_uid } : {}),
        lineageId: cad?.lineage_id ?? null,
        designId: cad?.design_id ?? null,
        revision: job.design_revision,
      },
      cad: cad && {
        ingestId: cad.ingest_id,
        manifestSha256: cad.manifest_sha256,
        documentName: cad.document_name,
        returnStateHash: cad.return_state_hash,
        ...(modelFile ? { modelFile } : {}),
        ...(cad.identity ? { identity: cad.identity } : {}),
      },
      solve: {
        ...job.solve_options,
        solvePath: job.solve_path ?? null,
        symmetry: job.symmetry ?? {},
        wallTimeSeconds: job.solve_wall_time_seconds ?? null,
      },
      mesh: job.mesh_stats ?? null,
      artifacts: {
        ...(members.length ? { files: members.map(nameOf).sort() } : {}),
        ...(meshReplay ? { meshReplay: { ...meshReplay, filename: meshReplay.filename ? nameOf(meshReplay.filename) : null } } : {}),
        mesh: job.has_mesh_artifact ? nameOf(`${exportStemForJob(job)}.msh`) : null,
        radiationImpedance: job.has_radiation_impedance_artifact ? {
          matrix: nameOf(`${exportStemForJob(job)}_radiation_impedance.npz`),
          curves: nameOf(`${exportStemForJob(job)}_radiation_impedance.csv`),
          bytes: job.radiation_impedance_artifact_bytes ?? null,
          units: 'Pa*s/m^3',
          phaseTimeConvention: 'engineering_exp_plus_jwt',
        } : null,
      },
      configSummary: job.config_summary,
      timing: {
        createdAt: job.created_at,
        queuedAt: job.queued_at,
        startedAt: job.started_at,
        completedAt: job.completed_at,
      },
    }),
  };
}

/** Whether this run still needs archiving. */
export function needsArchiving(job: JobItem): boolean {
  return job.run_number !== null && job.status === 'complete' && job.has_results && !job.archived_at;
}

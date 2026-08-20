import type { JobItem } from '../api/jobsSocket';
import { archiveFolderForJob, exportStemForJob, runSourceForJob } from './exportNaming';

/**
 * The permanent record of a solve.
 *
 * The job database prunes result payloads after 30 days, or sooner when the
 * terminal-run cap is passed, and only a rated run is exempt. Nothing else on
 * disk said a run had ever happened, so a CAD solve that was not manually
 * exported simply disappeared. These two files are what survives that: a run
 * record beside the exported curves, and one pointer file naming the design
 * the folder belongs to.
 *
 * Both are derived entirely from the job item, with no clock reading, so
 * archiving the same run twice produces the same bytes and the backend's
 * `merge_identical` write stays a no-op rather than a 409.
 */

export const RUN_RECORD_FILENAME = 'run.json';
export const DESIGN_RECORD_FILENAME = 'design.json';
export const RUN_RECORD_SCHEMA_VERSION = 1;

export interface WorkspaceTextFile {
  filename: string;
  text: string;
}

function stableJson(value: unknown): string {
  return `${JSON.stringify(value, null, 2)}\n`;
}

/** The design folder's pointer file: which design these runs belong to. */
export function buildDesignRecord(job: JobItem): WorkspaceTextFile {
  return {
    filename: DESIGN_RECORD_FILENAME,
    text: stableJson({
      schemaVersion: RUN_RECORD_SCHEMA_VERSION,
      folder: archiveFolderForJob(job),
      name: job.label ?? null,
      lineageId: job.cad_source?.lineage_id ?? null,
      designId: job.cad_source?.design_id ?? null,
    }),
  };
}

/** One run's record: what was solved, from what, with which settings. */
export function buildRunRecord(job: JobItem): WorkspaceTextFile {
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
      design: {
        name: job.label ?? null,
        lineageId: cad?.lineage_id ?? null,
        designId: cad?.design_id ?? null,
        revision: job.design_revision,
      },
      cad: cad && {
        ingestId: cad.ingest_id,
        manifestSha256: cad.manifest_sha256,
        documentName: cad.document_name,
        returnStateHash: cad.return_state_hash,
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
        mesh: job.has_mesh_artifact ? `${exportStemForJob(job)}.msh` : null,
        radiationImpedance: job.has_radiation_impedance_artifact ? {
          matrix: `${exportStemForJob(job)}_radiation_impedance.npz`,
          curves: `${exportStemForJob(job)}_radiation_impedance.csv`,
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
  return job.status === 'complete' && job.has_results && !job.archived_at;
}

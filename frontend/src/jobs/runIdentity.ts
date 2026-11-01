import type { JobItem } from '../api/jobsSocket';
import { runSourceForJob } from './exportNaming';

/** Use the run's retained identity, never the live CAD connection or document. */
export function runDocumentName(job: Partial<Pick<JobItem, 'cad_source' | 'cad_state'>>): string {
  return job.cad_source?.document_name?.trim() || job.cad_state?.snapshot?.document_name?.trim() || '';
}

/** Where a run came from, in the app's own words. Run archives keep `runSourceForJob`. */
export type RunOrigin = ReturnType<typeof runSourceForJob> | 'assembly';
export function runOrigin(job: Pick<JobItem, 'config_summary'>): RunOrigin {
  return job.config_summary.import_origin === 'design-assembly' ? 'assembly' : runSourceForJob(job);
}

export function runSourceLabel(job: JobItem): string {
  const source = runOrigin(job);
  return source === 'assembly' ? 'Assembly' : source === 'mesh-file' ? 'Mesh file' : source === 'cadlink' ? 'CAD Link' : 'Parametric';
}

export function runSourceText(job: JobItem): string {
  return [runSourceLabel(job), runDocumentName(job)].filter(Boolean).join(' · ');
}

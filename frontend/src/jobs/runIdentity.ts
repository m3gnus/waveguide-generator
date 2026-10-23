import type { JobItem } from '../api/jobsSocket';
import { runSourceForJob } from './exportNaming';

/** Use the run's retained identity, never the live CAD connection or document. */
export function runDocumentName(job: Partial<Pick<JobItem, 'cad_source' | 'cad_state'>>): string {
  return job.cad_source?.document_name?.trim() || job.cad_state?.snapshot?.document_name?.trim() || '';
}

export function runSourceLabel(job: JobItem): string {
  const source = runSourceForJob(job);
  return source === 'mesh-file' ? 'Mesh file' : source === 'cadlink' ? 'CAD Link' : 'Parametric';
}

export function runSourceText(job: JobItem): string {
  return [runSourceLabel(job), runDocumentName(job)].filter(Boolean).join(' · ');
}

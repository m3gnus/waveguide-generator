import type { JobItem } from '../api/jobsSocket';

/** Use the run's retained identity, never the live CAD connection or document. */
export function runDocumentName(job: Partial<Pick<JobItem, 'cad_source' | 'cad_state'>>): string {
  return job.cad_source?.document_name?.trim() || job.cad_state?.snapshot?.document_name?.trim() || '';
}

/** One origin for run display, filtering and archives. */
export type RunOrigin = 'parametric' | 'cadlink' | 'mesh-file' | 'assembly';
export function runOrigin(job: Pick<JobItem, 'config_summary'>): RunOrigin {
  if (job.config_summary.import_origin === 'design-assembly') return 'assembly';
  if (job.config_summary.import_origin === 'mesh-file') return 'mesh-file';
  return job.config_summary.geometry_type === 'imported' ? 'cadlink' : 'parametric';
}

export function runSourceLabel(job: JobItem): string {
  const source = runOrigin(job);
  return source === 'assembly' ? 'Assembly' : source === 'mesh-file' ? 'Mesh file' : source === 'cadlink' ? 'CAD Link' : 'Parametric';
}

export function runSourceText(job: JobItem): string {
  return [runSourceLabel(job), runDocumentName(job)].filter(Boolean).join(' · ');
}

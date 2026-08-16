import type { JobItem } from '../api/jobsSocket';
import { hydrateJobDesign } from '../jobs/jobDesign';
import type { DesignDocument } from '../stores/design';

/** Geometry/config exports for a result must use the design that produced it. */
export function resultExportSnapshot(
  job: (Pick<JobItem, 'script_snapshot' | 'design_revision'>
    & Partial<Pick<JobItem, 'solve_options'>>) | undefined,
): { design: DesignDocument | undefined; designRevision: number; polarConfig?: unknown } {
  const polarConfig = job?.solve_options?.polar_config;
  return {
    design: job ? hydrateJobDesign(job) ?? undefined : undefined,
    designRevision: job?.design_revision ?? 0,
    ...(polarConfig && typeof polarConfig === 'object' ? { polarConfig } : {}),
  };
}

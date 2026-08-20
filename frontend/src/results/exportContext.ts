import type { CadIdentityProvenance, JobItem } from '../api/jobsSocket';
import { hydrateJobDesign } from '../jobs/jobDesign';
import type { DesignDocument } from '../stores/design';
import { wgSolveSettingsFromSolveOptions } from '../stores/designWire';
import type { WgSolveSettings } from '../stores/wgSolveBlock';

/** Geometry/config exports for a result must use the design that produced it. */
export function resultExportSnapshot(
  job: (Pick<JobItem, 'script_snapshot' | 'design_revision'>
    & Partial<Pick<JobItem, 'solve_options' | 'cad_source'>>) | undefined,
): {
  design: DesignDocument | undefined;
  designRevision: number;
  polarConfig?: unknown;
  solveSettings?: WgSolveSettings | null;
  cadIdentity?: CadIdentityProvenance;
} {
  const polarConfig = job?.solve_options?.polar_config;
  return {
    design: job ? hydrateJobDesign(job) ?? undefined : undefined,
    designRevision: job?.design_revision ?? 0,
    ...(polarConfig && typeof polarConfig === 'object' ? { polarConfig } : {}),
    // The config exported for a run must describe that run, so its recorded
    // solve options are the source here -- never the draft on screen.
    solveSettings: wgSolveSettingsFromSolveOptions(job?.solve_options),
    ...(job?.cad_source?.identity ? { cadIdentity: job.cad_source.identity } : {}),
  };
}

import type { JobItem } from '../api/jobsSocket';
import { fetchJobResults } from '../api/results';
import type { ExportFormat, Preferences } from '../prefs/preferences';
import { resultExportSnapshot } from '../results/exportContext';
import { runAutomaticWorkspaceExport, type ExportBundleResult } from '../results/exporters';
import type { ResultPayload } from '../results/types';
import { autoExportSubdirectoryForJob, exportStemForJob } from './exportNaming';

export interface AutomaticRunExportOptions {
  preferences: Preferences;
  /** The fenced fetch of the automatic-export lane that owns this job. */
  fetcher: typeof fetch;
  /** The angle the polar outputs are referenced to, as on screen. */
  normalizationAngle?: number;
}

/**
 * One completed run's automatic export, as the jobs coordinator runs it.
 *
 * It writes into the run's `exports` folder, never the run folder the run
 * archive owns, and one Workspace write per format, so a refused format is
 * reported on its own while the others are still written.
 */
export async function exportRunAutomatically(
  job: JobItem,
  formats: readonly ExportFormat[],
  { preferences, fetcher, normalizationAngle }: AutomaticRunExportOptions,
): Promise<ExportBundleResult> {
  return runAutomaticWorkspaceExport({
    fetcher,
    result: await fetchJobResults(job.id, fetcher) as ResultPayload,
    ...resultExportSnapshot(job),
    jobId: job.id,
    jobStem: exportStemForJob(job),
    hasRadiationImpedanceArtifact: job.has_radiation_impedance_artifact,
    workspaceSubdirectory: autoExportSubdirectoryForJob(job),
    designName: job.label ?? undefined,
    normalizationAngle,
    preferences,
  }, formats);
}

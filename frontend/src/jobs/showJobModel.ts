import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { showCadJobModel } from '../shell/CadLinkCoordinator';
import { currentDesignLoadSource } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { canLoadJobDesign, replaceWithJobDesign } from './jobDesign';

/** Put the model represented by a history run back into its owning workspace. */
export async function showJobModel(
  job: JobItem,
  onError: (message: string) => void = () => undefined,
): Promise<boolean> {
  if (job.config_summary.geometry_type === 'imported') return showCadJobModel(job);
  if (!replaceWithJobDesign(job, { keepHistory: true })) {
    onError('This result has no readable design snapshot, so its model cannot be shown.');
    return false;
  }
  workspaceModeStore.setMode('parametric');
  return true;
}

/** The newest solved parametric run whose design can be put back on screen. */
export function latestSolvedParametricJob(jobs: JobItem[]): JobItem | null {
  const solvedAt = (job: JobItem) => Date.parse(job.completed_at ?? job.queued_at);
  return jobs
    .filter((job) => job.status === 'complete'
      && job.config_summary.geometry_type !== 'imported'
      && canLoadJobDesign(job))
    .reduce<JobItem | null>((latest, job) => (latest && solvedAt(latest) >= solvedAt(job) ? latest : job), null);
}

/**
 * Make a plain return to Parametric mode land on the user's parametric work.
 *
 * Opening a project from the CAD Link registry replaces the working design in
 * the store, so toggling back to Parametric would otherwise show that CAD
 * project's design as if it were the user's own. Restore the latest solved
 * parametric run's design instead — with history kept, so undo can still reach
 * the replaced document. When no CAD flow replaced the design (the common
 * case: CAD Link only showed an ingested mesh), the store already holds the
 * working design and nothing is touched.
 */
export function restoreParametricWorkingDesign(): void {
  if (currentDesignLoadSource() !== 'cad-project-switch') return;
  const job = latestSolvedParametricJob(jobsSocket.getSnapshot().jobs);
  if (job) replaceWithJobDesign(job, { keepHistory: true });
}

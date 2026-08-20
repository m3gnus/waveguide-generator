import type { JobItem } from '../api/jobsSocket';
import { showCadJobModel } from '../shell/CadLinkCoordinator';
import { workspaceModeStore } from '../stores/workspaceMode';
import { replaceWithJobDesign } from './jobDesign';

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

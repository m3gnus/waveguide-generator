import { showUnreadableRun } from '../model/currentModel';
import { type JobItem } from '../api/jobsSocket';
import { cadWorkspace } from '../stores/cadWorkspace';
import { workspaceModeStore } from '../stores/workspaceMode';
import { noteExplicitNavigation } from '../shell/workspaceNavigation';
import { replaceWithJobDesign } from './jobDesign';
import { openImported } from '../stores/importedModel';
// The rails a reopened imported run is shown in: the file and the CAD rail.
import '../stores/meshImport';
import '../shell/cadlink/restores';

/** Put the model represented by a history run back into its owning workspace. */
export async function showJobModel(
  job: JobItem,
  onError: (message: string) => void = () => undefined,
): Promise<boolean> {
  // Asked for from a run's own controls: the user is choosing what to look at.
  noteExplicitNavigation();
  if (job.config_summary.geometry_type === 'imported') {
    // A file or a CAD run: its model, prepared mesh and setup, from the server.
    try {
      return await openImported({ kind: 'run', job, display: {
        // Browsing history changes the model, while the user's panel stays open.
        enterCadWorkspace: () => workspaceModeStore.setMode('cad'),
        reportStatus: cadWorkspace.getSnapshot().reportStatus,
        reportViewportNotice: cadWorkspace.getSnapshot().reportViewportNotice,
      } });
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
      return false;
    }
  }
  if (!replaceWithJobDesign(job, { keepHistory: true })) {
    showUnreadableRun(job);
    onError('This result has no readable design snapshot, so its model cannot be shown.');
    return false;
  }
  workspaceModeStore.setMode('parametric');
  return true;
}

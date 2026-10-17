import type { JobItem } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { canOpenJobModel } from './jobDesign';
import { showJobModel } from './showJobModel';

/**
 * Selecting a run shows it: its results drive the charts and its design snapshot
 * goes back into the viewport. Both stores are set synchronously so the row
 * expands on the same frame as the click; the results payload is served from
 * the LRU cache and the geometry preview catches up behind it.
 *
 * Showing the model always goes through `showJobModel`, which also puts the
 * workspace into the run's owning mode — so selecting a parametric run from
 * CAD Link mode brings the parametric workspace back with it, exactly as an
 * imported run selected from parametric mode brings CAD Link back.
 */
export function selectJob(job: JobItem, onError?: (message: string) => void): void {
  // A finished run whose results were cleaned up is still the selected run: the
  // dock says so instead of leaving the previous run's charts under its card.
  if (job.has_results || job.status === 'complete') compareSelection.setPrimary(job.id);
  // Nothing readable to show for a cleaned-up run: select it, and stop there.
  if (job.status === 'complete' && !job.has_results && !canOpenJobModel(job)) return;
  // Undoable: browsing runs must not be able to discard the working design.
  if (job.config_summary.geometry_type !== 'imported' && !canOpenJobModel(job)) return;
  void showJobModel(job, onError);
}

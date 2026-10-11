import { isActiveJobStatus, type JobItem } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { selectJob } from './selectJob';

/** The run id a `?job=<id>` link names, or null when the URL carries none. */
export function jobIdFromLink(search: string): string | null {
  const id = new URLSearchParams(search).get('job')?.trim();
  return id ? id : null;
}

/**
 * Open the run a `?job=<id>` link names, once the jobs list is loaded.
 *
 * A finished run is opened exactly as a click on its card does (`selectJob`).
 * A run that is still going is selected the same way and, since it has no
 * results yet, claimed so its charts take the slot when it completes. An id
 * this data folder does not know is reported, not acted on.
 */
export function openLinkedJob(
  id: string,
  jobs: readonly JobItem[],
  report: (message: string) => void,
): void {
  const job = jobs.find((candidate) => candidate.id === id);
  if (!job) {
    report(`No run with id ${id} in this WG data folder.`);
    return;
  }
  selectJob(job);
  if (isActiveJobStatus(job.status)) compareSelection.awaitRun(job.id);
}

/** Drop `job` from the address bar so a reload does not open the run again. */
export function clearJobLink(): void {
  const url = new URL(window.location.href);
  if (!url.searchParams.has('job')) return;
  url.searchParams.delete('job');
  window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
}

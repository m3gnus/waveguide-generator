import { useMemo, useSyncExternalStore } from 'react';
import { jobsSocket, type JobItem, type JobsSnapshot } from './jobsSocket';

/**
 * Read one fact from the jobs snapshot, re-rendering only when that fact changes.
 *
 * `useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot)` hands a
 * component the whole snapshot, so every event of a running solve re-renders
 * it. This keeps the selection from the previous snapshot whenever `isEqual`
 * says the new one carries nothing different, and React then skips the render.
 * Pass module-level `selector` and `isEqual` functions: a new function on every
 * render starts a fresh comparison (still correct, only less selective).
 */
export function useJobsSelector<T>(
  selector: (snapshot: JobsSnapshot) => T,
  isEqual: (previous: T, next: T) => boolean = Object.is,
): T {
  const getSelection = useMemo(() => {
    let memo: { snapshot: JobsSnapshot; selection: T } | null = null;
    return () => {
      const snapshot = jobsSocket.getSnapshot();
      if (memo && memo.snapshot === snapshot) return memo.selection;
      const next = selector(snapshot);
      const selection = memo && isEqual(memo.selection, next) ? memo.selection : next;
      memo = { snapshot, selection };
      return selection;
    };
  }, [selector, isEqual]);
  return useSyncExternalStore(jobsSocket.subscribe, getSelection, getSelection);
}

/**
 * The fields a running solve rewrites on almost every event: `progress`,
 * `stage` and `log` messages change only these (`jobsSocket.applyDelta`).
 * Only views that draw a solve's progress read them -- the Jobs panel, the
 * current model's status line and `SolveProgressView` -- and those keep the
 * whole snapshot.
 */
export const JOB_PROGRESS_FIELDS: ReadonlySet<string> = new Set<keyof JobItem>([
  'progress', 'stage', 'stage_message', 'frequency_progress', 'log_tail',
]);

function sameJobApartFromProgress(a: JobItem, b: JobItem): boolean {
  if (a === b) return true;
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) {
    if (JOB_PROGRESS_FIELDS.has(key)) continue;
    if (!Object.is(a[key as keyof JobItem], b[key as keyof JobItem])) return false;
  }
  return true;
}

/** Same runs, in the same order, differing at most in their progress fields. */
export function sameJobsApartFromProgress(a: readonly JobItem[], b: readonly JobItem[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  return a.every((job, index) => sameJobApartFromProgress(job, b[index]));
}

const selectJobs = (snapshot: JobsSnapshot) => snapshot.jobs;
const selectEpoch = (snapshot: JobsSnapshot) => snapshot.epoch;
const selectJobsLoaded = (snapshot: JobsSnapshot) => snapshot.cursor !== null;

/**
 * The job list for views that do not draw a solve's progress.
 *
 * It changes when a run is added, removed or changes in anything but its
 * progress fields (status, results, metadata, CAD state...). While a solve
 * only advances, it keeps returning the list it last returned, so the running
 * job in it can hold an older `progress`, `stage`, `stage_message`,
 * `frequency_progress` or `log_tail`: never display those from this list.
 */
export function useJobsApartFromProgress(): JobItem[] {
  return useJobsSelector(selectJobs, sameJobsApartFromProgress);
}

/** The server epoch the job list belongs to. */
export function useJobsEpoch(): number | null {
  return useJobsSelector(selectEpoch);
}

/** Whether the job list has loaded at least once. */
export function useJobsLoaded(): boolean {
  return useJobsSelector(selectJobsLoaded);
}

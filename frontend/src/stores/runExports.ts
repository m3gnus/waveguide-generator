import { create } from 'zustand';
import type { ExportFormat } from '../prefs/preferences';

export interface RunExportJobState {
  busy: boolean;
  busyFormats: ExportFormat[];
  lastError: string | null;
  lastErrorFormats: ExportFormat[];
  lastNotice: string | null;
}

export interface RunExportOutcome {
  notice: string;
  error?: string | null;
  errorFormats?: ExportFormat[];
}

interface RunExportStore {
  jobs: Record<string, RunExportJobState>;
  execute(jobId: string, formats: ExportFormat[], task: () => Promise<RunExportOutcome>): Promise<RunExportOutcome>;
  clearFeedback(jobId: string): void;
  resetForTests(): void;
}

const pending = new Map<string, Promise<RunExportOutcome>>();
const noticeTimers = new Map<string, ReturnType<typeof setTimeout>>();

export const EMPTY_RUN_EXPORT_STATE: RunExportJobState = {
  busy: false,
  busyFormats: [],
  lastError: null,
  lastErrorFormats: [],
  lastNotice: null,
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function clearNoticeTimer(jobId: string): void {
  const timer = noticeTimers.get(jobId);
  if (timer !== undefined) clearTimeout(timer);
  noticeTimers.delete(jobId);
}

/**
 * Owns the promise as well as its visible state. A job card may disappear while
 * its download is being prepared; keeping both here prevents a remount from
 * losing the spinner or starting the same job export a second time.
 */
export const useRunExportStore = create<RunExportStore>((set) => ({
  jobs: {},
  execute(jobId, formats, task) {
    const existing = pending.get(jobId);
    if (existing) return existing;
    clearNoticeTimer(jobId);

    set((state) => ({
      jobs: {
        ...state.jobs,
        [jobId]: { busy: true, busyFormats: [...formats], lastError: null, lastErrorFormats: [], lastNotice: null },
      },
    }));

    let operation!: Promise<RunExportOutcome>;
    operation = (async () => {
      let outcome: RunExportOutcome;
      try {
        // Let `operation` be assigned before even a synchronously-throwing task
        // reaches the cleanup block below.
        await Promise.resolve();
        outcome = await task();
      } catch (error) {
        const reason = message(error);
        set((state) => ({
          jobs: {
            ...state.jobs,
            [jobId]: { busy: false, busyFormats: [], lastError: reason, lastErrorFormats: [...formats], lastNotice: null },
          },
        }));
        if (pending.get(jobId) === operation) pending.delete(jobId);
        throw error;
      }
      set((state) => ({
        jobs: {
          ...state.jobs,
          [jobId]: {
            busy: false,
            busyFormats: [],
            lastError: outcome.error ?? null,
            lastErrorFormats: outcome.errorFormats ?? [],
            lastNotice: outcome.notice,
          },
        },
      }));
      if (pending.get(jobId) === operation) pending.delete(jobId);
      if (outcome.error) throw new Error(outcome.error);
      const timer = setTimeout(() => {
        noticeTimers.delete(jobId);
        set((state) => {
          const current = state.jobs[jobId];
          if (!current || current.busy || current.lastNotice !== outcome.notice) return state;
          return { jobs: { ...state.jobs, [jobId]: { ...current, lastNotice: null } } };
        });
      }, 3_000);
      noticeTimers.set(jobId, timer);
      return outcome;
    })();
    pending.set(jobId, operation);
    return operation;
  },
  clearFeedback(jobId) {
    clearNoticeTimer(jobId);
    set((state) => {
      const current = state.jobs[jobId];
      if (!current) return state;
      return { jobs: { ...state.jobs, [jobId]: { ...current, lastError: null, lastErrorFormats: [], lastNotice: null } } };
    });
  },
  resetForTests() {
    pending.clear();
    noticeTimers.forEach((timer) => clearTimeout(timer));
    noticeTimers.clear();
    set({ jobs: {} });
  },
}));

/**
 * The one record of what WG is busy with, shown in the status bar
 * (`ActivityStatus.tsx`). Every operation that can take longer than one
 * second registers here instead of spinning on its own (the owner, 2026-11-03,
 * hornlab-policy `software/reference/product-decisions.md`): it says what is
 * happening, how long it has run, and either how far it is (when the work can
 * be counted) or how long the same operation usually takes on this machine.
 *
 * Solves keep their own fuller display in the Jobs rail; this is for the
 * waits around them -- building, exporting, importing, meshing.
 */

/** One name per kind of wait. Its recent durations are remembered under this
 * name, so a kind must mean the same work every time it is used. */
export type ActivityKind =
  | 'assembly-build'
  | 'solver-mesh'
  | 'mesh-count'
  | 'export'
  | 'send-to-cad'
  | 'cad-import'
  | 'design-open'
  | 'mesh-import'
  | 'engine-check'
  | 'recombine'
  | 'folder-open'
  | 'folder-select'
  | 'settings-read'
  | 'settings-write'
  | 'onshape-check'
  | 'diagnostics-bundle'
  | 'diagnostics-summary'
  | 'result-load'
  | 'preview';

export interface ActivityProgress {
  done: number;
  total: number;
}

export interface Activity {
  id: number;
  kind: ActivityKind;
  /** What is happening, in the user's words: "Building the mesh". */
  label: string;
  startedAt: number;
  /** Set only when the work can really be counted. */
  progress: ActivityProgress | null;
  /** When `progress` last gained, for the time-left estimate. */
  progressAt: number | null;
  /** Present when the operation can be stopped. */
  cancel: (() => void) | null;
  /** Background work the user did not ask for (a triangle count after an
   * edit) shows only once it has really run one second, never early. */
  background: boolean;
}

export interface ActivityHandle {
  /** A composed operation can become stoppable only during its cancellable step. */
  setCancel(cancel: (() => void) | null): void;
  progress(done: number, total: number): void;
  label(text: string): void;
  /** `completed` false (cancelled, failed) keeps the duration out of the
   * remembered history, so a quick failure never shortens "usually". */
  end(completed?: boolean): void;
}

export interface StartActivity {
  kind: ActivityKind;
  label: string;
  cancel?: () => void;
  background?: boolean;
  /** When the wait really began, if before this registration (epoch ms). */
  startedAt?: number;
}

/** Shown once it has run this long, or at once when it usually takes longer. */
export const ACTIVITY_SHOW_AFTER_MS = 1_000;

const HISTORY_KEY = 'wg.activity.durations';
const HISTORY_LENGTH = 5;

type History = Partial<Record<ActivityKind, number[]>>;

function readHistory(): History {
  try {
    const parsed = JSON.parse(globalThis.localStorage?.getItem(HISTORY_KEY) ?? '{}') as unknown;
    return parsed && typeof parsed === 'object' ? parsed as History : {};
  } catch {
    return {};
  }
}

function writeHistory(history: History): void {
  try {
    globalThis.localStorage?.setItem(HISTORY_KEY, JSON.stringify(history));
  } catch {
    // Storage can be full or blocked; the display still works without history.
  }
}

class ActivityStore {
  private activities: readonly Activity[] = [];
  private history: History = readHistory();
  private nextId = 1;
  private listeners = new Set<() => void>();
  private visibilityTimers = new Map<number, ReturnType<typeof setTimeout>>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  getSnapshot = (): readonly Activity[] => this.activities;

  /** The median of this machine's recent completed runs of a kind, in ms. */
  typicalMs(kind: ActivityKind): number | null {
    const runs = this.history[kind];
    if (!runs?.length) return null;
    const sorted = [...runs].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
  }

  start({ kind, label, cancel, background = false, startedAt = Date.now() }: StartActivity): ActivityHandle {
    const activity: Activity = {
      id: this.nextId++, kind, label, startedAt,
      progress: null, progressAt: null, cancel: cancel ?? null, background,
    };
    this.set([...this.activities, activity]);
    // Wake the status bar at this activity's boundary, even when the shared
    // elapsed-time clock ticks on a different offset. A new snapshot is needed
    // for useSyncExternalStore; the activity's data and start time stay intact.
    this.visibilityTimers.set(activity.id, setTimeout(() => {
      this.visibilityTimers.delete(activity.id);
      this.set([...this.activities]);
    }, Math.max(0, ACTIVITY_SHOW_AFTER_MS - (Date.now() - startedAt))));
    let ended = false;
    const patch = (change: Partial<Activity>) => {
      if (ended) return;
      this.set(this.activities.map((item) => item.id === activity.id ? { ...item, ...change } : item));
    };
    return {
      setCancel: (cancel) => patch({ cancel }),
      progress: (done, total) => {
        const current = this.activities.find((item) => item.id === activity.id);
        const gained = !current?.progress || done > current.progress.done;
        patch({ progress: { done, total }, ...(gained ? { progressAt: Date.now() } : {}) });
      },
      label: (text) => patch({ label: text }),
      end: (completed = true) => {
        if (ended) return;
        ended = true;
        clearTimeout(this.visibilityTimers.get(activity.id));
        this.visibilityTimers.delete(activity.id);
        if (completed) this.remember(kind, Date.now() - activity.startedAt);
        this.set(this.activities.filter((item) => item.id !== activity.id));
      },
    };
  }

  /** Run `work` as one activity; it ends however the promise settles. */
  async track<T>(start: StartActivity, work: (handle: ActivityHandle) => Promise<T>): Promise<T> {
    const handle = this.start(start);
    let completed = false;
    try {
      const result = await work(handle);
      completed = true;
      return result;
    } finally {
      handle.end(completed);
    }
  }

  /** Tests only. */
  reset(): void {
    this.visibilityTimers.forEach(clearTimeout);
    this.visibilityTimers.clear();
    this.history = {};
    this.set([]);
  }

  private remember(kind: ActivityKind, ms: number): void {
    this.history = { ...this.history, [kind]: [...(this.history[kind] ?? []), ms].slice(-HISTORY_LENGTH) };
    writeHistory(this.history);
  }

  private set(next: readonly Activity[]): void {
    this.activities = next;
    this.listeners.forEach((notify) => notify());
  }
}

export const activityStore = new ActivityStore();

/** Whether the status bar shows this activity yet. */
export function activityVisible(activity: Activity, now: number, typicalMs: number | null): boolean {
  const elapsed = now - activity.startedAt;
  if (elapsed >= ACTIVITY_SHOW_AFTER_MS) return true;
  return !activity.background && typicalMs !== null && typicalMs >= ACTIVITY_SHOW_AFTER_MS;
}

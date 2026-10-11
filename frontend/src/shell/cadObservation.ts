import type { CadOperationSummary } from '../api/cadOperations';
import type { CadCoordination } from '../api/cadCoordination';
import { isActiveJobStatus, jobsSocket, type JobItem } from '../api/jobsSocket';
import { useCadConnectionStore, type CadConnectionStore, type CadConnectionStatus } from '../stores/cadConnection';

// CAD observation and idle backoff cadences.
export const CAD_OBSERVATION_INTERVALS = {
  returnsMs: 2_500, returnsIdleMs: 30_000,
  fusionStatusMs: 2_500, fusionStatusIdleMs: 30_000, quietMs: 30_000,
} as const;
export const CAD_RECONNECT_RETRY_DELAYS_MS = [5_000, 30_000, 120_000] as const;

const PARKED = new Set(['needs_user_input', 'recovery_required']);
const FINISHED = new Set(['accepted', 'rejected', 'failed', 'cancelled', 'complete', 'error', 'superseded']);
export interface CadObservedRequest { id: string; state: string }
export interface CadObservationActivity {
  /** Only listing changes and deliberate CAD commands reset quiet cadence. */
  activityRevision?: number;
  cadApplication: string;
  cadFolderConfigured: boolean | null;
  coordination: CadCoordination;
  workspaceMode: string;
  /** Design/settings/selected-instance generation. Changing it fences old reads. */
  observationKey: string;
  /** Non-solve requests; solves are observed through the jobs snapshot. */
  requests: readonly CadObservedRequest[];
  /** Pull/return waiters that have no receipt yet. */
  awaitingReturn: boolean;
}

export interface CadObservationOptions {
  /** Route pushed request evidence to its command owner without another socket reader. */
  observeOperation?(operation: CadOperationSummary): void;
  getActivity(): CadObservationActivity;
  subscribeActivity(listener: () => void): () => void;
  readFusionStatus(): Promise<CadConnectionStatus>;
  /** Supplied by the return-discovery owner; no viewport or selection work here. */
  reloadReturns(): Promise<void>;
  /** Supplied by the receipt owner in S5-2. Reload the feed AND read each named
   * non-solve receipt by exact id (even when absent from the pending feed).
   * Return only ids whose outcomes were successfully reconciled. Throwing or
   * omitting an id leaves it retryable; listing absence never settles a waiter. */
  reloadReceipts(awaitedRequestIds: readonly string[]): Promise<ReadonlySet<string>>;
  manager?: Pick<typeof jobsSocket, 'getSnapshot' | 'subscribe' | 'subscribeCadOperations' | 'refresh'>;
  connection?: CadConnectionStore;
  now?: () => number;
}

function movingCadJob(job: JobItem): boolean {
  return Boolean(job.cad_state || job.cad_source || job.cad_intent || job.cad_provenance)
    && isActiveJobStatus(job.status) && !PARKED.has(job.cad_state?.state ?? '');
}
const pendingRequest = (request: CadObservedRequest) => !FINISHED.has(request.state);

/** This service consumes job/request activity; it neither starts the jobs socket nor
 * imports the operation store. The shell supplies receipt and return readers. */
export type CadObservationSubscription = (() => void) & { refresh(options?: { returns?: boolean }): void };

export function connectCadObservation(options: CadObservationOptions): CadObservationSubscription {
  const manager = options.manager ?? jobsSocket;
  const connection = options.connection ?? useCadConnectionStore;
  const now = options.now ?? Date.now;
  let active = true;
  let lastActivityAt = now();
  let activity = options.getActivity();
  let declaredInbox = connection.getState().fusionStatus?.addinInboxTransfer === true;
  let lastFusionState = connection.getState().fusionStatus?.state;
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const restarts = new Set<() => void>();
  const visible = () => document.visibilityState !== 'hidden';
  const working = () => options.getActivity().awaitingReturn
    || options.getActivity().requests.some((request) => pendingRequest(request) && !PARKED.has(request.state))
    || manager.getSnapshot().jobs.some(movingCadJob);
  let wasWorking = working();
  const restart = () => restarts.forEach((fn) => fn());
  const noteActivity = () => { lastActivityAt = now(); restart(); };
  const delay = (listing: boolean) => {
    const input = options.getActivity();
    if (input.cadApplication !== 'fusion360' || !visible()) return null;
    const base = listing ? CAD_OBSERVATION_INTERVALS.returnsMs : CAD_OBSERVATION_INTERVALS.fusionStatusMs;
    const idle = listing ? CAD_OBSERVATION_INTERVALS.returnsIdleMs : CAD_OBSERVATION_INTERVALS.fusionStatusIdleMs;
    if (input.coordination !== 'on' && !(listing && !declaredInbox)) return working() ? base : null;
    if (input.workspaceMode === 'cad' || connection.getState().fusionStatus?.processRunning || input.awaitingReturn) return base;
    if (input.cadFolderConfigured === false) return listing ? idle : null;
    return now() - lastActivityAt >= CAD_OBSERVATION_INTERVALS.quietMs ? idle : base;
  };
  const readStatus = () => {
    if (active && visible() && options.getActivity().cadApplication === 'fusion360') {
      return connection.read(options.readFusionStatus).then(() => undefined);
    }
    return Promise.resolve();
  };
  const readReturns = () => {
    if (active && visible() && options.getActivity().cadApplication === 'fusion360') {
      return options.reloadReturns().catch(() => undefined);
    }
    return Promise.resolve();
  };
  const poll = (listing: boolean, tick: () => void) => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const schedule = () => {
      const period = delay(listing);
      timer = period === null ? null : setTimeout(() => {
        timers.delete(timer!);
        timer = null;
        // Schedule before the read, so a slow response does not stretch cadence.
        schedule();
        tick();
      }, period);
      if (timer !== null) timers.add(timer);
    };
    restarts.add(() => {
      if (timer !== null) { clearTimeout(timer); timers.delete(timer); }
      timer = null;
      schedule();
    });
    schedule();
  };

  let recoveryGeneration = 0;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  const unresolved = new Set<string>();
  const clearRetry = () => {
    if (retryTimer !== null) clearTimeout(retryTimer);
    retryTimer = null;
  };
  const recover = (attempt = 0) => {
    if (!active) return;
    clearRetry();
    const generation = ++recoveryGeneration;
    // Capture before reloading: a pending feed can drop an outcome that ended
    // during the outage, but its exact awaited id remains owed until reconciled.
    options.getActivity().requests.filter(pendingRequest).forEach((request) => unresolved.add(request.id));
    const ids = [...unresolved];
    const reloadJobs = manager.refresh().then(() => {
      // The jobs client resolves refresh failures and reports them in its
      // snapshot; a resolved promise alone is not evidence of recovery.
      const error = manager.getSnapshot().error;
      if (error) throw new Error(error);
    });
    void Promise.allSettled([reloadJobs, options.reloadReceipts(ids)]).then(([jobs, receipts]) => {
      if (!active || generation !== recoveryGeneration) return;
      if (receipts.status === 'fulfilled') receipts.value.forEach((id) => unresolved.delete(id));
      const errors = [jobs, receipts].flatMap((result) => result.status === 'rejected' ? [String(result.reason)] : []);
      if (unresolved.size) errors.push('awaited CAD receipts could not all be reconciled');
      connection.setRecoveryError(errors.length ? `WG could not check CAD work after reconnect (${errors.join('; ')}). Refresh to try again.` : null);
      const wait = CAD_RECONNECT_RETRY_DELAYS_MS[attempt];
      if (errors.length && wait !== undefined) retryTimer = setTimeout(() => {
        retryTimer = null;
        if (manager.getSnapshot().connection === 'connected') recover(attempt + 1);
      }, wait);
    });
  };
  let firstWake = true;
  let firstResync = true;
  const startupKey = activity.observationKey;
  let startupReads: Promise<unknown> | null = null;
  let wakeScheduled = false;
  let wakeListing = false;
  const wake = (listing = true) => {
    noteActivity();
    wakeListing ||= listing;
    if (wakeScheduled) return;
    wakeScheduled = true;
    void Promise.resolve().then(() => {
      wakeScheduled = false;
      if (!active) return;
      const listingRequested = wakeListing; wakeListing = false;
      const reads = [listingRequested ? readReturns() : Promise.resolve(), readStatus()];
      if (firstWake) {
        firstWake = false;
        const batch = Promise.allSettled(reads);
        startupReads = batch;
        void batch.finally(() => { if (startupReads === batch) startupReads = null; });
      }
    });
  };
  const invalidateDeclaration = () => {
    declaredInbox = false;
    // Fence the previous session's read, but let its held report age by its TTL.
    connection.fence();
    restart();
  };
  const unsubscribeChannel = manager.subscribeCadOperations({
    operation: (operation) => options.observeOperation?.(operation),
    addinStatusChanged: () => { invalidateDeclaration(); wake(); },
    resync: () => {
      // The initial hello shares a still-running mount batch for this context.
      // Later reconnects always fence and read again.
      const coalesced = firstResync && startupReads !== null && options.getActivity().observationKey === startupKey;
      firstResync = false;
      if (!coalesced) { invalidateDeclaration(); wake(); }
      recover();
    },
  });
  const unsubscribeJobs = manager.subscribe(() => {
    const next = working();
    if (next !== wasWorking) { wasWorking = next; restart(); }
  });
  const unsubscribeActivity = options.subscribeActivity(() => {
    const next = options.getActivity();
    const cadActivityChanged = next.activityRevision !== activity.activityRevision;
    const applicationChanged = next.cadApplication !== activity.cadApplication;
    const contextChanged = next.observationKey !== activity.observationKey || applicationChanged;
    const folderChanged = next.cadFolderConfigured !== activity.cadFolderConfigured;
    const folderDiscovered = activity.cadFolderConfigured === null && folderChanged;
    const enteredCad = next.workspaceMode === 'cad' && activity.workspaceMode !== 'cad';
    const coordinationChanged = next.coordination !== activity.coordination;
    const pullChanged = next.awaitingReturn !== activity.awaitingReturn;
    const modeChanged = next.workspaceMode !== activity.workspaceMode;
    const nextWorking = working();
    const workChanged = nextWorking !== wasWorking;
    activity = next;
    wasWorking = nextWorking;
    if (cadActivityChanged) noteActivity();
    if (contextChanged) {
      // A design/instance change invalidates its status, not the add-in's
      // transport declaration. A stalled read must not restart legacy listing
      // clocks for an add-in already known to deliver through the inbox.
      connection.invalidate();
      if (applicationChanged) invalidateDeclaration();
      if (!(folderChanged || enteredCad || applicationChanged)) readStatus();
    }
    if ((folderChanged && !folderDiscovered) || enteredCad || applicationChanged) wake();
    else if (contextChanged || folderChanged || coordinationChanged || modeChanged || pullChanged || workChanged) restart();
  });
  const unsubscribeConnection = connection.subscribe((state) => {
    const next = state.fusionStatus;
    if (!next) return;
    const declares = next.addinInboxTransfer === true;
    if (declares !== declaredInbox) { declaredInbox = declares; restart(); }
    if (next.state !== lastFusionState) { lastFusionState = next.state; noteActivity(); }
  });
  const resume = () => {
    if (visible()) { invalidateDeclaration(); wake(); }
    else restart();
  };
  document.addEventListener('visibilitychange', resume);
  window.addEventListener('focus', resume);
  poll(true, readReturns);
  poll(false, readStatus);
  wake();
  if (manager.getSnapshot().connection === 'connected') recover();
  const stop = () => {
    active = false;
    recoveryGeneration += 1;
    clearRetry();
    timers.forEach(clearTimeout);
    timers.clear();
    restarts.clear();
    unsubscribeChannel(); unsubscribeJobs(); unsubscribeActivity(); unsubscribeConnection();
    document.removeEventListener('visibilitychange', resume);
    window.removeEventListener('focus', resume);
    connection.invalidate();
  };
  // The eventual Refresh command can retry a bounded failed recovery without
  // manufacturing a socket reconnect or forgetting its unresolved exact ids.
  return Object.assign(stop, { refresh(input?: { returns?: boolean }) {
    if (!active) return;
    invalidateDeclaration(); recover(); wake(input?.returns !== false);
  } });
}

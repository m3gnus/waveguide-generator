import type { CadOperationSummary } from '../api/cadOperations';
import type { CadCoordination } from '../api/cadCoordination';
import { isActiveJobStatus, jobsSocket, type JobItem } from '../api/jobsSocket';
import { useCadConnectionStore, type CadConnectionStore, type CadConnectionStatus } from '../stores/cadConnection';
import { cadStartupHello } from '../stores/cadStartupHello';

// CAD observation and idle backoff cadences.
export const CAD_OBSERVATION_INTERVALS = {
  returnsMs: 2_500, returnsIdleMs: 30_000,
  fusionStatusMs: 2_500, fusionStatusIdleMs: 30_000, quietMs: 30_000,
  /** A burst of design edits (a slider drag) reads Fusion status at its start and once after it settles. */
  contextReadMs: 300,
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
    // CAD mode keeps the base rate while Fusion may deliver. Once Fusion reports it
    // is closed, nothing can arrive until it starts again, which its add-in push,
    // focus or a quiet-rate listing notices; until then quiet cadence applies.
    const fusionClosed = connection.getState().fusionStatus?.state === 'closed';
    if ((input.workspaceMode === 'cad' && !fusionClosed)
      || connection.getState().fusionStatus?.processRunning || input.awaitingReturn) return base;
    if (input.cadFolderConfigured === false) return listing ? idle : null;
    return now() - lastActivityAt >= CAD_OBSERVATION_INTERVALS.quietMs ? idle : base;
  };
  /** Rejects when an attempted read published no report (failed or fenced). */
  const readStatusAnswered = () => {
    if (active && visible() && options.getActivity().cadApplication === 'fusion360') {
      return connection.read(options.readFusionStatus).then((status) => {
        if (!status) throw new Error('Fusion status read was not answered');
      });
    }
    return Promise.resolve();
  };
  const readStatus = () => readStatusAnswered().catch(() => undefined);
  // Each context change fences the previous read, so a burst needs only its
  // first read and one after the last change; the ones between are discarded.
  let contextRead: ReturnType<typeof setTimeout> | null = null;
  let contextReadOwed = false;
  const readStatusSettled = () => {
    if (contextRead !== null) {
      contextReadOwed = true;
      clearTimeout(contextRead); timers.delete(contextRead);
    } else void readStatus();
    const timer = setTimeout(() => {
      timers.delete(timer);
      contextRead = null;
      if (contextReadOwed) { contextReadOwed = false; readStatusSettled(); }
    }, CAD_OBSERVATION_INTERVALS.contextReadMs);
    contextRead = timer;
    timers.add(timer);
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
      connection.setRecoveryError(errors.length ? 'WG could not check CAD work after reconnecting. Refresh CAD Link to try again.' : null);
      const wait = CAD_RECONNECT_RETRY_DELAYS_MS[attempt];
      if (errors.length && wait !== undefined) retryTimer = setTimeout(() => {
        retryTimer = null;
        if (manager.getSnapshot().connection === 'connected') recover(attempt + 1);
      }, wait);
    });
  };
  const startupKey = activity.observationKey;
  const startup = cadStartupHello(manager);
  // The mount wake's reads start in a microtask; a hello before then shares them.
  let mountReads: ((reads: Promise<unknown>) => void) | null = null;
  startup.mountRead(new Promise((resolve) => { mountReads = resolve; }));
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
      const status = readStatusAnswered();
      const reads = Promise.all([listingRequested ? readReturns() : Promise.resolve(), status]);
      void reads.catch(() => undefined);
      mountReads?.(reads); mountReads = null;
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
      // The first hello of a socket still starting at mount is answered by the
      // mount batch for this context (running or answered): the socket sends its
      // own jobs snapshot after every hello, and nothing awaited can be owed yet.
      // Later reconnects always fence, read again and recover.
      const fresh = startup.resync();
      const coalesced = !fresh && options.getActivity().observationKey === startupKey;
      if (!coalesced) { invalidateDeclaration(); wake(); }
      if (!coalesced || unresolved.size || options.getActivity().requests.some(pendingRequest)) recover();
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
      if (!(folderChanged || enteredCad || applicationChanged)) readStatusSettled();
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

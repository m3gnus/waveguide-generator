import { create } from 'zustand';
import { isPendingCadOperation, listCadOperations, type CadDeliveryStatus, type CadInboxRefusal, type CadOperationSummary } from '../api/cadOperations';
import { jobsSocket, type JobsSocketManager } from '../api/jobsSocket';

/** How many refusals the CAD Link panel keeps, as the server does. */
const RECENT_REFUSALS = 20;

interface CadOperationsState {
  operations: Record<string, CadOperationSummary>;
  /** Refusals of taken inbox files with no operation row, newest first. */
  refusals: CadInboxRefusal[];
  /** Refusals the user has not had on screen yet. */
  unseenRefusals: number;
  recordRefusal: (refusal: CadInboxRefusal) => void;
  /** Merge the server's recent list (a page that connected late). */
  mergeRefusals: (refusals: CadInboxRefusal[]) => void;
  acknowledgeRefusals: () => void;
  /** The consumer's state as the server last pushed it, or null before any push. */
  deliveryStatus: CadDeliveryStatus | null;
  setDeliveryStatus: (status: CadDeliveryStatus) => void;
  /** Why the last reconnect recovery failed, or null. */
  recoveryError: string | null;
  setRecoveryError: (message: string | null) => void;
  /** Read the unfinished operations, which are authoritative. */
  load: (fetcher?: typeof fetch) => Promise<void>;
  /** Merge one `cadOperation` message; false when it is older than what is held. */
  apply: (operation: CadOperationSummary) => boolean;
}

/** Whether `incoming` describes the operation later than `held` does.
 *
 * The attempt generation decides first: a claim increments it, and every write
 * of an obsolete attempt is refused, so a lower generation is always older
 * whatever its clock says. Within one attempt the later write wins. */
function isNewer(incoming: CadOperationSummary, held: CadOperationSummary): boolean {
  if (incoming.attemptGeneration !== held.attemptGeneration) {
    return incoming.attemptGeneration > held.attemptGeneration;
  }
  const next = Date.parse(incoming.updatedAt ?? '');
  const current = Date.parse(held.updatedAt ?? '');
  if (Number.isNaN(next) || Number.isNaN(current)) return true;
  return next >= current;
}

/** Whether a listed copy replaces the one held. A listing was taken before
 * whatever has reached this client since, so it never undoes an operation's
 * end, and a tie keeps the copy held. */
function listingReplaces(listed: CadOperationSummary, held: CadOperationSummary): boolean {
  if (!isPendingCadOperation(held) && isPendingCadOperation(listed)) return false;
  if (listed.attemptGeneration !== held.attemptGeneration) {
    return listed.attemptGeneration > held.attemptGeneration;
  }
  const next = Date.parse(listed.updatedAt ?? '');
  const current = Date.parse(held.updatedAt ?? '');
  return !Number.isNaN(next) && !Number.isNaN(current) && next > current;
}

// Outside Zustand state: a load must not forget an operation a message
// reported while the listing was in flight, and that bookkeeping renders nothing.
let loadGeneration = 0;
const appliedDuringLoad = new Set<string>();

const refusalKey = (refusal: CadInboxRefusal) => `${refusal.at}|${refusal.file}|${refusal.operationId ?? ''}`;

export const useCadOperationsStore = create<CadOperationsState>((set, get) => ({
  operations: {},
  refusals: [],
  unseenRefusals: 0,
  deliveryStatus: null,
  setDeliveryStatus: (status) => set({ deliveryStatus: status }),
  recoveryError: null,
  setRecoveryError: (message) => { if (get().recoveryError !== message) set({ recoveryError: message }); },
  recordRefusal: (refusal) => {
    const held = get().refusals;
    if (held.some((item) => refusalKey(item) === refusalKey(refusal))) return;
    set({ refusals: [refusal, ...held].slice(0, RECENT_REFUSALS), unseenRefusals: get().unseenRefusals + 1 });
  },
  mergeRefusals: (refusals) => {
    const known = new Set(get().refusals.map(refusalKey));
    const fresh = refusals.filter((item) => !known.has(refusalKey(item)));
    if (!fresh.length) return;
    const merged = [...fresh, ...get().refusals].sort((a, b) => b.at.localeCompare(a.at)).slice(0, RECENT_REFUSALS);
    set({ refusals: merged, unseenRefusals: get().unseenRefusals + fresh.length });
  },
  acknowledgeRefusals: () => { if (get().unseenRefusals) set({ unseenRefusals: 0 }); },
  apply: (operation) => {
    appliedDuringLoad.add(operation.operationId);
    const held = get().operations[operation.operationId];
    if (held && !isNewer(operation, held)) return false;
    set({ operations: { ...get().operations, [operation.operationId]: operation } });
    return true;
  },
  load: async (fetcher = fetch) => {
    const generation = ++loadGeneration;
    appliedDuringLoad.clear();
    const listed = await listCadOperations({}, fetcher);
    if (generation !== loadGeneration) return;
    const next: Record<string, CadOperationSummary> = {};
    // A pending operation the listing leaves out finished while nobody was
    // listening, unless a message about it arrived after the listing was taken.
    Object.entries(get().operations).forEach(([operationId, operation]) => {
      if (!isPendingCadOperation(operation) || appliedDuringLoad.has(operationId)) next[operationId] = operation;
    });
    listed.forEach((operation) => {
      const held = next[operation.operationId];
      next[operation.operationId] = held && !listingReplaces(operation, held) ? held : operation;
    });
    set({ operations: next });
  },
}));

/** The operations still waiting or running, oldest first. */
export function pendingCadOperations(
  operations: Record<string, CadOperationSummary>,
): CadOperationSummary[] {
  return Object.values(operations)
    .filter(isPendingCadOperation)
    .sort((a, b) => (a.createdAt ?? '').localeCompare(b.createdAt ?? ''));
}

/** How far back a reconnect looks for Sends accepted while it was away, beyond
 * the previous connection's start: a margin for clocks, not a history. */
const RECONNECT_MARGIN_MS = 5_000;
/** How far back a page's first connection looks, when this tab has never
 * connected before: long enough for a WG that just started (its first pass
 * takes the Sends that waited while it was closed), short enough that a new
 * window does not replay the day. */
export const FIRST_CONNECTION_WINDOW_MS = 10 * 60_000;
const SEEN_SINCE_KEY = 'wg2.cad.sends.since.v1';
const DISPLAYED_KEY = 'wg2.cad.sends.displayed.v1';
const DISPLAYED_LIMIT = 100;

function sessionStore(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    return null;
  }
}

/**
 * The Sends this tab has already put on screen (or reported refused), kept
 * across reloads so a recovery after a reload never shows one twice.
 */
export const displayedSends = {
  has(operationId: string): boolean {
    try {
      const held = JSON.parse(sessionStore()?.getItem(DISPLAYED_KEY) ?? '[]') as unknown;
      return Array.isArray(held) && held.includes(operationId);
    } catch {
      return false;
    }
  },
  add(operationId: string): void {
    const storage = sessionStore();
    if (!storage) return;
    try {
      const held = JSON.parse(storage.getItem(DISPLAYED_KEY) ?? '[]') as unknown;
      const ids = Array.isArray(held) ? held.filter((id): id is string => typeof id === 'string' && id !== operationId) : [];
      storage.setItem(DISPLAYED_KEY, JSON.stringify([...ids, operationId].slice(-DISPLAYED_LIMIT)));
    } catch { /* a tab that cannot store keeps the in-memory record only */ }
  },
};

/**
 * What happened while this page was not listening (M1 transfer contract, C7
 * E3), read on every connection from the recent finished operations:
 *
 * - Sends accepted or refused since the last successful recovery. An accepted
 *   snapshot is finished, so the unfinished listing cannot hold it.
 * - The terminal outcome of exactly the operations this page was waiting for
 *   (pending in the store when the connection began): a solve that finished
 *   while disconnected still takes the result slot it was armed for, and one
 *   that was refused still says so. Unrelated history is not applied, so an old
 *   solve can never steal the slot.
 *
 * `awaited` is taken before the first await, before the reconnect's own
 * listing can forget those rows.
 */
export async function recoverMissedSnapshots(
  since: number,
  fetcher: typeof fetch = fetch,
  awaited: ReadonlySet<string> = new Set(pendingCadOperations(useCadOperationsStore.getState().operations).map((operation) => operation.operationId)),
): Promise<void> {
  const recent = await listCadOperations({ pending: false, limit: 50 }, fetcher);
  recent
    .filter((operation) => (
      awaited.has(operation.operationId)
      || (operation.kind === 'receive_snapshot'
        && (operation.state === 'accepted' || operation.state === 'rejected')
        && Date.parse(operation.updatedAt ?? '') >= since - RECONNECT_MARGIN_MS)
    ))
    .forEach((operation) => { useCadOperationsStore.getState().apply(operation); });
}

/** Retries of a recovery that failed, and how long each waits. Bounded: a
 * failure that persists is left visible instead of retried for ever. */
export const RECOVERY_RETRY_DELAYS_MS = [5_000, 30_000, 120_000];

/** Feed the store from the jobs channel, reading the list again on every connection. */
export function connectCadOperations(
  manager: JobsSocketManager = jobsSocket,
  now: () => number = Date.now,
): () => void {
  // The boundary of the last recovery that succeeded: only a success moves it,
  // and only the newest recovery may (review F2).
  let recoveredUpTo: number | null = null;
  let recoveryGeneration = 0;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  const clearRetry = () => {
    if (retryTimer !== null) clearTimeout(retryTimer);
    retryTimer = null;
  };
  const recover = (attempt: number) => {
    clearRetry();
    const generation = ++recoveryGeneration;
    const storage = sessionStore();
    const stored = Number(storage?.getItem(SEEN_SINCE_KEY) ?? Number.NaN);
    const at = now();
    const since = recoveredUpTo
      ?? (Number.isFinite(stored) && stored > 0 ? stored : at - FIRST_CONNECTION_WINDOW_MS);
    const awaited = new Set(pendingCadOperations(useCadOperationsStore.getState().operations).map((operation) => operation.operationId));
    void recoverMissedSnapshots(since, fetch, awaited).then(() => {
      if (generation !== recoveryGeneration) return;
      recoveredUpTo = Math.max(recoveredUpTo ?? 0, at);
      try { storage?.setItem(SEEN_SINCE_KEY, String(recoveredUpTo)); } catch { /* in memory only */ }
      useCadOperationsStore.getState().setRecoveryError(null);
    }, (reason: unknown) => {
      if (generation !== recoveryGeneration) return;
      // The boundary stays where the last success left it, so the next
      // recovery still covers what this one could not read.
      const delay = RECOVERY_RETRY_DELAYS_MS[attempt];
      useCadOperationsStore.getState().setRecoveryError(
        `WG could not check what arrived from Fusion while it was disconnected (${reason instanceof Error ? reason.message : String(reason)}). `
        + (delay !== undefined ? 'It will try again shortly.' : 'It will try again when it reconnects; Refresh CAD Link to look now.'),
      );
      if (delay !== undefined) retryTimer = setTimeout(() => recover(attempt + 1), delay);
    });
  };
  const unsubscribe = manager.subscribeCadOperations({
    operation: (operation) => { useCadOperationsStore.getState().apply(operation); },
    refusal: (refusal) => { useCadOperationsStore.getState().recordRefusal(refusal); },
    deliveryStatus: (status) => { useCadOperationsStore.getState().setDeliveryStatus(status); },
    resync: () => {
      // Every connection, the first included: a Send accepted before this page
      // first connected (WG's start-up pass, a reload) was pushed to nobody.
      // Since the last successful recovery -- kept across reloads -- or, for a
      // tab that never recovered, a bounded window. What this tab already
      // displayed is recorded (displayedSends), so nothing is shown twice.
      recover(0);
      void useCadOperationsStore.getState().load().catch(() => undefined);
    },
  });
  return () => {
    clearRetry();
    unsubscribe();
  };
}

export function resetCadOperationsStore(): void {
  loadGeneration += 1;
  appliedDuringLoad.clear();
  useCadOperationsStore.setState({ operations: {}, refusals: [], unseenRefusals: 0, deliveryStatus: null, recoveryError: null });
}

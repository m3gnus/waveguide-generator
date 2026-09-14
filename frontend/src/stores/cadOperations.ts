import { create } from 'zustand';
import { isPendingCadOperation, listCadOperations, type CadOperationSummary } from '../api/cadOperations';
import { jobsSocket, type JobsSocketManager } from '../api/jobsSocket';

interface CadOperationsState {
  operations: Record<string, CadOperationSummary>;
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

export const useCadOperationsStore = create<CadOperationsState>((set, get) => ({
  operations: {},
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

/** Feed the store from the jobs channel, reading the list again on every connection. */
export function connectCadOperations(manager: JobsSocketManager = jobsSocket): () => void {
  return manager.subscribeCadOperations({
    operation: (operation) => { useCadOperationsStore.getState().apply(operation); },
    resync: () => { void useCadOperationsStore.getState().load().catch(() => undefined); },
  });
}

export function resetCadOperationsStore(): void {
  loadGeneration += 1;
  appliedDuringLoad.clear();
  useCadOperationsStore.setState({ operations: {} });
}

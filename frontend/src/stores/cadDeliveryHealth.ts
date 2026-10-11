import { create } from 'zustand';
import { getDeliveryStatus, listCadOperations, type CadDeliveryStatus, type CadInboxRefusal, type CadOperationSummary } from '../api/cadOperations';
import { jobsSocket } from '../api/jobsSocket';

export const WAITING_READ_MS = 5_000;
const refusalKey = (item: CadInboxRefusal) => `${item.at}|${item.file}|${item.operationId ?? ''}`;

/** Delivery diagnostics belong to the consumer projection, independently of the operation UI. */
export const useCadDeliveryHealthStore = create<{
  status: CadDeliveryStatus | null;
  refusals: CadInboxRefusal[];
  unseenRefusals: number;
  waiting: boolean;
  recoveryError: string | null;
  setStatus: (status: CadDeliveryStatus) => void;
  mergeRefusals: (refusals: CadInboxRefusal[]) => void;
  acknowledgeRefusals: () => void;
}>((set, get) => ({
  status: null, refusals: [], unseenRefusals: 0, waiting: false, recoveryError: null,
  setStatus: (status) => { set({ status }); get().mergeRefusals(status.recentRefusals ?? []); },
  mergeRefusals: (refusals) => {
    const known = new Set(get().refusals.map(refusalKey));
    const fresh = refusals.filter((item) => !known.has(refusalKey(item)));
    if (!fresh.length) return;
    set({
      refusals: [...fresh, ...get().refusals].sort((a, b) => b.at.localeCompare(a.at)).slice(0, 20),
      unseenRefusals: get().unseenRefusals + fresh.length,
    });
  },
  acknowledgeRefusals: () => { if (get().unseenRefusals) set({ unseenRefusals: 0 }); },
}));

let refresh: (() => void) | null = null;
export function refreshCadDeliveryHealth(): void { refresh?.(); }

/** One shell-owned reader. Idle health is pushed; only received requests need a clock. */
export function connectCadDeliveryHealth(): () => void {
  let active = true;
  let loadRevision = 0;
  const changedDuringLoad = new Set<string>();
  let reading = false;
  let statusRevision = 0;
  // Only intake state is retained here; operation cards and recovery stay with their owners.
  const intake = new Map<string, Pick<CadOperationSummary, 'state' | 'attemptGeneration' | 'updatedAt'>>();
  const apply = (operation: CadOperationSummary, listed = false) => {
    const held = intake.get(operation.operationId);
    if (listed && held && held.state !== 'received' && operation.state === 'received'
      && held.attemptGeneration >= operation.attemptGeneration) return;
    if (held && (operation.attemptGeneration < held.attemptGeneration
      || (operation.attemptGeneration === held.attemptGeneration
        && Date.parse(operation.updatedAt ?? '') < Date.parse(held.updatedAt ?? '')))) return;
    intake.set(operation.operationId, {
      state: operation.state, attemptGeneration: operation.attemptGeneration, updatedAt: operation.updatedAt,
    });
  };
  let timer: ReturnType<typeof setInterval> | null = null;
  const read = () => {
    if (reading) return;
    reading = true;
    const started = statusRevision;
    void getDeliveryStatus().then((status) => {
      // A push received during this read is newer than its snapshot.
      if (active && started === statusRevision) useCadDeliveryHealthStore.getState().setStatus(status);
    }).catch(() => undefined).finally(() => { reading = false; });
  };
  const updateWaiting = () => {
    const waiting = [...intake.values()].some((item) => item.state === 'received');
    if (waiting === useCadDeliveryHealthStore.getState().waiting) return;
    useCadDeliveryHealthStore.setState({ waiting });
    if (timer !== null) clearInterval(timer);
    timer = waiting ? setInterval(read, WAITING_READ_MS) : null;
    read();
  };
  const loadWaiting = () => {
    const started = ++loadRevision;
    changedDuringLoad.clear();
    void listCadOperations().then((operations) => {
      if (!active || started !== loadRevision) return;
      const listedIds = new Set(operations.map((item) => item.operationId));
      for (const id of intake.keys()) {
        if (!listedIds.has(id) && !changedDuringLoad.has(id)) intake.delete(id);
      }
      operations.forEach((item) => {
        if (!changedDuringLoad.has(item.operationId)) apply(item, true);
      });
      updateWaiting();
    }).catch(() => undefined);
  };
  const unsubscribe = jobsSocket.subscribeCadOperations({
    operation: (operation: CadOperationSummary) => {
      changedDuringLoad.add(operation.operationId);
      apply(operation);
      updateWaiting();
    },
    refusal: (refusal) => { useCadDeliveryHealthStore.getState().mergeRefusals([refusal]); read(); },
    deliveryStatus: (status) => { statusRevision += 1; useCadDeliveryHealthStore.getState().setStatus(status); },
    resync: () => { loadWaiting(); read(); },
  });
  refresh = read;
  read();
  loadWaiting();
  return () => {
    active = false;
    refresh = null;
    unsubscribe();
    if (timer !== null) clearInterval(timer);
    useCadDeliveryHealthStore.setState({ waiting: false });
  };
}

export function resetCadDeliveryHealthStore(): void {
  useCadDeliveryHealthStore.setState({ status: null, refusals: [], unseenRefusals: 0, waiting: false, recoveryError: null });
}

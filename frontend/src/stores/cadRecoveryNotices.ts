import { createStore, type StoreApi } from 'zustand/vanilla';
import { useStore } from 'zustand';
import type { components } from '../api/generated/openapi';
import type { CadOperationSummary } from '../api/cadOperations';
import { isPendingCadOperation } from '../api/cadOperations';
import { jsonRequest, type CadReturnIngestRecord } from '../api/cadlink';
import { jobsSocket, type JobItem, type JobsSocketManager } from '../api/jobsSocket';
import { cadJobSummary, latestCadJobs, dismissCadJob } from '../jobs/cadSolve';
import { useCadDeliveryHealthStore } from './cadDeliveryHealth';
import { useCadConnectionStore, type CadConnectionStore } from './cadConnection';
import { claimCadRecoveryError } from './cadOperations';

export type CadRecoveryNotice = components['schemas']['RecoveryNotice'];
type NoticePage = components['schemas']['RecoveryNoticesResponse'];
export interface CadRecoveryNoticesState {
  notices: Record<string, CadRecoveryNotice>;
  recoveryError: string | null;
  load(fetcher?: typeof fetch): Promise<void>;
  dismiss(id: string, fetcher?: typeof fetch): Promise<void>;
  setRecoveryError(message: string | null): void;
}
export type CadRecoveryNoticesStore = StoreApi<CadRecoveryNoticesState>;

/** HTTP is authoritative across reloads. A local tombstone fences listings
 * taken before a dismissal; the server persists the same choice in cadlink.db. */
export function createCadRecoveryNoticesStore(): CadRecoveryNoticesStore {
  let generation = 0;
  const dismissed = new Set<string>();
  const pending = new Map<string, Promise<void>>();
  const store = createStore<CadRecoveryNoticesState>((set, get) => ({
    notices: {}, recoveryError: null,
    setRecoveryError(message) {
      set({ recoveryError: message });
      useCadDeliveryHealthStore.setState({ recoveryError: message });
    },
    async load(fetcher = fetch) {
      const started = ++generation;
      try {
        const page = await jsonRequest<NoticePage>('/api/cadlink/recovery-notices', {}, fetcher);
        if (started !== generation) return;
        set({ notices: Object.fromEntries(page.notices.filter((notice) => !dismissed.has(notice.id)).map((notice) => [notice.id, notice])) });
        get().setRecoveryError(null);
      } catch (error) {
        if (started !== generation) return;
        get().setRecoveryError(`WG could not check interrupted Fusion requests (${error instanceof Error ? error.message : String(error)}). Refresh CAD Link to try again.`);
        throw error;
      }
    },
    dismiss(id, fetcher = fetch) {
      const held = pending.get(id);
      if (held) return held;
      if (dismissed.has(id)) return Promise.resolve();
      const request = (async () => {
        try {
          await jsonRequest(`/api/cadlink/recovery-notices/${encodeURIComponent(id)}/dismiss`, { method: 'POST' }, fetcher);
          dismissed.add(id);
          const notices = { ...get().notices };
          delete notices[id];
          set({ notices });
          get().setRecoveryError(null);
        } catch (error) {
          get().setRecoveryError(`WG could not dismiss this recovery notice (${error instanceof Error ? error.message : String(error)}).`);
          throw error;
        } finally { pending.delete(id); }
      })();
      pending.set(id, request);
      return request;
    },
  }));
  return store;
}

export const cadRecoveryNoticesStore = createCadRecoveryNoticesStore();
export function useCadRecoveryNotices<T>(selector: (state: CadRecoveryNoticesState) => T): T {
  return useStore(cadRecoveryNoticesStore, selector);
}

/** Built beside the coordinator. S5-7 mounts this on the existing jobs channel;
 * pushes wake an authoritative read, never manufacture a recovery outcome. */
export function connectCadRecoveryNotices(
  manager: Pick<JobsSocketManager, 'subscribeCadOperations'> = jobsSocket,
  store = cadRecoveryNoticesStore,
  fetcher: typeof fetch = fetch,
  connection: Pick<CadConnectionStore, 'subscribe'> = useCadConnectionStore,
): () => void {
  const releaseRecoveryError = claimCadRecoveryError();
  useCadDeliveryHealthStore.setState({ recoveryError: store.getState().recoveryError });
  let active = true;
  let reading = false;
  let again = false;
  const read = () => {
    if (!active) return;
    if (reading) { again = true; return; }
    reading = true;
    void store.getState().load(fetcher).catch(() => undefined).finally(() => {
      reading = false;
      if (again) { again = false; read(); }
    });
  };
  const unsubscribe = manager.subscribeCadOperations({
    operation: (operation) => { if (operation.kind === 'insert_link' || operation.kind === 'update_link') read(); },
    resync: read,
    addinStatusChanged: read,
  });
  // The existing connection owner ages its held report once at expiry. This
  // also wakes document/evidence changes without introducing an idle poller.
  const stopConnection = connection.subscribe((state, previous) => {
    if (state.fusionStatus !== previous.fusionStatus) read();
  });
  read();
  return () => {
    if (!active) return;
    active = false; unsubscribe(); stopConnection(); releaseRecoveryError();
  };
}

export type CadRequest = {
  id: string;
  operation: CadOperationSummary;
  onScreen: boolean;
  openable: boolean;
} & ({ source: 'job'; job: JobItem; notice?: never }
  | { source: 'recovery'; notice?: CadRecoveryNotice; job?: never });

function recoverySummary(notice: CadRecoveryNotice): CadOperationSummary {
  return {
    operationId: notice.id, kind: notice.kind, state: 'recovery_required',
    stage: null, reason: null, message: null, jobId: null, attemptGeneration: 0,
    preparationId: null, setupRevisionId: null, legacy: false,
    createdAt: notice.created_at, updatedAt: notice.created_at, acceptedSeq: notice.sequence,
    snapshot: { manifestSha256: null, documentName: notice.document_name },
  };
}

/** One request view joins jobs and recovery notices; it has no coordinator
 * dependency. Legacy inputs preserve today's section until the S5-7 cut-over. */
export function selectCadRequests({ jobs, notices, record, recoveryOperations = {}, reportedRecovery = null }: {
  jobs: JobItem[];
  notices?: Record<string, CadRecoveryNotice>;
  record: CadReturnIngestRecord | null;
  recoveryOperations?: Record<string, CadOperationSummary>;
  reportedRecovery?: string | null;
}): { current: CadRequest[]; earlier: CadRequest[] } {
  const requests: CadRequest[] = latestCadJobs(jobs).flatMap((job) => {
    const operation = cadJobSummary(job);
    if (!isPendingCadOperation(operation)) return [];
    return [{ id: job.id, source: 'job' as const, job, operation,
      onScreen: Boolean(record && operation.snapshot?.manifestSha256 && record.manifest_sha256 === operation.snapshot.manifestSha256),
      openable: operation.state === 'needs_user_input' && operation.reason === 'setup_required' && Boolean(operation.snapshot?.projectLineageId) }];
  });
  const recovery = new Map<string, CadRequest>();
  // A supplied notice listing is authoritative, including an empty listing.
  // Never fill its settled/dismissed absences from transitional operations.
  if (notices === undefined) Object.values(recoveryOperations).forEach((operation) => {
    if (operation.state === 'recovery_required' && ['insert_link', 'update_link'].includes(operation.kind)) {
      recovery.set(operation.operationId, { id: operation.operationId, source: 'recovery', operation,
        onScreen: operation.operationId === reportedRecovery, openable: false });
    }
  });
  Object.values(notices ?? {}).forEach((notice) => recovery.set(notice.id, {
    id: notice.id, source: 'recovery', notice, operation: recoverySummary(notice), onScreen: notice.active_document, openable: false,
  }));
  // Preserve the parent's stable input order on timestamp ties: operations
  // (now notices) preceded jobs, and each owner's order stays intact.
  requests.unshift(...recovery.values());
  requests.sort((a, b) => (a.operation.createdAt ?? '').localeCompare(b.operation.createdAt ?? ''));
  return { current: requests.filter((item) => item.onScreen), earlier: requests.filter((item) => !item.onScreen) };
}

/** Capture named requests at the press. Late binding never redirects a dismiss
 * to another job, and overlapping Clear all / Dismiss share one request. */
export function createCadRequestActions({ recovery = cadRecoveryNoticesStore, dismissJob = dismissCadJob, openProject }: {
  recovery?: CadRecoveryNoticesStore;
  dismissJob?: (job: JobItem) => Promise<void>;
  /** The shell supplies its guarded project opener (discard and selection fences). */
  openProject: (lineageId: string) => Promise<void>;
}) {
  const pending = new Map<string, Promise<void>>();
  const completed = new Set<string>();
  let opening: Promise<void> | null = null;
  const dismiss = (request: CadRequest): Promise<void> => {
    const key = `${request.source}:${request.id}`;
    if (completed.has(key)) return Promise.resolve();
    const held = pending.get(key);
    if (held) return held;
    const action = Promise.resolve().then(() => request.source === 'job'
      ? dismissJob(request.job) : recovery.getState().dismiss(request.id)).then(() => { completed.add(key); })
      .finally(() => { pending.delete(key); });
    pending.set(key, action);
    return action;
  };
  return {
    dismiss,
    async clearAll(requests: readonly CadRequest[]) {
      const results = await Promise.allSettled(requests.filter((item) => item.operation.state !== 'cancel_requested').map(dismiss));
      const failed = results.find((result) => result.status === 'rejected');
      if (failed?.status === 'rejected') throw failed.reason;
    },
    open(request: CadRequest): Promise<void> {
      const lineage = request.operation.snapshot?.projectLineageId;
      if (!request.openable || !lineage) return Promise.resolve();
      if (opening) return opening;
      opening = Promise.resolve().then(() => openProject(lineage)).finally(() => { opening = null; });
      return opening;
    },
  };
}

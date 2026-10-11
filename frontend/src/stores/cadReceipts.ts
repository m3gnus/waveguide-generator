import { createStore, type StoreApi } from 'zustand/vanilla';
import { useStore } from 'zustand';
import { jobsSocket, type JobsSocketManager } from '../api/jobsSocket';
import type { CadInboxRefusal } from '../api/cadOperations';
import { useCadDeliveryHealthStore } from './cadDeliveryHealth';

export type ReceiptOutcome = 'accepted' | 'refused' | 'missing_source' | 'unreadable' | 'retained_copy';
export interface CadReceipt {
  receipt_id: string;
  send_id: string | null;
  /** Request acceptance order, independent of late terminal settlement. */
  send_sequence: number | null;
  project_id: string | null;
  design_ids: string[];
  ingest_id: string | null;
  outcome: ReceiptOutcome;
  reason: string | null;
  message: string | null;
  created_at: string;
  sent_at: string;
  cursor: number;
  bundle_path: string | null;
  manifest_sha256: string | null;
  document_name: string | null;
  retained_copy: boolean;
  refusal: CadInboxRefusal | null;
}
interface ReceiptPage { feed_id: string; receipts: CadReceipt[]; cursor: number; has_more: boolean }
export interface ReceiptFeedback { outcome: ReceiptOutcome | 'display_failed' | 'superseded'; message: string }
export interface CadReceiptsState {
  feedId: string | null;
  cursor: number;
  since: number;
  newestSequence: number;
  newestSentAt: number;
  intakeRefusals: CadInboxRefusal[];
  displayRetries: Record<string, { attempt: number; nextAt: number }>;
  retryDisplay(id: string): void;
  receipts: Record<string, CadReceipt>;
  fronted: string[];
  feedback: Record<string, ReceiptFeedback>;
  recoveryError: string | null;
  merge(page: ReceiptPage): void;
  markFronted(id: string, receipt?: CadReceipt): void;
  report(id: string, feedback: ReceiptFeedback): void;
  load(fetcher?: typeof fetch): Promise<void>;
}
export type CadReceiptsStore = StoreApi<CadReceiptsState>;
const KEY = 'wg2.cad.receipts.v2';
const HISTORY_LIMIT = 20;
export const RECEIPT_FIRST_CONNECTION_MS = 10 * 60_000;
const RETRY_MS = [5_000, 30_000, 120_000];
function windowStorage(): Storage | null {
  try { return typeof sessionStorage === 'undefined' ? null : sessionStorage; } catch { return null; }
}
function isReceipt(value: unknown): value is CadReceipt {
  if (!value || typeof value !== 'object') return false;
  const item = value as CadReceipt;
  return typeof item.receipt_id === 'string' && item.receipt_id.length > 0
    && Number.isSafeInteger(item.cursor) && item.cursor > 0
    && ['accepted', 'refused', 'missing_source', 'unreadable', 'retained_copy'].includes(item.outcome)
    && (item.send_sequence === null || (Number.isSafeInteger(item.send_sequence) && Number(item.send_sequence) > 0))
    && typeof item.created_at === 'string' && Number.isFinite(Date.parse(item.created_at))
    && typeof item.sent_at === 'string' && Number.isFinite(Date.parse(item.sent_at))
    && Array.isArray(item.design_ids) && item.design_ids.every((id) => typeof id === 'string')
    && (item.bundle_path === null || typeof item.bundle_path === 'string')
    && (item.manifest_sha256 === null || typeof item.manifest_sha256 === 'string')
    && (item.project_id === null || typeof item.project_id === 'string')
    && [item.send_id, item.ingest_id, item.reason, item.message, item.document_name]
      .every((value) => value === null || typeof value === 'string')
    && typeof item.retained_copy === 'boolean'
    && (item.refusal === null || (typeof item.refusal?.at === 'string' && Number.isFinite(Date.parse(item.refusal.at))
      && (item.refusal.operationId === null || typeof item.refusal.operationId === 'string')
      && typeof item.refusal.file === 'string' && typeof item.refusal.reason === 'string'));
}

/** A factory makes window/reload boundaries explicit without mounting the coordinator.
 * Cursor and pending receipts are persisted together: a fetched but not yet
 * displayed receipt must survive a reload too. Fronted state is per window.
 */
export function createCadReceiptsStore(storage = windowStorage(), now = Date.now): CadReceiptsStore {
  const restore = (feedId: string | null): Partial<CadReceiptsState> => {
    try {
      const saved = JSON.parse(storage?.getItem(`${KEY}:${feedId ?? 'pending'}`) ?? 'null') as CadReceiptsState | null;
      if (saved && Number.isSafeInteger(saved.cursor) && saved.cursor >= 0
        && Number.isFinite(saved.since) && Array.isArray(saved.fronted)
        && saved.fronted.every((id) => typeof id === 'string')
        && saved.receipts && Object.values(saved.receipts).every(isReceipt)) return saved;
    } catch { /* Invalid or unavailable storage starts fresh. */ }
    return {};
  };
  let feedId: string | null = null;
  try { feedId = storage?.getItem(`${KEY}:active`) ?? null; } catch { /* unavailable */ }
  const initial = (id: string | null) => {
    const saved = restore(id);
    return { feedId: id, cursor: saved.cursor ?? 0, since: saved.since ?? now() - RECEIPT_FIRST_CONNECTION_MS,
      receipts: saved.receipts ?? {}, fronted: saved.fronted ?? [], feedback: saved.feedback ?? {},
      newestSequence: saved.newestSequence ?? 0, newestSentAt: saved.newestSentAt ?? 0,
      intakeRefusals: saved.intakeRefusals ?? [], displayRetries: saved.displayRetries ?? {}, recoveryError: null };
  };
  let loading: Promise<void> | null = null;
  const store = createStore<CadReceiptsState>((set, get) => ({
    ...initial(feedId),
    merge: (page) => {
      if (get().feedId && get().feedId !== page.feed_id) set(initial(page.feed_id));
      const state = get();
      const receipts = { ...state.receipts };
      page.receipts.forEach((item) => {
        // Ignore overlaps already completed; pending rows can refresh source evidence.
        if ((item.cursor > state.cursor || receipts[item.receipt_id])
          && !state.fronted.includes(item.receipt_id)
          && (item.refusal || Date.parse(item.created_at) >= state.since)) receipts[item.receipt_id] = item;
      });
      const refusals = new Map(state.intakeRefusals.map((item) => [`${item.at}|${item.file}|${item.operationId ?? ''}`, item]));
      page.receipts.forEach((item) => { if (item.refusal) {
        const refusal = item.refusal;
        refusals.set(`${refusal.at}|${refusal.file}|${refusal.operationId ?? ''}`, refusal);
      } });
      set({ feedId: page.feed_id, receipts, cursor: Math.max(state.cursor, page.cursor), recoveryError: null,
        intakeRefusals: [...refusals.values()].sort((a, b) => b.at.localeCompare(a.at)).slice(0, HISTORY_LIMIT) });
    },
    markFronted: (id, supplied) => {
      const state = get();
      const item = state.receipts[id] ?? supplied;
      const receipts = { ...state.receipts };
      const displayRetries = { ...state.displayRetries };
      delete displayRetries[id];
      const fronted = [...new Set([...state.fronted, id])].filter((key) => {
        if ((receipts[key] && receipts[key].cursor <= state.cursor)
          || (key === id && item && item.cursor <= state.cursor)) { delete receipts[key]; return false; }
        return true;
      }).slice(-HISTORY_LIMIT);
      set({ receipts, fronted, displayRetries,
        newestSequence: item && item.outcome !== 'refused' ? Math.max(state.newestSequence, item.send_sequence ?? 0) : state.newestSequence,
        newestSentAt: item && item.outcome !== 'refused' ? Math.max(state.newestSentAt, Date.parse(item.sent_at)) : state.newestSentAt });
    },
    retryDisplay: (id) => {
      const attempt = (get().displayRetries[id]?.attempt ?? 0) + 1;
      if (attempt > RETRY_MS.length) {
        get().report(id, { outcome: 'display_failed', message: 'WG could not open this Send after repeated transport failures. Send it again from Fusion.' });
        get().markFronted(id);
      } else set({ displayRetries: { ...get().displayRetries, [id]: { attempt, nextAt: Date.now() + RETRY_MS[attempt - 1] } } });
    },
    report: (id, feedback) => {
      const entries = Object.entries(get().feedback).filter(([key]) => key !== id);
      set({ feedback: Object.fromEntries([...entries, [id, feedback]].slice(-HISTORY_LIMIT)) });
    },
    load: (fetcher = fetch) => {
      if (loading) return loading;
      loading = (async () => {
        try {
          for (;;) {
            const after = get().cursor;
            const response = await fetcher(`/api/cadlink/receipts?after=${after}&limit=50`);
            if (!response.ok) throw new Error(`Send receipt feed failed (${response.status})`);
            const page = await response.json() as ReceiptPage;
            if (typeof page.feed_id !== 'string' || !page.feed_id
              || !Array.isArray(page.receipts) || !Number.isSafeInteger(page.cursor) || page.cursor < 0
              || typeof page.has_more !== 'boolean') throw new Error('Invalid Send receipt page');
            if (get().feedId && get().feedId !== page.feed_id) {
              set(initial(page.feed_id));
              continue; // That page used the previous database's cursor. Read this feed's cursor instead.
            }
            if (page.cursor < after || (page.has_more && page.cursor <= after)) throw new Error('Invalid Send receipt page');
            const valid: CadReceipt[] = [];
            page.receipts.forEach((item, index) => {
              if (isReceipt(item) && item.cursor <= page.cursor) valid.push(item);
              else get().report(`invalid:${page.feed_id}:${page.cursor}:${index}`, {
                outcome: 'display_failed', message: 'WG skipped an invalid Send receipt in the feed.' });
            });
            get().merge({ ...page, receipts: valid.sort((a, b) => a.cursor - b.cursor) });
            if (!page.has_more) return;
          }
        } catch (error) {
          set({ recoveryError: error instanceof Error ? error.message : String(error) });
          throw error;
        }
      })().finally(() => { loading = null; });
      return loading;
    },
  }));
  store.subscribe((state) => {
    try {
      storage?.setItem(`${KEY}:${state.feedId ?? 'pending'}`, JSON.stringify({ feedId: state.feedId,
        cursor: state.cursor, since: state.since, receipts: state.receipts, fronted: state.fronted, feedback: state.feedback,
        newestSequence: state.newestSequence, newestSentAt: state.newestSentAt,
        intakeRefusals: state.intakeRefusals, displayRetries: state.displayRetries }));
      if (state.feedId) storage?.setItem(`${KEY}:active`, state.feedId);
    } catch { /* In-memory dedupe still works when persistence is unavailable. */ }
  });
  return store;
}
export const cadReceiptsStore = createCadReceiptsStore();
export function useCadReceiptsStore<T>(selector: (state: CadReceiptsState) => T): T {
  return useStore(cadReceiptsStore, selector);
}

/** Reuse existing jobs-channel operation/refusal pushes as invalidations.
 * HTTP remains authoritative, so pushes never advance this feed's cursor.
 * Shell mounts this source.
 */
export function connectCadReceipts(manager: Pick<JobsSocketManager, 'subscribeCadOperations'> = jobsSocket,
  store = cadReceiptsStore, fetcher: typeof fetch = fetch): () => void {
  let active = true;
  let reading = false;
  let again = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const read = (attempt = 0) => {
    if (!active) return;
    if (reading) { again = true; return; }
    if (timer !== null) clearTimeout(timer);
    timer = null;
    reading = true;
    void store.getState().load(fetcher).catch(() => {
      if (active && RETRY_MS[attempt] !== undefined) timer = setTimeout(() => read(attempt + 1), RETRY_MS[attempt]);
    }).finally(() => {
      reading = false;
      if (active && again) { again = false; read(); }
    });
  };
  const unsubscribe = manager.subscribeCadOperations({
    operation: (operation) => { if (operation.kind === 'receive_snapshot') read(); },
    refusal: (refusal) => { useCadDeliveryHealthStore.getState().mergeRefusals([refusal]); read(); },
    resync: () => read(),
  });
  read();
  return () => { active = false; unsubscribe(); if (timer !== null) clearTimeout(timer); };
}

/** Drain each batch once. Transport retries have bounded backoff and do not
 * stop later Sends. Completed receipts are removed from the pending queue.
 */
export function connectCadReceiptDisplay(display: (receipt: CadReceipt) => Promise<boolean>,
  store = cadReceiptsStore): () => void {
  let active = true;
  let draining = false;
  let again = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const drain = async () => {
    if (!active) return;
    if (draining) { again = true; return; }
    draining = true;
    if (timer !== null) clearTimeout(timer);
    timer = null;
    useCadDeliveryHealthStore.getState().mergeRefusals(store.getState().intakeRefusals);
    try {
      const feedId = store.getState().feedId;
      const batch = Object.values(store.getState().receipts).sort((a, b) => a.cursor - b.cursor);
      for (const next of batch) {
        const state = store.getState();
        if (!active || state.feedId !== feedId) break;
        if (!state.receipts[next.receipt_id] || state.fronted.includes(next.receipt_id)
          || (state.displayRetries[next.receipt_id]?.nextAt ?? 0) > Date.now()) continue;
        let completed = true;
        if (!next.refusal) {
          try { completed = await display(next); }
          catch (error) {
            // The display action classifies API/transport errors. An unexpected
            // exception is a completed failure, never a poison queue entry.
            store.getState().report(next.receipt_id, { outcome: 'display_failed',
              message: error instanceof Error ? error.message : String(error) });
          }
        }
        if (active && store.getState().feedId === feedId) {
          if (completed) {
            if (store.getState().receipts[next.receipt_id]) store.getState().markFronted(next.receipt_id, next);
          }
          else store.getState().retryDisplay(next.receipt_id);
        }
      }
    } finally {
      draining = false;
      if (active && again) { again = false; void drain(); }
      else if (active) {
        const due = Object.values(store.getState().displayRetries).map((retry) => retry.nextAt);
        if (due.length) timer = setTimeout(() => { void drain(); }, Math.max(0, Math.min(...due) - Date.now()));
      }
    }
  };
  const unsubscribe = store.subscribe(() => { void drain(); });
  void drain();
  return () => { active = false; unsubscribe(); if (timer !== null) clearTimeout(timer); };
}

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
interface ReceiptPage { receipts: CadReceipt[]; cursor: number; has_more: boolean }
export interface ReceiptFeedback { outcome: ReceiptOutcome | 'display_failed' | 'superseded'; message: string }
export interface CadReceiptsState {
  cursor: number;
  since: number;
  receipts: Record<string, CadReceipt>;
  fronted: string[];
  feedback: Record<string, ReceiptFeedback>;
  recoveryError: string | null;
  merge(page: ReceiptPage): void;
  markFronted(id: string): void;
  report(id: string, feedback: ReceiptFeedback): void;
  load(fetcher?: typeof fetch): Promise<void>;
}
export type CadReceiptsStore = StoreApi<CadReceiptsState>;
const KEY = 'wg2.cad.receipts.v1';
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
    && (item.refusal === null || (typeof item.refusal?.at === 'string'
      && typeof item.refusal.file === 'string' && typeof item.refusal.reason === 'string'));
}

/** A factory makes window/reload boundaries explicit without mounting the coordinator.
 * Cursor and pending receipts are persisted together: a fetched but not yet
 * displayed receipt must survive a reload too. Fronted state is per window.
 */
export function createCadReceiptsStore(storage = windowStorage(), now = Date.now): CadReceiptsStore {
  let restored: Partial<CadReceiptsState> = {};
  try {
    const saved = JSON.parse(storage?.getItem(KEY) ?? 'null') as CadReceiptsState | null;
    if (saved && Number.isSafeInteger(saved.cursor) && saved.cursor >= 0
      && Number.isFinite(saved.since) && Array.isArray(saved.fronted)
      && saved.fronted.every((id) => typeof id === 'string')
      && saved.receipts && Object.values(saved.receipts).every(isReceipt)) restored = saved;
  } catch { /* Start a fresh window when storage is unavailable or invalid. */ }
  let loading: Promise<void> | null = null;
  const store = createStore<CadReceiptsState>((set, get) => ({
    cursor: restored.cursor ?? 0, since: restored.since ?? now() - RECEIPT_FIRST_CONNECTION_MS,
    receipts: restored.receipts ?? {}, fronted: restored.fronted ?? [],
    feedback: restored.feedback ?? {}, recoveryError: null,
    merge: (page) => {
      const receipts = { ...get().receipts };
      page.receipts.forEach((item) => {
        // A fresh window keeps the documented ten-minute discovery boundary.
        // Intake refusals use health's bounded log even for a late page.
        if (item.refusal || Date.parse(item.created_at) >= get().since) receipts[item.receipt_id] = item;
      });
      set({ receipts, cursor: Math.max(get().cursor, page.cursor), recoveryError: null });
    },
    markFronted: (id) => {
      if (!get().fronted.includes(id)) set({ fronted: [...get().fronted, id] });
    },
    report: (id, feedback) => set({ feedback: { ...get().feedback, [id]: feedback } }),
    load: (fetcher = fetch) => {
      if (loading) return loading;
      loading = (async () => {
        try {
          for (;;) {
            const after = get().cursor;
            const response = await fetcher(`/api/cadlink/receipts?after=${after}&limit=50`);
            if (!response.ok) throw new Error(`Send receipt feed failed (${response.status})`);
            const page = await response.json() as ReceiptPage;
            if (!Array.isArray(page.receipts) || !page.receipts.every(isReceipt)
              || !Number.isSafeInteger(page.cursor) || page.cursor < after
              || typeof page.has_more !== 'boolean'
              || page.receipts.some((item, i) => item.cursor > page.cursor
                || (i > 0 && item.cursor < page.receipts[i - 1].cursor))
              || (page.has_more && page.cursor <= after)) throw new Error('Invalid Send receipt page');
            get().merge(page);
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
      storage?.setItem(KEY, JSON.stringify({ cursor: state.cursor, since: state.since,
        receipts: state.receipts, fronted: state.fronted, feedback: state.feedback }));
    } catch { /* In-memory dedupe still works for a window that cannot persist. */ }
  });
  return store;
}
export const cadReceiptsStore = createCadReceiptsStore();
export function useCadReceiptsStore<T>(selector: (state: CadReceiptsState) => T): T {
  return useStore(cadReceiptsStore, selector);
}

/** Reuse existing jobs-channel operation/refusal pushes as invalidations.
 * HTTP remains authoritative, so pushes never advance this feed's cursor.
 * S5-7 mounts this source from Shell.
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

/** Drain in receipt order; only completed display/report actions are fronted.
 * A transient failure remains pending for the next reconnect or explicit retry.
 */
export function connectCadReceiptDisplay(display: (receipt: CadReceipt) => Promise<boolean>,
  store = cadReceiptsStore): () => void {
  let active = true;
  let draining = false;
  const drain = async () => {
    if (!active || draining) return;
    draining = true;
    // Rehydrate the existing health owner even for receipts processed before
    // reload. Fronting a receipt does not remove a refusal from the panel.
    const refusals = Object.values(store.getState().receipts)
      .sort((a, b) => b.cursor - a.cursor).flatMap((item) => item.refusal ? [item.refusal] : []).slice(0, 20);
    useCadDeliveryHealthStore.getState().mergeRefusals(refusals);
    try {
      while (active) {
        const state = store.getState();
        const next = Object.values(state.receipts).sort((a, b) => a.cursor - b.cursor)
          .find((item) => !state.fronted.includes(item.receipt_id));
        if (!next) break;
        if (!next.refusal) {
          try { if (!await display(next)) break; }
          catch (error) {
            store.getState().report(next.receipt_id, { outcome: 'display_failed',
              message: error instanceof Error ? error.message : String(error) });
            break;
          }
        }
        if (active) store.getState().markFronted(next.receipt_id);
      }
    } finally { draining = false; }
  };
  const unsubscribe = store.subscribe(() => { void drain(); });
  void drain();
  return () => { active = false; unsubscribe(); };
}

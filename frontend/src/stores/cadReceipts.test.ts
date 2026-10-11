import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadOperationListener } from '../api/jobsSocket';
import type { CadOperationSummary } from '../api/cadOperations';
import { connectCadReceiptDisplay, connectCadReceipts, createCadReceiptsStore, type CadReceipt } from './cadReceipts';
import { resetCadDeliveryHealthStore, useCadDeliveryHealthStore } from './cadDeliveryHealth';

const NOW = Date.parse('2026-10-11T12:00:00Z');
const receipt = (cursor: number, overrides: Partial<CadReceipt> = {}): CadReceipt => ({
  receipt_id: `send:${cursor}`, send_id: `send-${cursor}`, send_sequence: cursor, project_id: null, design_ids: [], ingest_id: null,
  outcome: 'accepted', reason: null, message: null, created_at: new Date(NOW).toISOString(),
  sent_at: new Date(NOW).toISOString(), cursor, bundle_path: 'wgreturn/speaker.wgreturn',
  manifest_sha256: 'sha256:m', document_name: 'Speaker', retained_copy: true, refusal: null, ...overrides,
});
const answer = (receipts: CadReceipt[], cursor = receipts.at(-1)?.cursor ?? 0, has_more = false) =>
  new Response(JSON.stringify({ receipts, cursor, has_more }));
const flush = async () => { await vi.advanceTimersByTimeAsync(0); };

function channel() {
  let listener: CadOperationListener;
  const unsubscribe = vi.fn();
  const manager = { subscribeCadOperations: (next: CadOperationListener) => { listener = next; return unsubscribe; } };
  return { manager, get listener() { return listener; }, unsubscribe };
}

describe('Send receipt cursor source, without a coordinator', () => {
  beforeEach(() => { vi.useFakeTimers(); sessionStorage.clear(); resetCadDeliveryHealthStore(); });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it('walks every page beyond 50 and deduplicates overlapping pages', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const rows = Array.from({ length: 123 }, (_, i) => receipt(i + 1));
    const calls: number[] = [];
    const fetcher = vi.fn(async (url: RequestInfo | URL) => {
      const after = Number(new URL(String(url), 'http://test').searchParams.get('after'));
      calls.push(after);
      const end = Math.min(123, after + 50);
      return answer(rows.slice(after ? after - 1 : 0, end), end, end < 123);
    });
    await store.getState().load(fetcher);
    expect(calls).toEqual([0, 50, 100]);
    expect(Object.keys(store.getState().receipts)).toHaveLength(123);
    expect(store.getState().cursor).toBe(123);
  });

  it('never advances past a failed page and resumes there after a reload', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const fetcher = vi.fn().mockResolvedValueOnce(answer(Array.from({ length: 50 }, (_, i) => receipt(i + 1)), 50, true))
      .mockResolvedValueOnce(new Response('{}', { status: 503 }));
    await expect(store.getState().load(fetcher)).rejects.toThrow('503');
    expect(store.getState().cursor).toBe(50);
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW + 3600_000);
    const retry = vi.fn().mockResolvedValue(answer([receipt(51)]));
    await reloaded.getState().load(retry);
    expect(retry).toHaveBeenCalledWith('/api/cadlink/receipts?after=50&limit=50');
    expect(Object.keys(reloaded.getState().receipts)).toHaveLength(51);
    expect(reloaded.getState().recoveryError).toBeNull();
  });

  it('persists pending receipts together with the cursor until they are fronted', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    await store.getState().load(vi.fn().mockResolvedValue(answer([receipt(1), receipt(2)])));
    store.getState().markFronted('send:1');
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = vi.fn(async (_receipt: CadReceipt) => true);
    const stop = connectCadReceiptDisplay(display, reloaded);
    await flush();
    expect(display).toHaveBeenCalledTimes(1);
    expect(display.mock.calls[0]).toEqual([receipt(2)]);
    expect(reloaded.getState().fronted).toEqual(['send:1', 'send:2']);
    stop();
  });

  it('does not forget fronted ids after more than 100 receipts or a reload', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const rows = Array.from({ length: 123 }, (_, i) => receipt(i + 1));
    store.getState().merge({ receipts: rows, cursor: 123, has_more: false });
    rows.forEach((item) => store.getState().markFronted(item.receipt_id));
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = vi.fn(async (_receipt: CadReceipt) => true);
    const stop = connectCadReceiptDisplay(display, reloaded);
    await flush();
    expect(display).not.toHaveBeenCalled();
    stop();
  });

  it('keeps the ten-minute first-connection boundary, then discovers a long outage', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    await store.getState().load(vi.fn().mockResolvedValue(answer([
      receipt(1, { created_at: new Date(NOW - 600_001).toISOString() }),
      receipt(2, { created_at: new Date(NOW - 600_000).toISOString() }),
    ])));
    expect(Object.keys(store.getState().receipts)).toEqual(['send:2']);
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW + 24 * 3600_000);
    await reloaded.getState().load(vi.fn().mockResolvedValue(answer([receipt(3, { created_at: new Date(NOW + 3600_000).toISOString() })])));
    expect(Object.keys(reloaded.getState().receipts)).toEqual(['send:2', 'send:3']);
  });

  it('rejects malformed or non-advancing pages without moving the cursor', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    await expect(store.getState().load(vi.fn().mockResolvedValue(answer([receipt(1)], 0, true))))
      .rejects.toThrow('Invalid Send receipt page');
    expect(store.getState().cursor).toBe(0);
    expect(store.getState().receipts).toEqual({});
  });

  it('coalesces reconnects and pushes while reading and resyncs from the HTTP cursor', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const socket = channel();
    let resolve!: (value: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done; }))
      .mockResolvedValueOnce(answer([receipt(2)]))
      .mockResolvedValueOnce(answer([], 2));
    const stop = connectCadReceipts(socket.manager, store, fetcher);
    socket.listener.resync();
    socket.listener.operation({ kind: 'receive_snapshot' } as CadOperationSummary);
    resolve(answer([receipt(1)]));
    await flush();
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/api/cadlink/receipts?after=0&limit=50', '/api/cadlink/receipts?after=1&limit=50',
    ]);
    socket.listener.resync();
    await flush();
    expect(fetcher).toHaveBeenLastCalledWith('/api/cadlink/receipts?after=2&limit=50');
    stop();
    expect(socket.unsubscribe).toHaveBeenCalledOnce();
  });

  it('keeps late intake refusals in delivery health and counts a push/reconnect once', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const socket = channel();
    const refusal = { at: new Date(NOW - 3600_000).toISOString(), file: 'bad.json', operationId: null, reason: 'bad request' };
    const item = receipt(1, { receipt_id: 'intake:bad', outcome: 'refused', refusal, created_at: refusal.at });
    const stopDisplay = connectCadReceiptDisplay(vi.fn(async () => true), store);
    const fetcher = vi.fn().mockResolvedValue(answer([item]));
    const stop = connectCadReceipts(socket.manager, store, fetcher);
    socket.listener.refusal!(refusal);
    await flush();
    expect(useCadDeliveryHealthStore.getState().unseenRefusals).toBe(1);
    useCadDeliveryHealthStore.getState().acknowledgeRefusals();
    socket.listener.resync();
    await flush();
    expect(useCadDeliveryHealthStore.getState().unseenRefusals).toBe(0);
    expect(useCadDeliveryHealthStore.getState().refusals).toEqual([refusal]);
    stop(); stopDisplay();
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW);
    const stopReload = connectCadReceiptDisplay(vi.fn(async () => true), reloaded);
    await flush();
    expect(useCadDeliveryHealthStore.getState().unseenRefusals).toBe(0);
    stopReload();
  });

  it('restores a processed intake refusal to health after a reload and backend restart', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const refusal = { at: new Date(NOW).toISOString(), file: 'bad.json', operationId: null, reason: 'bad request' };
    store.getState().merge({ receipts: [receipt(1, { receipt_id: 'intake:bad', outcome: 'refused', refusal })], cursor: 1, has_more: false });
    store.getState().markFronted('intake:bad');
    resetCadDeliveryHealthStore();
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = vi.fn(async () => true);
    const stop = connectCadReceiptDisplay(display, reloaded);
    await flush();
    expect(useCadDeliveryHealthStore.getState().refusals).toEqual([refusal]);
    expect(display).not.toHaveBeenCalled();
    stop();
  });

  it('retries a failed feed, then stops only its own retry timer on disconnect', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const socket = channel();
    const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 503 }));
    const stop = connectCadReceipts(socket.manager, store, fetcher);
    await flush();
    expect(store.getState().recoveryError).toContain('503');
    await vi.advanceTimersByTimeAsync(5_000);
    expect(fetcher).toHaveBeenCalledTimes(2);
    stop();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('keeps a failed display pending and tries again when recovery merges a page', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const stop = connectCadReceiptDisplay(display, store);
    store.getState().merge({ receipts: [receipt(1)], cursor: 1, has_more: false });
    await flush();
    expect(store.getState().fronted).toEqual([]);
    store.getState().merge({ receipts: [], cursor: 1, has_more: false });
    await flush();
    expect(store.getState().fronted).toEqual(['send:1']);
    expect(display).toHaveBeenCalledTimes(2);
    stop();
  });
});

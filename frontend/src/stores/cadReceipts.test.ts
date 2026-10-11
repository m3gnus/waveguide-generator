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
  new Response(JSON.stringify({ feed_id: 'feed-a', receipts, cursor, has_more }));
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
    expect(retry).toHaveBeenCalledWith('/api/cadlink/receipts?after=50&limit=50', { signal: expect.any(AbortSignal) });
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
    expect(reloaded.getState().receipts).toEqual({});
    stop();
  });

  it('does not forget fronted ids after more than 100 receipts or a reload', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const rows = Array.from({ length: 123 }, (_, i) => receipt(i + 1));
    store.getState().merge({ feed_id: 'feed-a', receipts: rows, cursor: 123, has_more: false });
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

  it('persists fronted source identities after removing their pending receipts (row 1)', () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const item = receipt(1);
    store.getState().merge({ feed_id: 'feed-a', receipts: [item], cursor: 1, has_more: false });
    store.getState().markFronted(item.receipt_id);
    expect(store.getState().receipts).toEqual({});
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW);
    expect(reloaded.getState().frontedBundles).toEqual([{ path: item.bundle_path, manifest: item.manifest_sha256 }]);
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
    expect(fetcher).toHaveBeenLastCalledWith('/api/cadlink/receipts?after=2&limit=50', { signal: expect.any(AbortSignal) });
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
    store.getState().merge({ feed_id: 'feed-a', receipts: [receipt(1, { receipt_id: 'intake:bad', outcome: 'refused', refusal })], cursor: 1, has_more: false });
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

  it('keeps a failed display pending until its retry backoff ends', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const stop = connectCadReceiptDisplay(display, store);
    store.getState().merge({ feed_id: 'feed-a', receipts: [receipt(1)], cursor: 1, has_more: false });
    await flush();
    expect(store.getState().fronted).toEqual([]);
    store.getState().merge({ feed_id: 'feed-a', receipts: [], cursor: 1, has_more: false });
    await flush();
    expect(store.getState().receipts['send:1']).toBeDefined();
    expect(display).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(display).toHaveBeenCalledTimes(2);
    stop();
  });
});

describe('receipt review regressions', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); sessionStorage.clear(); resetCadDeliveryHealthStore(); });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it.each(['fetch', 'body'])('bounds a hung receipt %s read, permits retry and fences its late page', async (phase) => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    let release!: () => void;
    const late = answer([receipt(99)]);
    const hung = phase === 'fetch'
      ? new Promise<Response>((resolve) => { release = () => resolve(late); })
      : Promise.resolve({ ok: true, json: () => new Promise((resolve) => {
          release = () => resolve({ feed_id: 'feed-a', receipts: [receipt(99)], cursor: 99, has_more: false });
        }) } as Response);
    const fetcher = vi.fn<typeof fetch>().mockImplementationOnce(() => hung).mockResolvedValueOnce(answer([receipt(1)]));
    const read = store.getState().load(fetcher);
    expect(store.getState().load(fetcher)).toBe(read);
    const outcome = read.catch((error: Error) => error);
    await flush(); await vi.advanceTimersByTimeAsync(3_000);
    expect(await Promise.race([outcome, Promise.resolve('still waiting')])).toBeInstanceOf(Error);
    expect(store.getState().recoveryError).toMatch(/timed out/i);
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    await store.getState().load(fetcher);
    expect(store.getState().cursor).toBe(1);
    expect(store.getState().recoveryError).toBeNull();
    release(); await flush();
    expect(store.getState().cursor).toBe(1);
    expect(Object.keys(store.getState().receipts)).toEqual(['send:1']);
    expect(store.getState().recoveryError).toBeNull();
  });

  it('does not let a transient receipt block later Sends and bounds retries across pushes and reloads', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = vi.fn(async (item: CadReceipt) => item.cursor !== 1);
    let stop = connectCadReceiptDisplay(display, store);
    store.getState().merge({ feed_id: 'feed-a', receipts: [receipt(1), receipt(2)], cursor: 2, has_more: false });
    await flush();
    expect(display.mock.calls.map(([item]) => item.cursor)).toEqual([1, 2]);
    for (let i = 0; i < 5; ++i) store.getState().merge({ feed_id: 'feed-a', receipts: [], cursor: 2, has_more: false });
    await flush();
    expect(display).toHaveBeenCalledTimes(2);
    stop();
    const reload = createCadReceiptsStore(sessionStorage, () => NOW);
    stop = connectCadReceiptDisplay(display, reload);
    await flush();
    expect(display).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(5_000 + 30_000 + 120_000);
    expect(display.mock.calls.filter(([item]) => item.cursor === 1)).toHaveLength(4);
    expect(reload.getState().receipts).toEqual({});
    expect(reload.getState().feedback['send:1'].message).toContain('repeated transport failures');
    reload.getState().merge({ feed_id: 'feed-a', receipts: [receipt(1), receipt(2)], cursor: 2, has_more: false });
    await flush();
    expect(display).toHaveBeenCalledTimes(5);
    stop();
  });

  it('scopes saved cursors to the server feed and resets ordering when the database changes', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    store.getState().merge({ feed_id: 'feed-a', receipts: [receipt(100)], cursor: 100, has_more: false });
    store.getState().markFronted('send:100');
    const reload = createCadReceiptsStore(sessionStorage, () => NOW);
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ feed_id: 'feed-b', receipts: [], cursor: 100, has_more: false })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ feed_id: 'feed-b', receipts: [receipt(1)], cursor: 1, has_more: false })));
    await reload.getState().load(fetcher);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/api/cadlink/receipts?after=100&limit=50', '/api/cadlink/receipts?after=0&limit=50',
    ]);
    expect(reload.getState().feedId).toBe('feed-b');
    expect(reload.getState().cursor).toBe(1);
    expect(reload.getState().newestSequence).toBe(0);
    expect(Object.keys(reload.getState().receipts)).toEqual(['send:1']);
    const keys = Object.keys(sessionStorage);
    expect(keys).toContain('wg2.cad.receipts.v2:feed-a');
    expect(keys).toContain('wg2.cad.receipts.v2:feed-b');
  });

  it('prunes completed rows and bounds history under a storage quota, keeping shown-once after reload', async () => {
    let saved = new Map<string, string>();
    const storage = {
      getItem: (key: string) => saved.get(key) ?? null,
      setItem: (key: string, value: string) => {
        if (value.length > 20_000) throw new Error('quota exceeded');
        saved.set(key, value);
      },
    } as Storage;
    const store = createCadReceiptsStore(storage, () => NOW);
    const display = vi.fn(async () => true);
    const stop = connectCadReceiptDisplay(display, store);
    for (let i = 1; i <= 300; ++i) {
      store.getState().merge({ feed_id: 'feed-a', receipts: [receipt(i)], cursor: i, has_more: false });
      store.getState().report(`send:${i}`, { outcome: 'accepted', message: 'Received' });
      await flush();
    }
    stop();
    expect(store.getState().receipts).toEqual({});
    expect(store.getState().fronted).toEqual([]);
    expect(Object.keys(store.getState().feedback)).toHaveLength(20);
    const reload = createCadReceiptsStore(storage, () => NOW);
    expect(reload.getState().cursor).toBe(300);
    reload.getState().merge({ feed_id: 'feed-a', receipts: [receipt(299), receipt(300)], cursor: 300, has_more: false });
    const reloadStop = connectCadReceiptDisplay(display, reload);
    await flush();
    expect(display).toHaveBeenCalledTimes(300);
    reloadStop();
  });

  it('skips and reports malformed rows, including a whole bad page, while advancing the server cursor', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ feed_id: 'feed-a', receipts: [null, { ...receipt(2), design_ids: 2 }], cursor: 2, has_more: true })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ feed_id: 'feed-a', receipts: [{ ...receipt(3), cursor: 500 }, receipt(4)], cursor: 4, has_more: false })));
    await store.getState().load(fetcher);
    expect(store.getState().cursor).toBe(4);
    expect(Object.keys(store.getState().receipts)).toEqual(['send:4']);
    expect(Object.values(store.getState().feedback)).toHaveLength(3);
    expect(store.getState().recoveryError).toBeNull();
    expect(fetcher.mock.calls[1]).toEqual(['/api/cadlink/receipts?after=2&limit=50', { signal: expect.any(AbortSignal) }]);
  });
});

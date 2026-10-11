import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createCadRecoveryNoticesStore, connectCadRecoveryNotices, type CadRecoveryNotice } from './cadRecoveryNotices';
import { useCadDeliveryHealthStore } from './cadDeliveryHealth';
import type { JobsSocketManager } from '../api/jobsSocket';

const notice = (overrides: Partial<CadRecoveryNotice> = {}): CadRecoveryNotice => ({
  id: 'mutation', kind: 'update_link', document_id: 'fusion:active', document_name: 'Document',
  created_at: '2026-10-01T10:00:00Z', sequence: 1, active_document: true, ...overrides,
});
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const deferred = <T>() => { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

describe('recovery notices without the coordinator', () => {
  beforeEach(() => useCadDeliveryHealthStore.setState({ recoveryError: null }));
  it('restores notices on reload and evidence alone removes them', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => json({ notices: [notice()] }));
    const first = createCadRecoveryNoticesStore();
    await first.getState().load(fetcher);
    const reload = createCadRecoveryNoticesStore();
    await reload.getState().load(fetcher);
    expect(reload.getState().notices).toEqual(first.getState().notices);
    fetcher.mockResolvedValueOnce(json({ notices: [] }));
    await reload.getState().load(fetcher);
    expect(reload.getState().notices).toEqual({});
    expect(fetcher.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });
  it('dismisses once, fences a stale binding/listing, and cannot revive after reload', async () => {
    const store = createCadRecoveryNoticesStore();
    const response = deferred<Response>();
    let hidden = false;
    const fetcher = vi.fn<typeof fetch>(async (_url, init) => {
      if (init?.method === 'POST') { hidden = true; return json({ id: 'mutation', dismissed: true, repaired: false }); }
      return json({ notices: hidden ? [] : [notice()] });
    });
    await store.getState().load(fetcher);
    const listing = store.getState().load(() => response.promise);
    const a = store.getState().dismiss('mutation', fetcher);
    const b = store.getState().dismiss('mutation', fetcher);
    expect(a).toBe(b);
    await a;
    response.resolve(json({ notices: [notice()] }));
    await listing;
    await store.getState().dismiss('mutation', fetcher);
    expect(store.getState().notices).toEqual({});
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    const reload = createCadRecoveryNoticesStore();
    await reload.getState().load(fetcher);
    expect(reload.getState().notices).toEqual({});
  });
  it('keeps unresolved notices and owns delivery-health recoveryError until a successful retry', async () => {
    const store = createCadRecoveryNoticesStore();
    await store.getState().load(async () => json({ notices: [notice()] }));
    await expect(store.getState().load(async () => { throw new Error('disconnected'); })).rejects.toThrow();
    expect(store.getState().notices.mutation).toBeDefined();
    expect(store.getState().recoveryError).toContain('disconnected');
    expect(useCadDeliveryHealthStore.getState().recoveryError).toBe(store.getState().recoveryError);
    await store.getState().load(async () => json({ notices: [notice()] }));
    expect(useCadDeliveryHealthStore.getState().recoveryError).toBeNull();
  });
  it('refuses an older load success or failure after a newer read', async () => {
    const store = createCadRecoveryNoticesStore();
    const old = deferred<Response>();
    const read = store.getState().load(() => old.promise);
    await store.getState().load(async () => json({ notices: [] }));
    old.resolve(json({ notices: [notice()] }));
    await read;
    expect(store.getState().notices).toEqual({});
    const failed = deferred<Response>();
    const failure = store.getState().load(() => failed.promise);
    await store.getState().load(async () => json({ notices: [notice()] }));
    failed.reject(new Error('old failure'));
    await failure;
    expect(store.getState().recoveryError).toBeNull();
  });
  it('a failed dismissal stays visible and retryable', async () => {
    const store = createCadRecoveryNoticesStore();
    await store.getState().load(async () => json({ notices: [notice()] }));
    await expect(store.getState().dismiss('mutation', async () => { throw new Error('offline'); })).rejects.toThrow('offline');
    expect(store.getState().notices.mutation).toBeDefined();
    await store.getState().dismiss('mutation', async () => json({ dismissed: true }));
    expect(store.getState().notices).toEqual({});
  });
  it('coalesces channel wakeups and reconnects, then unsubscribes', async () => {
    let handlers!: Parameters<JobsSocketManager['subscribeCadOperations']>[0];
    const unsubscribe = vi.fn();
    const manager = { subscribeCadOperations: vi.fn((value) => { handlers = value; return unsubscribe; }) } as Pick<JobsSocketManager, 'subscribeCadOperations'>;
    const store = createCadRecoveryNoticesStore();
    const response = deferred<Response>();
    const fetcher = vi.fn<typeof fetch>().mockImplementationOnce(() => response.promise).mockImplementation(async () => json({ notices: [] }));
    const stop = connectCadRecoveryNotices(manager, store, fetcher);
    handlers.resync?.(); handlers.resync?.();
    expect(fetcher).toHaveBeenCalledOnce();
    response.resolve(json({ notices: [notice()] }));
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    stop(); handlers.resync?.();
    expect(unsubscribe).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
